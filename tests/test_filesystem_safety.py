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

def test_physical_filesystem_operations(tmp_path):
    from cida.infrastructure.filesystem import PhysicalFilesystem, validate_filesystem_safety
    from cida.domain.errors import EncodingValidationError

    fs = PhysicalFilesystem(durable=True)

    txt_file = str(tmp_path / "test.txt")
    bin_file = str(tmp_path / "test.bin")

    fs.write_text(txt_file, "Hello world", durable=True)
    assert fs.exists(txt_file)
    assert fs.is_file(txt_file)
    assert fs.read_text(txt_file) == "Hello world\n" or fs.read_text(txt_file) == "Hello world"

    fs.write_bytes(bin_file, b"Hello\x00world", durable=True)
    assert fs.read_bytes(bin_file) == b"Hello\x00world"
    assert fs.is_binary_file(bin_file)

    files = fs.list_files(str(tmp_path))
    assert len(files) >= 2

    assert fs.list_dir(str(tmp_path / "nonexistent")) == []
    assert len(fs.list_dir(str(tmp_path))) >= 2

    fs.remove(txt_file)
    assert not fs.exists(txt_file)

    # Encoding error check
    invalid_utf8 = str(tmp_path / "bad.txt")
    fs.write_bytes(invalid_utf8, b"\x80\x81\x82")
    with pytest.raises(EncodingValidationError):
        fs.read_text(invalid_utf8)

    # Report path safety check — report_path is a stem (no extension).
    # validate_filesystem_safety checks report_path+'.md' and report_path+'.json'.
    # Create a .md source file and a report_path that when suffixed with '.md'
    # collides with that source file.
    md_file = str(tmp_path / "report")
    with open(md_file + ".md", "w", encoding="utf-8") as f:
        f.write("source content")
    with pytest.raises(SourcePathError):
        validate_filesystem_safety(md_file + ".md", str(tmp_path / "dst"), report_path=md_file)
