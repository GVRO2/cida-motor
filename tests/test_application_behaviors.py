import os
import pytest
from unittest.mock import MagicMock, patch
from cida.application.optimize_file import FileOptimizerUsecase
from cida.application.optimize_corpus import CorpusOptimizerUsecase
from cida.application.generate_report import ReportGeneratorUsecase
from cida.application.generate_manifest import ManifestGeneratorUsecase
from cida.application.validate_sidecar import SidecarValidatorUsecase
from cida.infrastructure.filesystem import PhysicalFilesystem
from cida.infrastructure.tokenizer import OfflineTokenizer
from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec
from cida.domain.errors import SidecarValidationError

@pytest.fixture(autouse=True)
def setup_env():
    old_val = os.environ.get("TIKTOKEN_CACHE_DIR")
    os.environ["TIKTOKEN_CACHE_DIR"] = os.path.abspath("resources")
    yield
    if old_val is not None:
        os.environ["TIKTOKEN_CACHE_DIR"] = old_val
    else:
        os.environ.pop("TIKTOKEN_CACHE_DIR", None)

def test_optimize_file_detect_profile():
    fs = PhysicalFilesystem()
    tok = OfflineTokenizer(cache_dir="resources")
    hs = HashService()
    jc = JsonCodec()

    usecase = FileOptimizerUsecase(tok, fs, hs, jc)

    assert usecase.detect_profile("App.java", "public class App {}") == "java"
    assert usecase.detect_profile("workflow.md", "stepsCompleted: 1") == "bmad"
    assert usecase.detect_profile("random/path/_bmad/file.md", "hello") == "bmad"
    assert usecase.detect_profile("code.py", "print(1)") == "code"
    assert usecase.detect_profile("regular.md", "Just text markdown content.") == "markdown"

def test_optimize_file_scope_semantic_fail_branch():
    fs = PhysicalFilesystem()
    tok = OfflineTokenizer(cache_dir="resources")
    hs = HashService()
    jc = JsonCodec()

    usecase = FileOptimizerUsecase(tok, fs, hs, jc)
    text = "This is regular_word_long repeated. " * 15

    with patch("cida.application.optimize_file.validate_semantics", return_value=(False, "Failed")):
        res_text, sidecar, tokens = usecase.optimize_markdown_dictionary_file_scope(text, text, "doc.md", True)
        assert res_text == text

def test_optimize_file_scope_sidecar_exception_branch():
    fs = PhysicalFilesystem()
    tok = OfflineTokenizer(cache_dir="resources")
    hs = HashService()
    jc = JsonCodec()

    usecase = FileOptimizerUsecase(tok, fs, hs, jc)
    text = "This is regular_word_long repeated. " * 15

    with patch("cida.application.optimize_file.create_sidecar_data", side_effect=Exception("sidecar fail")):
        res_text, sidecar, tokens = usecase.optimize_markdown_dictionary_file_scope(text, text, "doc.md", True)
        assert res_text == text

def test_optimize_corpus_empty_and_exception_branches(tmp_path):
    fs = PhysicalFilesystem()
    tok = OfflineTokenizer(cache_dir="resources")
    hs = HashService()
    jc = JsonCodec()
    builder = MagicMock()

    builder.build_corpus_dictionary.return_value = {}
    usecase = CorpusOptimizerUsecase(tok, fs, hs, jc, builder)
    f1 = tmp_path / "f1.md"
    f1.write_text("word content repeated")

    res = usecase.build_corpus_dict([str(f1)], str(tmp_path))
    assert res == ({}, "", 0, 0)

    mock_fs = MagicMock(spec=fs)
    mock_fs.is_binary_file.return_value = False
    mock_fs.read_text.side_effect = Exception("read fail")
    mock_fs.read_bytes.side_effect = Exception("read fail")
    usecase_err = CorpusOptimizerUsecase(tok, mock_fs, hs, jc, builder)
    res_err = usecase_err.build_corpus_dict(["f1.md"], str(tmp_path))
    assert res_err == ({}, "", 0, 0)

def test_generate_report_formatting(tmp_path):
    fs = PhysicalFilesystem()
    jc = JsonCodec()

    gen = ReportGeneratorUsecase(fs, jc)
    file1 = tmp_path / "file1.md"
    file1.write_text("hello")
    gen.add_entry(
        filepath=str(file1),
        profile="markdown",
        tokens_orig=100,
        tokens_base=90,
        tokens_new=85,
        dict_included=True,
        tokens_sidecar=5,
        tokens_aux=0,
        accepted_transforms=["trim"],
        rejected_transforms=[],
        semantic_status="VALID",
        execution_time=0.1
    )
    gen.make_deterministic(str(tmp_path))
    md = gen.generate_markdown(deterministic=True)
    assert "# Relatório de Benchmark - CIDA Motor" in md
    assert "file1.md" in md
    assert "markdown" in md

def test_generate_tree_manifest(tmp_path):
    fs = PhysicalFilesystem()
    hs = HashService()
    jc = JsonCodec()

    gen = ManifestGeneratorUsecase(fs, hs, jc)
    f1 = tmp_path / "f1.txt"
    f1.write_text("hello world")

    manifest = gen.generate_tree_manifest(str(tmp_path))
    assert "tree_sha256" in manifest
    assert len(manifest["tree_sha256"]) == 64
    assert len(manifest["files"]) == 1
    assert manifest["files"][0]["path"] == "f1.txt"
    assert manifest["files"][0]["sha256"] == hs.sha256(b"hello world")

def test_sidecar_validator_usecase(tmp_path):
    fs = PhysicalFilesystem()
    jc = JsonCodec()
    hs = HashService()

    val = SidecarValidatorUsecase(fs, jc, hs)

    dst = tmp_path / "dst"
    dst.mkdir()
    f1 = tmp_path / "doc.md"
    f1.write_bytes(b"sample text content")

    sidecar = dst / "doc.md.cidatkn"
    sidecar_data = {
        "format": "cida-token-sidecar",
        "version": 1,
        "source": "doc.md",
        "source_sha256": hs.sha256(b"sample text content"),
        "entries": {"XY": "content"}
    }
    sidecar.write_text(jc.encode(sidecar_data))

    val.verify_destination_sidecars(str(tmp_path), str(dst))

def test_sidecar_validator_invalid_sidecar(tmp_path):
    fs = PhysicalFilesystem()
    jc = JsonCodec()
    hs = HashService()

    val = SidecarValidatorUsecase(fs, jc, hs)

    dst = tmp_path / "dst"
    dst.mkdir()

    sidecar = dst / "bad.cidatkn"
    sidecar.write_text('{"invalid_json": true}')

    with pytest.raises(SidecarValidationError):
        val.verify_destination_sidecars(str(tmp_path), str(dst))
