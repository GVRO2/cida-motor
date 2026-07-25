import json
import os
import sys
import subprocess
import pytest
from unittest.mock import patch
from pathlib import Path
from cida.domain.policies import ValidationLevel, validate_validation_level
from cida.domain.errors import UsageError, SidecarValidationError
from cida.application.strict_auditing import StrictBundleAuditor
from cida.infrastructure.filesystem import PhysicalFilesystem
from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec
from cida.interfaces.cli import main as cli_main

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_validate_validation_level_valid():
    assert ValidationLevel.BALANCED == "balanced"
    assert ValidationLevel.STRICT == "strict"
    assert validate_validation_level("balanced") == "balanced"
    assert validate_validation_level("strict") == "strict"
    assert validate_validation_level("BALANCED") == "balanced"
    assert validate_validation_level("STRICT") == "strict"



def test_validate_validation_level_invalid():
    with pytest.raises(UsageError) as exc_info:
        validate_validation_level("unknown")
    assert "Invalid validation level 'unknown'" in str(exc_info.value)


def test_cli_invalid_validation_level():
    cmd = [
        sys.executable, "-m", "cida.interfaces.cli",
        "--src", "some_path",
        "--dst", "some_dst",
        "--validation-level", "invalid_level",
    ]
    result = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    assert result.returncode == 1
    assert "error:" in result.stderr.lower() or "invalid" in result.stderr.lower()


def test_cli_strict_validation_conflicts_with_explicit_balanced():
    cmd = [
        sys.executable, "-m", "cida.interfaces.cli",
        "--src", "some_path",
        "--dst", "some_dst",
        "--strict-validation",
        "--validation-level", "balanced",
    ]
    result = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    assert result.returncode == 1
    assert "cannot be combined" in result.stderr


def test_functional_equivalence_balanced_and_strict(tmp_path):
    """Verify that balanced and strict validation levels produce identical output files and sidecars."""
    src_dir = tmp_path / "src"
    dst_balanced = tmp_path / "dst_balanced"
    dst_strict = tmp_path / "dst_strict"

    src_dir.mkdir()
    sample_text = "# Title\n\n" + ("repeated_long_word_for_dictionary_testing " * 30) + "\n"
    (src_dir / "doc.md").write_text(sample_text, encoding="utf-8")

    env = os.environ.copy()
    env["TIKTOKEN_CACHE_DIR"] = str(REPO_ROOT / "resources")

    # Run balanced
    cmd_bal = [
        sys.executable, "-m", "cida.interfaces.cli",
        "--src", str(src_dir),
        "--dst", str(dst_balanced),
        "--validation-level", "balanced",
    ]
    res_bal = subprocess.run(cmd_bal, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True)
    assert res_bal.returncode == 0

    # Run strict
    cmd_str = [
        sys.executable, "-m", "cida.interfaces.cli",
        "--src", str(src_dir),
        "--dst", str(dst_strict),
        "--validation-level", "strict",
    ]
    res_str = subprocess.run(cmd_str, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True)
    assert res_str.returncode == 0

    out_bal = (dst_balanced / "doc.md").read_text(encoding="utf-8")
    out_str = (dst_strict / "doc.md").read_text(encoding="utf-8")
    assert out_bal == out_str

    side_bal_path = dst_balanced / "doc.md.cidatkn"
    side_str_path = dst_strict / "doc.md.cidatkn"
    assert side_bal_path.exists() == side_str_path.exists()
    if side_bal_path.exists():
        assert side_bal_path.read_text(encoding="utf-8") == side_str_path.read_text(encoding="utf-8")


def test_balanced_does_not_construct_strict_auditor(tmp_path, monkeypatch):
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    (src_dir / "doc.md").write_text("# Title\n\nSmall content.\n", encoding="utf-8")

    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(REPO_ROOT / "resources"))

    with patch.object(sys, "argv", [
        "cida",
        "--src", str(src_dir),
        "--dst", str(dst_dir),
        "--validation-level", "balanced",
    ]), patch("cida.interfaces.cli._load_strict_bundle_auditor") as load_auditor:
        cli_main()

    load_auditor.assert_not_called()


def test_python_context_parallelism_reported_for_independent_files(tmp_path, monkeypatch):
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    (src_dir / "a.md").write_text("# A\n\nSmall content.\n", encoding="utf-8")
    (src_dir / "b.md").write_text("# B\n\nSmall content.\n", encoding="utf-8")
    report_path = tmp_path / "report"

    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(REPO_ROOT / "resources"))

    with patch.object(sys, "argv", [
        "cida",
        "--src", str(src_dir),
        "--dst", str(dst_dir),
        "--workers", "2",
        "--resource-profile", "custom",
        "--report", "json",
        "--report-path", str(report_path),
        "--report-schema", "2",
    ]):
        cli_main()

    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["schema_version"] == 2
    assert report["resources"]["python_parallel_execution"] is True
    assert report["resources"]["effective_workers"] == 2
    assert "processing_context" in report["resources"]["parallel_stages"]
    assert "file_optimization" in report["resources"]["sequential_stages"]
    assert [entry["arquivo"] for entry in report["entries"]] == ["a.md", "b.md"]


