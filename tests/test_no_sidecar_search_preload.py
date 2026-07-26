from pathlib import Path

from benchmarks.context_usage_compare import _build_tknc_corpus, _question_set, _write_fixture_corpus
from cida.infrastructure.tknc_context_session import ContextFilesystem, TkncContextSession, is_content_artifact, is_evidence_artifact, is_lookup_artifact
from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec
from cida.infrastructure.tokenizer import OfflineTokenizer


def test_search_classifies_lookup_and_evidence_out_of_initial_content(tmp_path):
    assert is_content_artifact(Path("src/example.py"))
    assert is_content_artifact(Path("src/example.py.tknc"))
    assert is_lookup_artifact(Path("tknd/alias-index.json"))
    assert is_lookup_artifact(Path("tknd/chunk-000000.cidatkn"))
    assert is_lookup_artifact(Path("tknd/content-search-index.json"))
    assert is_lookup_artifact(Path("tknd/search-index/segment-a.json"))
    assert is_evidence_artifact(Path("tknc-manifest.json"))
    assert not is_content_artifact(Path("tknd/chunk-000000.cidatkn"))
    assert not is_content_artifact(Path("tknc-manifest.json"))


def test_no_sidecar_or_index_is_read_during_initial_tknc_search(tmp_path):
    original, relpaths = _write_fixture_corpus(tmp_path, "search", 600)
    tknc = tmp_path / "search" / "tknc"
    _build_tknc_corpus(original, tknc, relpaths)
    fs = ContextFilesystem()
    session = TkncContextSession(tknc, fs, JsonCodec(), HashService(), OfflineTokenizer())

    search = session.search(_question_set()[0].question, query_id="Q-search")

    assert search.files
    assert search.search_mode == "INDEXED"
    assert search.search_index_segments_loaded > 0
    assert all(event.artifact_type in {"content", "search_index"} for event in fs.reads)
    assert not any(event.artifact_type in {"sidecar", "alias_index", "manifest"} for event in fs.reads)
