import pytest

from cida.application.content_search_index import build_content_search_index_artifacts, validate_content_search_segment
from cida.domain.errors import SidecarValidationError
from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec


def test_content_search_index_rejects_path_traversal_postings():
    hs = HashService()
    jc = JsonCodec()
    corpus_id = hs.sha256(b"corpus")
    artifacts = build_content_search_index_artifacts(
        [("safe.py.tknc", "needle")],
        corpus_id=corpus_id,
        hash_service=hs,
        json_codec=jc,
    )
    segment_path, segment = next(iter(artifacts.segments.items()))
    segment_id = segment["segment_id"]
    segment["terms"]["needle"] = ["../evil.py"]

    with pytest.raises(SidecarValidationError, match="Unsafe content search path"):
        validate_content_search_segment(
            segment,
            segment_id=segment_id,
            expected_sha256=segment["segment_sha256"],
            corpus_id=corpus_id,
            hash_service=hs,
            json_codec=jc,
        )

    assert segment_path.startswith("search-index/")
