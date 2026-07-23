import sys
import pytest
from unittest.mock import patch
from cida.interfaces.decompress_cli import main as decompress_main
from cida.application.decompress_file import FileDecompressorUsecase
from cida.infrastructure.filesystem import PhysicalFilesystem
from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec
from cida.domain.errors import SourcePathError, SidecarValidationError, ReconstructionError, EncodingValidationError
from cida.domain.reconstruction import reconstruct_content

def test_reconstruct_content_direct():
    # 1. Invalid sidecar_data
    with pytest.raises(SidecarValidationError):
        reconstruct_content("text", None)

    # 2. Header stripping and empty entries
    header_text = "> 🤖 AI RAG DICT: test\n\nOriginal text content"
    sidecar_empty = {
        "format": "cida-token-sidecar",
        "version": 1,
        "source": "doc.md",
        "source_sha256": "a" * 64,
        "entries": {}
    }
    assert reconstruct_content(header_text, sidecar_empty) == "Original text content"

    # 3. Alias substitution with word boundaries and length ordering
    sidecar_entries = {
        "format": "cida-token-sidecar",
        "version": 1,
        "source": "doc.md",
        "source_sha256": "b" * 64,
        "entries": {
            "_tkn1_": "replacement1",
            "_tkn2_": "replacement2"
        }
    }
    comp_text = "Here is _tkn1_ and _tkn2_."
    assert reconstruct_content(comp_text, sidecar_entries) == "Here is replacement1 and replacement2."

def test_decompress_cli_main_success(tmp_path):
    hs = HashService()
    jc = JsonCodec()

    src_file = tmp_path / "compressed.md"
    sidecar_file = tmp_path / "compressed.md.cidatkn"
    dst_file = tmp_path / "decompressed.md"

    orig_content = b"This is repeattoken candidate text repeattoken candidate text"
    orig_sha = hs.sha256(orig_content)

    compressed_content = "This is AA candidate text AA candidate text"
    src_file.write_bytes(compressed_content.encode("utf-8"))

    sidecar_data = {
        "format": "cida-token-sidecar",
        "version": 1,
        "source": "compressed.md",
        "source_sha256": orig_sha,
        "entries": {"AA": "repeattoken"}
    }
    sidecar_file.write_bytes(jc.encode(sidecar_data, indent=4).encode("utf-8"))

    test_args = ["decompress_cli.py", "--src", str(src_file), "--dst", str(dst_file), "--sidecar", str(sidecar_file)]
    with patch.object(sys, "argv", test_args):
        decompress_main()

    assert dst_file.exists()
    assert dst_file.read_bytes() == orig_content

def test_decompress_cli_main_cida_error(tmp_path):
    test_args = ["decompress_cli.py", "--src", str(tmp_path / "nonexistent.md"), "--dst", str(tmp_path / "out.md")]
    with patch.object(sys, "argv", test_args), patch("sys.exit") as mock_exit:
        decompress_main()
        mock_exit.assert_called_with(4)

def test_decompress_cli_main_unexpected_error(tmp_path):
    test_args = ["decompress_cli.py", "--src", str(tmp_path / "file.md"), "--dst", str(tmp_path / "out.md")]
    with patch.object(sys, "argv", test_args), \
         patch("cida.application.decompress_file.FileDecompressorUsecase.decompress_to_file", side_effect=Exception("crash")), \
         patch("sys.exit") as mock_exit:
        decompress_main()
        mock_exit.assert_called_with(6)

def test_decompress_file_usecase_edge_cases(tmp_path):
    fs = PhysicalFilesystem()
    hs = HashService()
    jc = JsonCodec()

    usecase = FileDecompressorUsecase(fs, jc, hs)

    # 1. Source missing -> SourcePathError
    with pytest.raises(SourcePathError):
        usecase.decompress(str(tmp_path / "missing.md"))

    # 2. Invalid UTF-8 in compressed file -> EncodingValidationError
    inv_file = tmp_path / "invalid.md"
    inv_file.write_bytes(b"\x80\x81\x82")
    with pytest.raises(EncodingValidationError):
        usecase.decompress(str(inv_file))

    # 3. Uncompressed file without sidecar -> returns raw content
    src = tmp_path / "doc.md"
    src.write_bytes(b"hello world")
    assert usecase.decompress(str(src)) == b"hello world"

    # 4. Compressed file with marker but sidecar missing -> SidecarValidationError (exit code 5)
    comp_marked = tmp_path / "marked.md"
    comp_marked.write_bytes(b"> \xf0\x9f\xa4\x96 AI RAG DICT: _tkn1_=word\n\n_tkn1_ content")
    with pytest.raises(SidecarValidationError, match="Missing required sidecar file"):
        usecase.decompress(str(comp_marked))

    # 5. Sidecar file contains malformed JSON -> SidecarValidationError
    bad_json_sidecar = tmp_path / "marked.md.cidatkn"
    bad_json_sidecar.write_bytes(b"invalid json")
    with pytest.raises(SidecarValidationError, match="Failed to parse JSON"):
        usecase.decompress(str(comp_marked))

    # 6. Sidecar source field invalid type -> SidecarValidationError
    sidecar_bad_src = {
        "format": "cida-token-sidecar",
        "version": 1,
        "source": 123,
        "source_sha256": "a" * 64,
        "entries": {"_tkn1_": "word"}
    }
    bad_json_sidecar.write_bytes(jc.encode(sidecar_bad_src).encode("utf-8"))
    with pytest.raises(SidecarValidationError, match="non-empty string"):
        usecase.decompress(str(comp_marked))

    # 7. Sidecar source identity mismatch -> SidecarValidationError
    sidecar_data_mismatch = {
        "format": "cida-token-sidecar",
        "version": 1,
        "source": "other.md",
        "source_sha256": "a" * 64,
        "entries": {"_tkn1_": "word"}
    }
    bad_json_sidecar.write_bytes(jc.encode(sidecar_data_mismatch).encode("utf-8"))
    with pytest.raises(SidecarValidationError, match="Sidecar source mismatch"):
        usecase.decompress(str(comp_marked))

    # 8. Sidecar path traversal -> SidecarValidationError
    sidecar_data_traversal = {
        "format": "cida-token-sidecar",
        "version": 1,
        "source": "../marked.md",
        "source_sha256": "a" * 64,
        "entries": {"_tkn1_": "word"}
    }
    bad_json_sidecar.write_bytes(jc.encode(sidecar_data_traversal).encode("utf-8"))
    with pytest.raises(SidecarValidationError, match="Path traversal detected"):
        usecase.decompress(str(comp_marked))

    # 9. SHA mismatch error -> ReconstructionError
    sidecar_data_sha = {
        "format": "cida-token-sidecar",
        "version": 1,
        "source": "marked.md",
        "source_sha256": "0" * 64,
        "entries": {"_tkn1_": "word"}
    }
    bad_json_sidecar.write_bytes(jc.encode(sidecar_data_sha).encode("utf-8"))
    with pytest.raises(ReconstructionError, match="Reconstructed SHA256 mismatch"):
        usecase.decompress(str(comp_marked))

    # 10. Corpus sidecar source -> allowed
    sidecar_corpus = {
        "format": "cida-token-sidecar",
        "version": 1,
        "source": "corpus",
        "source_sha256": hs.sha256(b"word content"),
        "entries": {"_tkn1_": "word"}
    }
    bad_json_sidecar.write_bytes(jc.encode(sidecar_corpus).encode("utf-8"))
    assert usecase.decompress(str(comp_marked)) == b"word content"
