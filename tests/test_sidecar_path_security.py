"""Tests for sidecar path security contracts.

Validates realpath/normcase/commonpath containment enforcement:
- sidecar source field traversal
- sidecar_ref physical path validation
- same basename in different directories fails reconciliation
- symlink escaping root (where symlinks are supported)
- bundle output/sidecar/source symlink outside root
"""
import os
import sys
import json
import pytest
from unittest.mock import MagicMock
from cida.domain.errors import SidecarValidationError
from cida.domain.sidecar import (
    create_compressed_envelope,
    validate_sidecar_ref,
    reconcile_envelope_and_sidecar,
)
from cida.application.validate_sidecar import SidecarValidatorUsecase, validate_sidecar_ref_physical
from cida.infrastructure.hashing import HashService
from cida.infrastructure.filesystem import PhysicalFilesystem
from cida.infrastructure.json_codec import JsonCodec


# ── validate_sidecar_ref lexical checks ─────────────────────────────────────

class TestValidateSidecarRefLexical:
    def test_rejects_absolute_unix_path(self):
        with pytest.raises(SidecarValidationError):
            validate_sidecar_ref("/etc/passwd")

    def test_rejects_absolute_windows_path(self):
        with pytest.raises(SidecarValidationError):
            validate_sidecar_ref("C:\\Windows\\System32\\file.cidatkn")

    def test_rejects_dotdot_traversal(self):
        with pytest.raises(SidecarValidationError):
            validate_sidecar_ref("../other/file.cidatkn")

    def test_rejects_backslash_separator(self):
        with pytest.raises(SidecarValidationError):
            validate_sidecar_ref("subdir\\file.cidatkn")

    def test_accepts_simple_filename(self):
        result = validate_sidecar_ref("file.cidatkn")
        assert result == "file.cidatkn"

    def test_accepts_subdir_slash(self):
        """sidecar_ref can have forward-slash subdir."""
        result = validate_sidecar_ref("subdir/file.cidatkn")
        assert result == "subdir/file.cidatkn"


# ── validate_sidecar_ref_physical ────────────────────────────────────────────

class TestValidateSidecarRefPhysical:
    def test_valid_sibling_sidecar(self, tmp_path):
        output_root = tmp_path / "output"
        output_root.mkdir()
        compressed = output_root / "doc.md"
        compressed.write_bytes(b"content")
        sidecar_ref = "doc.md.cidatkn"

        result = validate_sidecar_ref_physical(str(compressed), sidecar_ref, str(output_root))
        assert result == sidecar_ref

    def test_rejects_traversal_via_dotdot(self, tmp_path):
        output_root = tmp_path / "output"
        output_root.mkdir()
        compressed = output_root / "doc.md"
        compressed.write_bytes(b"content")

        with pytest.raises(SidecarValidationError) as exc_info:
            validate_sidecar_ref_physical(str(compressed), "../outside.cidatkn", str(output_root))

        assert exc_info.value.exit_code == 5
        assert "traversal" in str(exc_info.value).lower() or "outside" in str(exc_info.value).lower()

    def test_rejects_absolute_ref(self, tmp_path):
        output_root = tmp_path / "output"
        output_root.mkdir()
        compressed = output_root / "doc.md"
        compressed.write_bytes(b"content")

        with pytest.raises(SidecarValidationError):
            validate_sidecar_ref_physical(str(compressed), "/etc/sidecar.cidatkn", str(output_root))

    @pytest.mark.skipif(sys.platform == "win32", reason="symlinks require elevation on Windows")
    def test_rejects_symlink_escaping_root(self, tmp_path):
        output_root = tmp_path / "output"
        output_root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        outside_file = outside / "secret.cidatkn"
        outside_file.write_bytes(b"{}")

        # Create symlink inside output_root pointing outside
        link = output_root / "evil.cidatkn"
        link.symlink_to(outside_file)

        compressed = output_root / "doc.md"
        compressed.write_bytes(b"content")

        with pytest.raises(SidecarValidationError) as exc_info:
            validate_sidecar_ref_physical(str(compressed), "evil.cidatkn", str(output_root))

        assert exc_info.value.exit_code == 5


