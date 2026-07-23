import os
import sys
import subprocess
import pytest
from pathlib import Path
from cida.infrastructure.filesystem import PhysicalFilesystem
from cida.infrastructure.tokenizer import OfflineTokenizer
from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec
from cida.application.optimize_file import FileOptimizerUsecase
from cida.application.decompress_file import FileDecompressorUsecase

@pytest.fixture(autouse=True)
def setup_env():
    old_val = os.environ.get("TIKTOKEN_CACHE_DIR")
    os.environ["TIKTOKEN_CACHE_DIR"] = os.path.abspath("resources")
    yield
    if old_val is not None:
        os.environ["TIKTOKEN_CACHE_DIR"] = old_val
    else:
        os.environ.pop("TIKTOKEN_CACHE_DIR", None)

def test_production_decompressor_e2e_roundtrip(tmp_path):
    fs = PhysicalFilesystem()
    tok = OfflineTokenizer()
    hs = HashService()
    json_codec = JsonCodec()

    optimizer = FileOptimizerUsecase(tok, fs, hs, json_codec)
    decompressor = FileDecompressorUsecase(fs, json_codec, hs)

    root_path = Path(".")
    ignored_dirs = {".git", ".cida-local", "node_modules", "venv", ".venv", "htmlcov", "__pycache__", "Microsoft", ".pytest_cache", "resources"}

    eligible_files = []
    for ext in ["*.md", "*.txt"]:
        for p in root_path.rglob(ext):
            if any(part in ignored_dirs for part in p.parts):
                continue
            if "_mimificado" in p.name or p.name.endswith(".cidatkn"):
                continue
            eligible_files.append(p)

    eligible_files.sort()
    assert len(eligible_files) > 0

    results = []
    compressed_count = 0

    for file_path in eligible_files:
        original_bytes = fs.read_bytes(str(file_path))
        original_sha = hs.sha256(original_bytes)
        original_text = original_bytes.decode('utf-8')

        rel_path = str(file_path).replace('\\', '/')
        compressed_text, sidecar_data, dict_tokens = optimizer.optimize_markdown_dictionary_file_scope(
            original_text, original_text, rel_path, verify_semantics=True
        )

        dst_file = tmp_path / file_path.name
        fs.write_bytes(str(dst_file), compressed_text.encode('utf-8'))

        sidecar_file = tmp_path / (file_path.name + ".cidatkn")
        sidecar_created = False
        entries_count = 0
        if sidecar_data:
            fs.write_text(str(sidecar_file), json_codec.encode(sidecar_data, indent=4))
            compressed_count += 1
            sidecar_created = True
            entries_count = len(sidecar_data.get("entries", {}))
            assert fs.exists(str(sidecar_file))
            assert entries_count > 0

        reconstructed_bytes = decompressor.decompress(str(dst_file), str(sidecar_file) if sidecar_data else None)
        reconstructed_sha = hs.sha256(reconstructed_bytes)

        newline_style = "LF"
        if b"\r\n" in original_bytes:
            newline_style = "CRLF"
        elif b"\r" in original_bytes:
            newline_style = "CR"

        bom_present = original_bytes.startswith(b"\xef\xbb\xbf")
        roundtrip_passed = (reconstructed_bytes == original_bytes) and (reconstructed_sha == original_sha)

        results.append({
            "path": str(file_path),
            "original_sha256": original_sha,
            "reconstructed_sha256": reconstructed_sha,
            "original_size": len(original_bytes),
            "reconstructed_size": len(reconstructed_bytes),
            "newline_style": newline_style,
            "bom_present": bom_present,
            "sidecar_created": sidecar_created,
            "entries_count": entries_count,
            "compressed": sidecar_created,
            "roundtrip_passed": roundtrip_passed
        })

        assert roundtrip_passed, f"Byte mismatch for {file_path}"

    assert all(r["roundtrip_passed"] for r in results)
    assert compressed_count > 0, "No file was compressed in lossless E2E roundtrip"

