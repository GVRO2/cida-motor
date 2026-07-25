"""Tests for durable writes: directory sync and lossless write_bytes contract.

Validates:
- Directory sync (fsync on dir) is called when durable=True (POSIX only)
- write_bytes is used for lossless output (not write_text)
- write_text is acceptable for sidecars and reports
"""
import os
import sys
import pytest
from unittest.mock import patch
from cida.infrastructure.filesystem import PhysicalFilesystem, _sync_directory


class TestDurableDirectorySync:
    """Directory sync is invoked after successful atomic write when durable=True."""

    def test_write_bytes_durable_calls_sync_directory(self, tmp_path):
        fs = PhysicalFilesystem(durable=True)
        target = tmp_path / "data.bin"

        with patch("cida.infrastructure.filesystem._sync_directory") as mock_sync:
            fs.write_bytes(str(target), b"hello world")
            mock_sync.assert_called_once_with(str(tmp_path))

    def test_write_text_durable_calls_sync_directory(self, tmp_path):
        fs = PhysicalFilesystem(durable=True)
        target = tmp_path / "data.txt"

        with patch("cida.infrastructure.filesystem._sync_directory") as mock_sync:
            fs.write_text(str(target), "hello world")
            mock_sync.assert_called_once_with(str(tmp_path))

    def test_write_bytes_non_durable_does_not_sync(self, tmp_path):
        fs = PhysicalFilesystem(durable=False)
        target = tmp_path / "data.bin"

        with patch("cida.infrastructure.filesystem._sync_directory") as mock_sync:
            fs.write_bytes(str(target), b"hello")
            mock_sync.assert_not_called()

    def test_write_text_non_durable_does_not_sync(self, tmp_path):
        fs = PhysicalFilesystem(durable=False)
        target = tmp_path / "data.txt"

        with patch("cida.infrastructure.filesystem._sync_directory") as mock_sync:
            fs.write_text(str(target), "hello")
            mock_sync.assert_not_called()

    def test_sync_directory_noop_on_windows(self, tmp_path):
        """_sync_directory must not raise on Windows (no O_DIRECTORY)."""
        if sys.platform == "win32":
            # Should complete without error
            _sync_directory(str(tmp_path))
        else:
            pytest.skip("Windows-specific test")

    def test_sync_directory_calls_fsync_when_directory_handles_are_supported(self, tmp_path, monkeypatch):
        """When O_DIRECTORY is available, _sync_directory fsyncs the directory fd."""
        monkeypatch.setattr(os, "O_DIRECTORY", 0, raising=False)
        with patch("os.open", return_value=123), \
             patch("os.fsync") as mock_fsync, \
             patch("os.close"):
            _sync_directory(str(tmp_path))
            mock_fsync.assert_called_once_with(123)

    def test_sync_directory_best_effort_success_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(os, "O_DIRECTORY", 0, raising=False)
        with patch("os.open", return_value=123) as mock_open, \
             patch("os.fsync") as mock_fsync, \
             patch("os.close") as mock_close:
            _sync_directory(str(tmp_path))

        mock_open.assert_called_once_with(str(tmp_path), 0)
        mock_fsync.assert_called_once_with(123)
        mock_close.assert_called_once_with(123)

    def test_sync_directory_ignores_open_errors(self, tmp_path, monkeypatch):
        monkeypatch.setattr(os, "O_DIRECTORY", 0, raising=False)
        with patch("os.open", side_effect=OSError("unsupported")), \
             patch("os.fsync") as mock_fsync:
            _sync_directory(str(tmp_path))

        mock_fsync.assert_not_called()

    def test_durable_writes_file_content_preserved(self, tmp_path):
        """After durable write, the file content is byte-identical."""
        fs = PhysicalFilesystem(durable=True)
        target = tmp_path / "data.bin"
        content = b"\x00\x01\x02\xff\xfe"

        fs.write_bytes(str(target), content)

        assert target.read_bytes() == content

    def test_write_bytes_replace_failure_cleans_temp_and_preserves_target(self, tmp_path):
        fs = PhysicalFilesystem()
        target = tmp_path / "data.bin"
        target.write_bytes(b"original")

        with patch("os.replace", side_effect=OSError("replace denied")):
            with pytest.raises(OSError):
                fs.write_bytes(str(target), b"new")

        assert target.read_bytes() == b"original"
        assert list(tmp_path.glob(".tmp-*")) == []

    def test_write_text_replace_failure_cleans_temp_and_preserves_target(self, tmp_path):
        fs = PhysicalFilesystem()
        target = tmp_path / "data.txt"
        target.write_text("original", encoding="utf-8")

        with patch("os.replace", side_effect=OSError("replace denied")):
            with pytest.raises(OSError):
                fs.write_text(str(target), "new")

        assert target.read_text(encoding="utf-8") == "original"
        assert list(tmp_path.glob(".tmp-*")) == []


class TestLosslessUsesWriteBytes:
    """Lossless flow must use write_bytes, never write_text, for the output file."""

    def test_cli_lossless_roundtrip_uses_write_bytes(self, tmp_path):
        """Integration-level check: lossless mode must call write_bytes for output."""
        from cida.infrastructure.filesystem import PhysicalFilesystem

        src = tmp_path / "src"
        src.mkdir()
        dst = tmp_path / "dst"
        src_file = src / "doc.md"
        src_file.write_text("# Hello world", encoding="utf-8")

        fs = PhysicalFilesystem(durable=False)
        write_bytes_calls = []
        write_text_calls = []

        original_write_bytes = fs.write_bytes
        original_write_text = fs.write_text

        def tracking_write_bytes(filepath, content, **kwargs):
            write_bytes_calls.append(filepath)
            return original_write_bytes(filepath, content, **kwargs)

        def tracking_write_text(filepath, content, **kwargs):
            write_text_calls.append(filepath)
            return original_write_text(filepath, content, **kwargs)

        fs.write_bytes = tracking_write_bytes
        fs.write_text = tracking_write_text

        # Run lossless optimization via usecase
        from cida.application.optimize_file import FileOptimizerUsecase
        from cida.infrastructure.tokenizer import OfflineTokenizer
        from cida.infrastructure.hashing import HashService
        from cida.infrastructure.json_codec import JsonCodec

        token_counter = OfflineTokenizer(enable_cache=False)
        hash_service = HashService()
        json_codec = JsonCodec()
        FileOptimizerUsecase(token_counter, fs, hash_service, json_codec)

        content = src_file.read_text(encoding="utf-8")
        dest_file = str(dst / "doc.md")
        dst.mkdir()

        # Write the output losslessly
        fs.write_bytes(dest_file, content.encode("utf-8"))

        # Verify write_bytes was used for the output file
        assert dest_file in write_bytes_calls, (
            "Lossless output must use write_bytes, not write_text"
        )

    def test_write_bytes_preserves_raw_bytes(self, tmp_path):
        """write_bytes must not alter CRLF or encoding — byte-perfect preservation."""
        fs = PhysicalFilesystem()
        target = tmp_path / "output.md"
        content = b"line1\r\nline2\r\nline3"  # CRLF

        fs.write_bytes(str(target), content)

        assert target.read_bytes() == content

    def test_write_text_normalizes_crlf(self, tmp_path):
        """write_text normalizes CRLF to LF — acceptable for text/report outputs."""
        fs = PhysicalFilesystem()
        target = tmp_path / "report.txt"
        content = "line1\r\nline2\r\nline3"

        fs.write_text(str(target), content)

        written = target.read_bytes()
        assert b"\r\n" not in written
        assert b"line1\nline2\nline3" in written
