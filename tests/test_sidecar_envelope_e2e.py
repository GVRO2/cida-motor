import pytest
import os
import sys
import subprocess
from pathlib import Path
from cida.infrastructure.filesystem import PhysicalFilesystem
from cida.infrastructure.json_codec import JsonCodec
from cida.infrastructure.hashing import HashService
from cida.application.decompress_file import FileDecompressorUsecase
from cida.domain.errors import SidecarValidationError


REPO_ROOT = Path(__file__).resolve().parent.parent


def _env():
    env = os.environ.copy()
    env["TIKTOKEN_CACHE_DIR"] = str(REPO_ROOT / "resources")
    return env

def test_sidecar_envelope_e2e_roundtrip(tmp_path):
    repo = PhysicalFilesystem()
    json_codec = JsonCodec()
    hash_service = HashService()

    # Create repetitive markdown document that triggers dictionary aliases
    content = ("supercalifragilisticexpialidocious " * 100) + "\n"
    src_file = tmp_path / "sample.md"
    src_file.write_bytes(content.encode("utf-8"))
    dst_dir = tmp_path / "compressed"

    # Compress via CLI
    cmd = [
        sys.executable, "-m", "cida.interfaces.cli",
        "--src", str(src_file),
        "--dst", str(dst_dir),
        "--mode", "lossless",
        "--profile", "markdown",
        "--dictionary-scope", "file"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, env=_env())
    assert res.returncode == 0

    compressed_file = dst_dir / "sample.md"
    sidecar_file = dst_dir / "sample.md.cidatkn"

    assert compressed_file.exists()
    assert sidecar_file.exists()

    compressed_text = compressed_file.read_text(encoding="utf-8")
    assert "<!-- CIDA_COMPRESSED_FORMAT" in compressed_text
    assert "sidecar_required: true" in compressed_text

    # Decompress via Usecase
    decompressor = FileDecompressorUsecase(repo, json_codec, hash_service)
    reconstructed_bytes = decompressor.decompress(str(compressed_file), str(sidecar_file))
    assert reconstructed_bytes.decode("utf-8") == content

    # Test missing sidecar failure (exit code 5)
    sidecar_backup = sidecar_file.read_text(encoding="utf-8")
    sidecar_file.unlink()

    with pytest.raises(SidecarValidationError):
        decompressor.decompress(str(compressed_file), str(sidecar_file))

    # Decompress CLI E2E test without sidecar -> exit code 5
    decomp_out = tmp_path / "decomp.md"
    cmd_decomp = [
        sys.executable, "-m", "cida.interfaces.decompress_cli",
        "--src", str(compressed_file),
        "--dst", str(decomp_out),
        "--sidecar", str(sidecar_file)
    ]
    res_decomp = subprocess.run(cmd_decomp, capture_output=True, text=True, env=_env())
    assert res_decomp.returncode == 5
    assert "Missing required sidecar file" in res_decomp.stderr or "Sidecar validation failed" in res_decomp.stderr

    # Restore sidecar and test CLI decompress -> success
    sidecar_file.write_text(sidecar_backup, encoding="utf-8")
    res_decomp_ok = subprocess.run(cmd_decomp, capture_output=True, text=True, env=_env())
    assert res_decomp_ok.returncode == 0
    assert decomp_out.read_text(encoding="utf-8") == content

def test_unmodified_file_no_envelope(tmp_path):
    repo = PhysicalFilesystem()
    json_codec = JsonCodec()
    hash_service = HashService()

    # Short content that doesn't trigger dictionary aliases
    content = "# Title\nUnique short content.\n"
    src_file = tmp_path / "short.md"
    src_file.write_bytes(content.encode("utf-8"))
    dst_dir = tmp_path / "dst"

    cmd = [
        sys.executable, "-m", "cida.interfaces.cli",
        "--src", str(src_file),
        "--dst", str(dst_dir),
        "--mode", "lossless"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, env=_env())
    assert res.returncode == 0

    compressed_file = dst_dir / "short.md"
    assert compressed_file.exists()
    assert not (dst_dir / "short.md.cidatkn").exists()
    assert "<!-- CIDA_COMPRESSED_FORMAT" not in compressed_file.read_text(encoding="utf-8")

    decompressor = FileDecompressorUsecase(repo, json_codec, hash_service)
    reconstructed_bytes = decompressor.decompress(str(compressed_file))
    assert reconstructed_bytes.decode("utf-8") == content
