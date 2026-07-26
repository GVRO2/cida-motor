import json

import pytest

from cida.application.content_search_index import (
    build_content_search_index_artifacts,
    validate_content_search_index,
    validate_content_search_segment,
)
from cida.application.selective_alias_resolution import (
    AliasDetector,
    SelectiveAliasResolver,
    build_alias_index_artifacts,
    corpus_chunk_filename,
)
from cida.domain.errors import SidecarValidationError
from cida.infrastructure.filesystem import PhysicalFilesystem
from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec


def _alias_artifacts():
    hs = HashService()
    jc = JsonCodec()
    chunk = corpus_chunk_filename(0)
    return build_alias_index_artifacts(
        {"AA": chunk},
        hs.sha256(b"dictionary"),
        {chunk: hs.sha256(b"chunk")},
        hs,
        jc,
        manifest_sha256=hs.sha256(b"manifest"),
        chunk_entry_counts={chunk: 1},
    )


def test_alias_detector_v3_exact_aliases_path():
    detector = AliasDetector()

    assert detector.detect("AA AB 'AC'", {"schema_version": 3, "exact_aliases": ["AA"]}) == {"AA"}


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda root: root.update({"format": "wrong"}), "format"),
        (lambda root: root.update({"dictionary_id": "bad"}), "dictionary_id"),
        (lambda root: root.update({"alias_count": -1}), "alias_count"),
        (lambda root: root.update({"chunks": []}), "chunks"),
        (lambda root: root.update({"source_manifest_sha256": "bad"}), "source_manifest"),
        (lambda root: root.update({"alias_codec_version": 99}), "codec"),
        (lambda root: root.update({"membership": "APPROXIMATE_MEMBERSHIP"}), "exact membership"),
        (lambda root: root.update({"segments": []}), "segments"),
    ],
)
def test_alias_index_v3_rejects_invalid_root_shapes(mutation, message):
    artifacts = _alias_artifacts()
    root = json.loads(json.dumps(artifacts.root))
    mutation(root)

    resolver = SelectiveAliasResolver(PhysicalFilesystem(), JsonCodec(), HashService())
    with pytest.raises(SidecarValidationError, match=message):
        resolver._validate_index(root)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda segment: segment.update({"format": "wrong"}), "format"),
        (lambda segment: segment.update({"schema_version": 99}), "schema"),
        (lambda segment: segment.update({"corpus_id": "0" * 64}), "corpus_id"),
        (lambda segment: segment.update({"segment_id": "2-U-B"}), "id mismatch"),
        (lambda segment: segment.update({"alias_codec_version": 99}), "codec"),
        (lambda segment: segment.update({"aliases": []}), "aliases"),
        (lambda segment: segment["aliases"].update({"Aa": corpus_chunk_filename(0)}), "Malformed alias"),
        (lambda segment: segment["aliases"].update({"BA": corpus_chunk_filename(0)}), "wrong segment"),
        (lambda segment: segment.update({"segment_sha256": "bad"}), "hash"),
    ],
)
def test_alias_segment_rejects_invalid_shapes(mutation, message):
    artifacts = _alias_artifacts()
    root = artifacts.root
    segment = json.loads(json.dumps(next(iter(artifacts.segments.values()))))
    mutation(segment)

    resolver = SelectiveAliasResolver(PhysicalFilesystem(), JsonCodec(), HashService())
    with pytest.raises(SidecarValidationError, match=message):
        resolver._validate_segment(segment, "2-U-A", root)


def test_build_alias_index_rejects_invalid_chunk_and_entry_hashes():
    hs = HashService()
    jc = JsonCodec()

    with pytest.raises(SidecarValidationError, match="max_chunks"):
        build_alias_index_artifacts({}, hs.sha256(b"d"), {corpus_chunk_filename(i): hs.sha256(b"x") for i in range(501)}, hs, jc)
    with pytest.raises(SidecarValidationError, match="Invalid sidecar chunk hash"):
        build_alias_index_artifacts({"AA": corpus_chunk_filename(0)}, hs.sha256(b"d"), {corpus_chunk_filename(0): "bad"}, hs, jc, chunk_entry_counts={corpus_chunk_filename(0): 1})
    with pytest.raises(SidecarValidationError, match="entries_sha256"):
        build_alias_index_artifacts(
            {"AA": corpus_chunk_filename(0)},
            hs.sha256(b"d"),
            {corpus_chunk_filename(0): hs.sha256(b"x")},
            hs,
            jc,
            chunk_entry_counts={corpus_chunk_filename(0): 1},
            chunk_entries_sha256={corpus_chunk_filename(0): "bad"},
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda root: root.update({"format": "wrong"}), "format"),
        (lambda root: root.update({"schema_version": 99}), "schema"),
        (lambda root: root.update({"corpus_id": "bad"}), "corpus_id"),
        (lambda root: root.update({"segments": []}), "segments"),
        (lambda root: root.update({"segment_count": 99}), "segment_count"),
        (lambda root: next(iter(root["segments"].values())).update({"path": "../bad.json"}), "segment path"),
        (lambda root: next(iter(root["segments"].values())).update({"sha256": "bad"}), "segment hash"),
        (lambda root: root.update({"index_sha256": "bad"}), "hash"),
    ],
)
def test_content_search_index_rejects_invalid_roots(mutation, message):
    hs = HashService()
    jc = JsonCodec()
    artifacts = build_content_search_index_artifacts([("a.py.tknc", "alpha needle")], corpus_id=hs.sha256(b"corpus"), hash_service=hs, json_codec=jc)
    root = json.loads(json.dumps(artifacts.root))
    mutation(root)

    with pytest.raises(SidecarValidationError, match=message):
        validate_content_search_index(root, hash_service=hs, json_codec=jc, corpus_id=artifacts.root["corpus_id"])


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda segment: segment.update({"format": "wrong"}), "format"),
        (lambda segment: segment.update({"schema_version": 99}), "schema"),
        (lambda segment: segment.update({"corpus_id": "0" * 64}), "corpus_id"),
        (lambda segment: segment.update({"segment_id": "z"}), "id mismatch"),
        (lambda segment: segment.update({"terms": []}), "terms"),
        (lambda segment: segment["terms"].update({"zterm": ["a.py.tknc"]}), "wrong segment"),
        (lambda segment: segment["terms"].update({"alpha": "../bad.py"}), "postings"),
        (lambda segment: segment.update({"segment_sha256": "bad"}), "hash"),
    ],
)
def test_content_search_segment_rejects_invalid_shapes(mutation, message):
    hs = HashService()
    jc = JsonCodec()
    artifacts = build_content_search_index_artifacts([("a.py.tknc", "alpha needle")], corpus_id=hs.sha256(b"corpus"), hash_service=hs, json_codec=jc)
    segment = json.loads(json.dumps(next(iter(artifacts.segments.values()))))
    mutation(segment)

    with pytest.raises(SidecarValidationError, match=message):
        validate_content_search_segment(
            segment,
            segment_id="a",
            expected_sha256=next(iter(artifacts.segments.values()))["segment_sha256"],
            corpus_id=artifacts.root["corpus_id"],
            hash_service=hs,
            json_codec=jc,
        )