def test_strict_constructs_auditor_once_and_audits_bundle_once(tmp_path, monkeypatch):
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    (src_dir / "doc.md").write_text("# Title\n\nSmall content.\n", encoding="utf-8")

    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(REPO_ROOT / "resources"))

    with patch.object(sys, "argv", [
        "cida",
        "--src", str(src_dir),
        "--dst", str(dst_dir),
        "--validation-level", "strict",
    ]), patch("cida.interfaces.cli._load_strict_bundle_auditor") as load_auditor:
        auditor_cls = load_auditor.return_value
        auditor = auditor_cls.return_value
        cli_main()

    load_auditor.assert_called_once()
    auditor_cls.assert_called_once()
    auditor.audit_destination_sidecars.assert_called_once()
    auditor.audit_output_bundle.assert_called_once()



def test_strict_auditor_detects_orphan_sidecar(tmp_path):
    """Verify that StrictBundleAuditor detects orphan sidecars in destination."""
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    file_repo = PhysicalFilesystem()
    json_codec = JsonCodec()
    hash_service = HashService()
    auditor = StrictBundleAuditor(file_repo, json_codec, hash_service)

    # Write orphan sidecar pointing to non-existent source
    orphan_sidecar = {
        "format": "cida-token-sidecar",
        "version": 1,
        "source": "non_existent_source.md",
        "source_sha256": "0" * 64,
        "entries": {"A0": "word"},
    }
    file_repo.write_text(str(dst_dir / "orphan.cidatkn"), json_codec.encode(orphan_sidecar))

    with pytest.raises(SidecarValidationError) as exc_info:
        auditor.audit_destination_sidecars(str(src_dir), str(dst_dir))
    assert "Orphan sidecar detected" in str(exc_info.value)


def test_processing_context_and_file_inventory():
    from cida.domain.processing_context import ProcessingContext, FileInventory

    ctx = ProcessingContext(
        source_path="src/file.md",
        source_real_path="/abs/src/file.md",
        relative_path="file.md",
        source_bytes=b"hello",
        source_text="hello",
        source_sha256="2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        detected_profile="markdown",
        original_tokens=1,
    )
    assert ctx.source_path == "src/file.md"
    assert ctx.original_tokens == 1

    inv = FileInventory()
    inv.all_files.append("file.md")
    assert "file.md" in inv.all_files


def test_strict_auditor_output_bundle_checks(tmp_path):
    file_repo = PhysicalFilesystem()
    json_codec = JsonCodec()
    hash_service = HashService()
    auditor = StrictBundleAuditor(file_repo, json_codec, hash_service)

    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    src_file = src_dir / "test.md"
    src_file.write_text("Hello World content\n", encoding="utf-8")
    src_bytes = src_file.read_bytes()
    src_sha = hash_service.sha256(src_bytes)

    # Missing output file test
    with pytest.raises(SidecarValidationError) as exc1:
        auditor.audit_output_bundle(str(src_dir), str(dst_dir), str(dst_dir / "missing.md"))
    assert "Output file does not exist" in str(exc1.value)

    # Plain output file (no envelope, no sidecar required)
    out_plain = dst_dir / "test.md"
    out_plain.write_text("Hello World content\n", encoding="utf-8")
    auditor.audit_output_bundle(str(src_dir), str(dst_dir), str(out_plain))

    # Envelope referencing required sidecar
    envelope = (
        f"<!-- CIDA_COMPRESSED_FORMAT\n"
        f"version: 1\n"
        f"mode: lossless\n"
        f"sidecar_required: true\n"
        f"sidecar_ref: test.md.cidatkn\n"
        f"source_sha256: {src_sha}\n"
        f"compression_strategy: dictionary\n"
        f"-->\nPayload content\n"
    )
    out_env = dst_dir / "env.md"
    out_env.write_text(envelope, encoding="utf-8")

    # Missing sidecar error
    with pytest.raises(SidecarValidationError) as exc2:
        auditor.audit_output_bundle(str(src_dir), str(dst_dir), str(out_env))
    assert "Required sidecar file does not exist" in str(exc2.value)

    # Add valid sidecar
    sidecar_data = {
        "format": "cida-token-sidecar",
        "version": 1,
        "source": "test.md",
        "source_sha256": src_sha,
        "entries": {"A0": "Payload"},
    }
    sidecar_file = dst_dir / "test.md.cidatkn"
    sidecar_file.write_text(json_codec.encode(sidecar_data), encoding="utf-8")

    # Full audit success
    auditor.audit_output_bundle(str(src_dir), str(dst_dir), str(out_env), sidecar_file=str(sidecar_file))