# ── reconcile_envelope_and_sidecar ───────────────────────────────────────────

class TestReconcileEnvelopeAndSidecar:
    def _make_envelope(self, sidecar_ref="doc.md.cidatkn", sha="a" * 64):
        return {
            "version": 1,
            "source_sha256": sha,
            "sidecar_ref": sidecar_ref,
        }

    def _make_sidecar(self, sha="a" * 64):
        return {
            "version": 1,
            "source_sha256": sha,
        }

    def test_same_basename_different_dir_fails_with_paths(self, tmp_path):
        """Same basename in different directories must fail when compressed_file provided."""
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()

        compressed = dir1 / "doc.md"
        compressed.write_bytes(b"compressed content")

        # Sidecar is in dir2, not dir1 — same basename but wrong location
        actual_sidecar = dir2 / "doc.md.cidatkn"
        actual_sidecar.write_bytes(b"{}")

        envelope = self._make_envelope(sidecar_ref="doc.md.cidatkn")
        sidecar_data = self._make_sidecar()

        # Resolve physical paths in application layer (as real callers do)
        parent_real = os.path.normcase(os.path.realpath(os.path.dirname(os.path.abspath(str(compressed)))))
        declared_ref = envelope.get("sidecar_ref", "")
        resolved_declared = os.path.normcase(os.path.realpath(os.path.join(parent_real, declared_ref)))
        resolved_actual = os.path.normcase(os.path.realpath(os.path.abspath(str(actual_sidecar))))

        with pytest.raises(SidecarValidationError) as exc_info:
            reconcile_envelope_and_sidecar(
                envelope,
                sidecar_data,
                actual_sidecar_filename=str(actual_sidecar),
                resolved_declared_ref=resolved_declared,
                resolved_actual_sidecar=resolved_actual,
            )

        assert exc_info.value.exit_code == 5
        assert "disagrees" in str(exc_info.value).lower() or "resolved" in str(exc_info.value).lower()

    def test_correct_sidecar_path_passes(self, tmp_path):
        """Correct sidecar next to compressed file passes."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        compressed = output_dir / "doc.md"
        compressed.write_bytes(b"compressed content")

        actual_sidecar = output_dir / "doc.md.cidatkn"
        actual_sidecar.write_bytes(b"{}")

        envelope = self._make_envelope(sidecar_ref="doc.md.cidatkn")
        sidecar_data = self._make_sidecar()

        # Resolve physical paths in application layer
        parent_real = os.path.normcase(os.path.realpath(os.path.dirname(os.path.abspath(str(compressed)))))
        declared_ref = envelope.get("sidecar_ref", "")
        resolved_declared = os.path.normcase(os.path.realpath(os.path.join(parent_real, declared_ref)))
        resolved_actual = os.path.normcase(os.path.realpath(os.path.abspath(str(actual_sidecar))))

        # Should not raise
        reconcile_envelope_and_sidecar(
            envelope,
            sidecar_data,
            actual_sidecar_filename=str(actual_sidecar),
            resolved_declared_ref=resolved_declared,
            resolved_actual_sidecar=resolved_actual,
        )

    def test_sha_mismatch_raises(self, tmp_path):
        envelope = self._make_envelope(sha="a" * 64)
        sidecar_data = self._make_sidecar(sha="b" * 64)

        with pytest.raises(SidecarValidationError) as exc_info:
            reconcile_envelope_and_sidecar(envelope, sidecar_data)

        assert exc_info.value.exit_code == 5

    @pytest.mark.skipif(sys.platform == "win32", reason="symlinks require elevation on Windows")
    def test_symlink_sidecar_pointing_outside_fails(self, tmp_path):
        """Sidecar file that is a symlink pointing outside the root fails."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        outside_sidecar = outside_dir / "secret.cidatkn"
        outside_sidecar.write_bytes(b"{}")

        compressed = output_dir / "doc.md"
        compressed.write_bytes(b"compressed")

        # Create symlink in output pointing to outside
        link = output_dir / "doc.md.cidatkn"
        link.symlink_to(outside_sidecar)

        envelope = self._make_envelope(sidecar_ref="doc.md.cidatkn")
        sidecar_data = self._make_sidecar()

        with pytest.raises(SidecarValidationError):
            reconcile_envelope_and_sidecar(
                envelope,
                sidecar_data,
                actual_sidecar_filename=str(link),
                compressed_file=str(compressed),
            )