@pytest.mark.parametrize("content_bytes, description", [
    (b"# Header\n\nThis is a simple test file with LF.\n", "UTF-8 sem BOM + LF"),
    (b"# Header\r\n\r\nThis is a simple test file with CRLF.\r\n", "UTF-8 sem BOM + CRLF"),
    (b"\xef\xbb\xbf# Header\n\nThis is a simple test file with BOM + LF.\n", "UTF-8 com BOM + LF"),
    (b"\xef\xbb\xbf# Header\r\n\r\nThis is a simple test file with BOM + CRLF.\r\n", "UTF-8 com BOM + CRLF"),
    (b"# Header\n\nNo final newline at end", "sem newline final"),
    (b"# Header\n\nWith final newline at end\n", "com newline final"),
    (b"# Header\n\nUnicode acentuado: A\xc3\xa7\xc3\xa3o, Cora\xc3\xa7\xc3\xa3o, Ol\xc3\xa1.\n", "Unicode acentuado"),
    (b"# Header\n\nEmoji test: \xf0\x9f\x9a\x80 \xf0\x9f\xa4\x96 \xe2\x9c\xa8\n", "emoji"),
    (b"# Header\n\tTabbed\tcontent\there.\n", "tabs"),
    (b"# Header   \nTrailing   spaces   \n", "trailing spaces"),
])
def test_lossless_roundtrip_byte_formats(tmp_path, content_bytes, description):
    fs = PhysicalFilesystem()
    hs = HashService()
    json_codec = JsonCodec()

    decompressor = FileDecompressorUsecase(fs, json_codec, hs)

    file_path = tmp_path / "format_test.md"
    file_path.write_bytes(content_bytes)

    rec_bytes = decompressor.decompress(str(file_path), sidecar_filepath=None)
    assert rec_bytes == content_bytes, f"Failed raw roundtrip for format: {description}"

def test_cli_decompressor_subprocess_roundtrip_realistic_and_crlf(tmp_path):
    fs = PhysicalFilesystem()
    hs = HashService()

    fixtures = [
        Path("tests/fixtures/lossless/repetitive_realistic.md"),
        Path("tests/fixtures/lossless/crlf.md")
    ]

    for fixture_src in fixtures:
        assert fixture_src.exists()

        original_bytes = fs.read_bytes(str(fixture_src))
        original_sha = hs.sha256(original_bytes)

        dst_dir = tmp_path / f"out_{fixture_src.stem}"
        dst_dir.mkdir(exist_ok=True)

        compress_cmd = [
            sys.executable, "token_optimizer.py",
            "--src", str(fixture_src),
            "--dst", str(dst_dir),
            "--mode", "lossless",
            "--dictionary-scope", "file"
        ]
        res_comp = subprocess.run(compress_cmd, capture_output=True, text=True)
        assert res_comp.returncode == 0, f"Compression CLI failed for {fixture_src}: {res_comp.stderr}"

        comp_file = dst_dir / fixture_src.name
        sidecar_file = dst_dir / (fixture_src.name + ".cidatkn")

        assert comp_file.exists()
        assert sidecar_file.exists()

        compressed_bytes = fs.read_bytes(str(comp_file))
        assert compressed_bytes != original_bytes, f"Expected compression gain on fixture {fixture_src}"

        decomp_out = tmp_path / f"reconstructed_{fixture_src.stem}.md"
        decompress_cmd = [
            sys.executable, "-m", "cida.interfaces.decompress_cli",
            "--src", str(comp_file),
            "--dst", str(decomp_out),
            "--sidecar", str(sidecar_file)
        ]
        res_decomp = subprocess.run(decompress_cmd, capture_output=True, text=True)
        assert res_decomp.returncode == 0, f"Decompression CLI failed for {fixture_src}: {res_decomp.stderr}"

        reconstructed_bytes = fs.read_bytes(str(decomp_out))
        assert reconstructed_bytes == original_bytes, f"Byte mismatch for fixture {fixture_src}"
        assert hs.sha256(reconstructed_bytes) == original_sha
