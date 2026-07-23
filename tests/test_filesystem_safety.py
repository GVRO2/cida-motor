import pytest
import sys
import subprocess
from cida.domain.errors import SourcePathError
from cida.infrastructure.filesystem import validate_filesystem_safety

def test_identical_source_destination_rejected(tmp_path):
    src = tmp_path / "folder"
    src.mkdir()
    with pytest.raises(SourcePathError) as exc:
        validate_filesystem_safety(str(src), str(src))
    assert "Destination path cannot be identical to source path" in str(exc.value)

def test_destination_inside_source_rejected(tmp_path):
    src = tmp_path / "src_dir"
    src.mkdir()
    dst = src / "output_dir"
    with pytest.raises(SourcePathError) as exc:
        validate_filesystem_safety(str(src), str(dst))
    assert "Destination directory cannot be nested inside source directory" in str(exc.value)

def test_source_inside_destination_rejected(tmp_path):
    dst = tmp_path / "dst_dir"
    dst.mkdir()
    src = dst / "src_dir"
    src.mkdir()
    with pytest.raises(SourcePathError) as exc:
        validate_filesystem_safety(str(src), str(dst))
    assert "Source directory cannot be inside destination directory" in str(exc.value)

def test_cli_e2e_filesystem_safety_rejected(tmp_path):
    src_dir = tmp_path / "data"
    src_dir.mkdir()
    (src_dir / "test.md").write_text("Hello world", encoding="utf-8")

    cmd = [
        sys.executable, "-m", "cida.interfaces.cli",
        "--src", str(src_dir),
        "--dst", str(src_dir),
        "--mode", "lossless"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 4
    assert "Destination path cannot be identical to source path" in res.stderr