# ── verify_destination_sidecars traversal guard ──────────────────────────────

class TestVerifyDestinationSidecarsTraversalGuard:
    def _make_sidecar_usecase(self, src_dir, sidecar_content):
        file_repo = MagicMock()
        json_codec = MagicMock()
        hash_service = MagicMock()
        hash_service.sha256.return_value = "a" * 64

        sidecar_path = str(src_dir / "output.cidatkn")
        file_repo.list_files.return_value = [sidecar_path]
        file_repo.read_text.return_value = json.dumps(sidecar_content)
        json_codec.decode.return_value = sidecar_content
        file_repo.is_file.return_value = False
        file_repo.abspath.side_effect = lambda p: os.path.abspath(p)
        file_repo.dirname.side_effect = lambda p: os.path.dirname(os.path.abspath(p))
        file_repo.basename.return_value = "output.cidatkn"
        file_repo.exists.return_value = True

        return SidecarValidatorUsecase(file_repo, json_codec, hash_service)

    def test_traversal_in_source_field_raises(self, tmp_path):
        """sidecar 'source' field with '..' must be rejected before any file read."""
        src = tmp_path / "src"
        src.mkdir()
        dst = tmp_path / "dst"
        dst.mkdir()

        sidecar_data = {
            "format": "cida-token-sidecar",
            "version": 1,
            "source": "../outside_file.md",
            "source_sha256": "a" * 64,
            "entries": {"A0": "hello"},
        }

        file_repo = MagicMock()
        json_codec = MagicMock()
        hash_service = MagicMock()

        sidecar_path = str(dst / "file.md.cidatkn")
        file_repo.list_files.return_value = [sidecar_path]
        file_repo.read_text.return_value = json.dumps(sidecar_data)
        json_codec.decode.return_value = sidecar_data
        file_repo.is_file.return_value = False
        file_repo.abspath.side_effect = os.path.abspath
        file_repo.dirname.side_effect = lambda p: os.path.dirname(os.path.abspath(p))
        file_repo.basename.return_value = "file.md.cidatkn"

        uc = SidecarValidatorUsecase(file_repo, json_codec, hash_service)

        with pytest.raises(SidecarValidationError) as exc_info:
            uc.verify_destination_sidecars(str(src), str(dst))

        assert exc_info.value.exit_code == 5
        assert exc_info.value.__class__.__name__ == "SidecarValidationError"
        # Ensure we never called read_bytes (no file was read after traversal detection)
        file_repo.read_bytes.assert_not_called()

    def test_absolute_source_field_raises_before_read(self, tmp_path):
        """Absolute sidecar 'source' values are rejected even when contained."""
        src = tmp_path / "src"
        src.mkdir()
        dst = tmp_path / "dst"
        dst.mkdir()
        source_file = src / "doc.md"
        source_file.write_bytes(b"# Hello")

        sidecar_data = {
            "format": "cida-token-sidecar",
            "version": 1,
            "source": str(source_file),
            "source_sha256": HashService().sha256(source_file.read_bytes()),
            "entries": {},
        }

        file_repo = MagicMock()
        json_codec = MagicMock()
        hash_service = MagicMock()

        sidecar_path = str(dst / "file.md.cidatkn")
        file_repo.list_files.return_value = [sidecar_path]
        file_repo.read_text.return_value = json.dumps(sidecar_data)
        json_codec.decode.return_value = sidecar_data
        file_repo.is_file.return_value = False
        file_repo.abspath.side_effect = os.path.abspath
        file_repo.dirname.side_effect = lambda p: os.path.dirname(os.path.abspath(p))
        file_repo.basename.return_value = "file.md.cidatkn"

        uc = SidecarValidatorUsecase(file_repo, json_codec, hash_service)

        with pytest.raises(SidecarValidationError) as exc_info:
            uc.verify_destination_sidecars(str(src), str(dst))

        assert exc_info.value.exit_code == 5
        assert "relative path" in str(exc_info.value)
        file_repo.read_bytes.assert_not_called()

    def test_valid_source_field_passes(self, tmp_path):
        """A well-formed 'source' field with matching SHA passes validation."""
        src = tmp_path / "src"
        src.mkdir()
        dst = tmp_path / "dst"
        dst.mkdir()

        source_content = b"# Hello world"
        src_file = src / "doc.md"
        src_file.write_bytes(source_content)

        sidecar_data = {
            "format": "cida-token-sidecar",
            "version": 1,
            "source": "doc.md",
            "source_sha256": HashService().sha256(source_content),
            "entries": {},
        }

        from cida.infrastructure.filesystem import PhysicalFilesystem
        from cida.infrastructure.json_codec import JsonCodec

        file_repo = PhysicalFilesystem()
        json_codec = JsonCodec()
        hash_service = HashService()

        # Write sidecar to dst
        sidecar_path = dst / "doc.md.cidatkn"
        sidecar_path.write_text(json.dumps(sidecar_data), encoding="utf-8")

        uc = SidecarValidatorUsecase(file_repo, json_codec, hash_service)
        # Should not raise
        uc.verify_destination_sidecars(str(src), str(dst))


