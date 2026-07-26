import pytest

from cida.application.content_search_index import (
    SEARCH_INDEX_FILENAME,
    build_content_search_index_artifacts,
    normalize_terms,
    segment_id_for_term,
    validate_content_search_index,
    validate_content_search_segment,
)
from cida.domain.errors import SidecarValidationError
from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec
from cida.infrastructure.tknc_context_session import ContextFilesystem, search_context
from cida.infrastructure.tokenizer import OfflineTokenizer


def test_content_search_index_loads_only_term_segments_and_candidates(tmp_path):
    hs = HashService()
    jc = JsonCodec()
    files = [
        ("a.py.tknc", "def alpha_worker(): return 'needle_symbol'"),
        ("b.py.tknc", "def beta_worker(): return 'other'"),
        ("docs/readme.md", "needle_symbol documentation"),
    ]
    artifacts = build_content_search_index_artifacts(files, corpus_id=hs.sha256(b"corpus"), hash_service=hs, json_codec=jc)
    tknd = tmp_path / "tknd"
    tknd.mkdir()
    for segment_path, segment in artifacts.segments.items():
        full_segment = tknd / segment_path
        full_segment.parent.mkdir(parents=True, exist_ok=True)
        full_segment.write_text(jc.encode(segment, indent=4), encoding="utf-8", newline="\n")
    (tknd / SEARCH_INDEX_FILENAME).write_text(jc.encode(artifacts.root, indent=4), encoding="utf-8", newline="\n")
    for rel, text in files:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    fs = ContextFilesystem()
    result = search_context(tmp_path, "needle_symbol", fs, OfflineTokenizer(), query_id="indexed")

    assert result.search_mode == "INDEXED"
    assert result.files == ("a.py.tknc", "docs/readme.md")
    assert result.files_scanned == 2
    assert result.files_available == 3
    assert fs.physical_read_count("content") == 2


def test_content_search_index_root_hash_validates():
    hs = HashService()
    jc = JsonCodec()
    artifacts = build_content_search_index_artifacts(
        [("a.py.tknc", "alpha needle")],
        corpus_id=hs.sha256(b"corpus"),
        hash_service=hs,
        json_codec=jc,
    )

    assert artifacts.root["segmentation"] == "single"
    validate_content_search_index(artifacts.root, hash_service=hs, json_codec=jc)


def test_content_search_index_normalizes_long_terms_and_fallback_segments():
    long_term = "A" + ("b" * 80)

    assert normalize_terms(long_term) == (("a" + ("b" * 63)),)
    assert segment_id_for_term("") == "_"
    assert segment_id_for_term("alpha") == "a-f"
    assert segment_id_for_term("needle") == "m-r"
    assert segment_id_for_term("token") == "s-z"
    assert segment_id_for_term("123token") == "0-9"
    assert segment_id_for_term("_needle") == "_"


def test_content_search_index_rejects_invalid_root_and_segment_shapes():
    hs = HashService()
    jc = JsonCodec()
    corpus_id = hs.sha256(b"corpus")
    artifacts = build_content_search_index_artifacts(
        [("a.py.tknc", "alpha needle")],
        corpus_id=corpus_id,
        hash_service=hs,
        json_codec=jc,
    )

    with pytest.raises(SidecarValidationError, match="JSON object"):
        validate_content_search_index([], hash_service=hs, json_codec=jc)

    root = dict(artifacts.root)
    root["segments"] = {"ab": next(iter(root["segments"].values()))}
    root["segment_count"] = 1
    with pytest.raises(SidecarValidationError, match="segment id"):
        validate_content_search_index(root, hash_service=hs, json_codec=jc)

    root = dict(artifacts.root)
    root["segments"] = {"a-f": []}
    root["segment_count"] = 1
    with pytest.raises(SidecarValidationError, match="metadata"):
        validate_content_search_index(root, hash_service=hs, json_codec=jc)

    root = dict(artifacts.root)
    root["segments"] = {"a-f": {"path": 1}}
    root["segment_count"] = 1
    with pytest.raises(SidecarValidationError, match="segment path"):
        validate_content_search_index(root, hash_service=hs, json_codec=jc)

    root = dict(artifacts.root)
    root["segments"] = {
        "a-f": {
            "path": "search-index/segment-a-f.json",
            "sha256": next(iter(artifacts.segments.values()))["segment_sha256"],
            "term_count": -1,
        }
    }
    root["segment_count"] = 1
    with pytest.raises(SidecarValidationError, match="term_count"):
        validate_content_search_index(root, hash_service=hs, json_codec=jc)

    with pytest.raises(SidecarValidationError, match="JSON object"):
        validate_content_search_segment(
            [],
            segment_id="a-f",
            expected_sha256="0" * 64,
            corpus_id=corpus_id,
            hash_service=hs,
            json_codec=jc,
        )

    with pytest.raises(SidecarValidationError, match="Invalid content search path"):
        build_content_search_index_artifacts([("", "alpha")], corpus_id=corpus_id, hash_service=hs, json_codec=jc)

    root = dict(artifacts.root)
    root["segments"] = {
        "a-f": {
            "path": "search-index/segment-a-f/../bad.json",
            "sha256": next(iter(artifacts.segments.values()))["segment_sha256"],
            "term_count": 1,
        }
    }
    root["segment_count"] = 1
    with pytest.raises(SidecarValidationError, match="Unsafe content search segment path"):
        validate_content_search_index(root, hash_service=hs, json_codec=jc)