class TestValidateOutputBundleNegativeContracts:
    def _validator(self):
        return SidecarValidatorUsecase(PhysicalFilesystem(), JsonCodec(), HashService())

    def test_missing_derived_sidecar_fails(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        source = src / "doc.md"
        source.write_text("# Hello", encoding="utf-8")
        output = dst / "doc.md"
        output.write_text(
            create_compressed_envelope("Payload", "doc.md.cidatkn", HashService().sha256(source.read_bytes())),
            encoding="utf-8",
        )

        with pytest.raises(SidecarValidationError) as exc_info:
            self._validator().validate_output_bundle(str(src), str(dst), str(output))

        assert exc_info.value.exit_code == 5
        assert "Required sidecar file does not exist" in str(exc_info.value)

    def test_output_invalid_utf8_fails_before_envelope_parse(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        output = dst / "doc.md"
        output.write_bytes(b"\xff\xfe\xfd")

        with pytest.raises(Exception) as exc_info:
            self._validator().validate_output_bundle(str(src), str(dst), str(output))

        assert exc_info.value.__class__.__name__ == "EncodingValidationError"

    def test_absolute_sidecar_source_inside_root_still_fails(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        source = src / "doc.md"
        source.write_text("# Hello world", encoding="utf-8")
        source_sha = HashService().sha256(source.read_bytes())
        output = dst / "doc.md"
        sidecar = dst / "doc.md.cidatkn"

        output.write_text(
            create_compressed_envelope("Payload", "doc.md.cidatkn", source_sha),
            encoding="utf-8",
        )
        sidecar.write_text(json.dumps({
            "format": "cida-token-sidecar",
            "version": 1,
            "source": str(source),
            "source_sha256": source_sha,
            "entries": {},
        }), encoding="utf-8")

        with pytest.raises(SidecarValidationError) as exc_info:
            self._validator().validate_output_bundle(str(src), str(dst), str(output), str(sidecar))

        assert exc_info.value.exit_code == 5
        assert "relative path" in str(exc_info.value)
