import json

import pytest

from cida.application.selective_alias_resolution import (
    ALIAS_INDEX_FILENAME,
    SelectiveAliasResolver,
    AliasDetector,
    build_alias_index_artifacts,
    build_alias_index_v2,
    corpus_chunk_filename,
)
from cida.domain.errors import SidecarValidationError
from cida.infrastructure.filesystem import PhysicalFilesystem
from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec


def _write_v3_chunk(tmp_path, entries: dict[str, str]):
    fs = PhysicalFilesystem()
    hs = HashService()
    jc = JsonCodec()
    dictionary_id = hs.sha256(b"dictionary")
    manifest_sha256 = hs.sha256(b"manifest")
    chunk_name = corpus_chunk_filename(0)
    tknd = tmp_path / "tknd"
    tknd.mkdir()
    sidecar = {
        "format": "cida-token-sidecar",
        "version": 2,
        "source": "corpus",
        "dictionary_id": dictionary_id,
        "manifest_sha256": manifest_sha256,
        "chunk_index": 0,
        "chunk_count": 1,
        "entries_sha256": hs.sha256(jc.canonical_encode(entries).encode("utf-8")),
        "entries": entries,
    }
    serialized = jc.encode(sidecar, indent=4)
    (tknd / chunk_name).write_text(serialized, encoding="utf-8", newline="\n")
    artifacts = build_alias_index_artifacts(
        {alias: chunk_name for alias in entries},
        dictionary_id,
        {chunk_name: hs.sha256(serialized.encode("utf-8"))},
        hs,
        jc,
        manifest_sha256=manifest_sha256,
        chunk_entry_counts={chunk_name: len(entries)},
        chunk_entries_sha256={chunk_name: sidecar["entries_sha256"]},
    )
    for segment_path, segment_data in artifacts.segments.items():
        full_segment = tknd / segment_path
        full_segment.parent.mkdir(parents=True, exist_ok=True)
        full_segment.write_text(jc.encode(segment_data, indent=4), encoding="utf-8", newline="\n")
    (tknd / ALIAS_INDEX_FILENAME).write_text(jc.encode(artifacts.root, indent=4), encoding="utf-8", newline="\n")
    return SelectiveAliasResolver(fs, jc, hs), tknd, chunk_name


def _write_v2_chunk(tmp_path, entries: dict[str, str]):
    resolver, tknd, chunk_name = _write_v3_chunk(tmp_path, entries)
    hs = HashService()
    jc = JsonCodec()
    sidecar = json.loads((tknd / chunk_name).read_text(encoding="utf-8"))
    index = build_alias_index_v2(
        {alias: chunk_name for alias in entries},
        sidecar["dictionary_id"],
        {chunk_name: hs.sha256((tknd / chunk_name).read_text(encoding="utf-8").encode("utf-8"))},
        hs,
        jc,
        manifest_sha256=sidecar["manifest_sha256"],
        chunk_entry_counts={chunk_name: len(entries)},
    )
    (tknd / ALIAS_INDEX_FILENAME).write_text(jc.encode(index, indent=4), encoding="utf-8", newline="\n")
    return resolver, tknd, chunk_name


def test_alias_index_v3_requires_manifest_dictionary_hashes_and_chunk_metadata(tmp_path):
    resolver, tknd, chunk_name = _write_v3_chunk(tmp_path, {"AA": "alpha", "AB": "beta"})

    index = json.loads((tknd / ALIAS_INDEX_FILENAME).read_text(encoding="utf-8"))
    assert index["format"] == "cida-alias-index"
    assert index["schema_version"] == 3
    assert index["membership"] == "EXACT_MEMBERSHIP"
    assert len(index["dictionary_id"]) == 64
    assert len(index["source_manifest_sha256"]) == 64
    assert index["chunks"][chunk_name]["sha256"]
    assert index["chunks"][chunk_name]["entry_count"] == 2
    assert index["chunks"][chunk_name]["entries_sha256"]
    assert index["segments"]
    assert "ranges" not in index
    assert "aliases" not in index

    assert resolver.resolve({"AA"}, str(tknd)).resolved == {"AA": "alpha"}


def test_alias_index_v2_compatibility_is_marked_approximate(tmp_path):
    resolver, tknd, _ = _write_v2_chunk(tmp_path, {"AA": "alpha"})

    result = resolver.resolve({"AA"}, str(tknd))

    assert result.resolved == {"AA": "alpha"}
    assert result.membership_mode == "APPROXIMATE_MEMBERSHIP"


def test_alias_index_rejects_chunk_from_another_dictionary(tmp_path):
    resolver, tknd, chunk_name = _write_v3_chunk(tmp_path, {"AA": "alpha"})
    sidecar = json.loads((tknd / chunk_name).read_text(encoding="utf-8"))
    sidecar["dictionary_id"] = "b" * 64
    serialized = json.dumps(sidecar, indent=4)
    (tknd / chunk_name).write_text(serialized, encoding="utf-8", newline="\n")

    with pytest.raises(SidecarValidationError, match="hash mismatch|dictionary_id mismatch"):
        resolver.resolve({"AA"}, str(tknd))


def test_alias_detector_uses_index_ranges_and_ignores_strings():
    index = {
        "ranges": [
            {"first_alias": "AA", "last_alias": "AZ", "path": corpus_chunk_filename(0)},
        ]
    }

    detector = AliasDetector()

    assert detector.detect("", index) == set()
    assert detector.detect("AA real 'AB string' AZ", index) == {"AA", "AZ"}
    assert detector.detect("BA outside range", index) == set()
    assert detector.detect("AA", {"ranges": "bad"}) == set()


def test_chunk_filename_rejects_negative_index():
    with pytest.raises(ValueError, match="non-negative"):
        corpus_chunk_filename(-1)


def test_build_alias_index_rejects_invalid_writer_inputs():
    hs = HashService()
    jc = JsonCodec()
    chunk_name = corpus_chunk_filename(0)
    digest = hs.sha256(b"chunk")
    dictionary_id = hs.sha256(b"dictionary")
    manifest_sha256 = hs.sha256(b"manifest")

    with pytest.raises(SidecarValidationError, match="dictionary_id"):
        build_alias_index_artifacts({"AA": chunk_name}, "not-a-sha", {chunk_name: digest}, hs, jc, chunk_entry_counts={chunk_name: 1})

    with pytest.raises(SidecarValidationError, match="manifest_sha256"):
        build_alias_index_artifacts(
            {"AA": chunk_name},
            dictionary_id,
            {chunk_name: digest},
            hs,
            jc,
            manifest_sha256="not-a-sha",
            chunk_entry_counts={chunk_name: 1},
        )

    with pytest.raises(SidecarValidationError, match="Malformed alias"):
        build_alias_index_artifacts(
            {"../AA": chunk_name},
            dictionary_id,
            {chunk_name: digest},
            hs,
            jc,
            manifest_sha256=manifest_sha256,
            chunk_entry_counts={chunk_name: 1},
        )

    with pytest.raises(SidecarValidationError, match="entry_count"):
        build_alias_index_artifacts(
            {"AA": chunk_name},
            dictionary_id,
            {chunk_name: digest},
            hs,
            jc,
            manifest_sha256=manifest_sha256,
            chunk_entry_counts={},
        )

    with pytest.raises(SidecarValidationError, match="metadata mismatch"):
        build_alias_index_artifacts(
            {"AA": chunk_name},
            dictionary_id,
            {chunk_name: digest, corpus_chunk_filename(1): hs.sha256(b"extra")},
            hs,
            jc,
            manifest_sha256=manifest_sha256,
            chunk_entry_counts={chunk_name: 1, corpus_chunk_filename(1): 0},
        )


def test_resolver_rejects_structurally_invalid_index_variants(tmp_path):
    resolver, tknd, _ = _write_v3_chunk(tmp_path, {"AA": "alpha"})
    index_path = tknd / ALIAS_INDEX_FILENAME
    valid = json.loads(index_path.read_text(encoding="utf-8"))

    mutations = [
        ("format", "wrong", "format"),
        ("schema_version", 99, "schema"),
        ("alias_count", -1, "alias_count"),
        ("chunk_count", 2, "chunk_count"),
        ("segments", "bad", "segments"),
        ("index_sha256", "bad", "hash"),
    ]
    for key, value, message in mutations:
        data = dict(valid)
        data[key] = value
        index_path.write_text(json.dumps(data), encoding="utf-8", newline="\n")
        with pytest.raises(SidecarValidationError, match=message):
            resolver.resolve({"AA"}, str(tknd))


def test_resolver_rejects_bad_range_shapes(tmp_path):
    resolver, tknd, _ = _write_v2_chunk(tmp_path, {"AA": "alpha"})
    index_path = tknd / ALIAS_INDEX_FILENAME
    valid = json.loads(index_path.read_text(encoding="utf-8"))

    bad_ranges = [
        [{"first_alias": "AZ", "last_alias": "AA", "path": corpus_chunk_filename(0)}],
        [{"first_alias": "../AA", "last_alias": "AZ", "path": corpus_chunk_filename(0)}],
        [{"first_alias": "AA", "last_alias": "AZ", "path": 123}],
        [{"first_alias": "AA", "last_alias": "AZ", "path": corpus_chunk_filename(9)}],
    ]
    for ranges in bad_ranges:
        data = dict(valid)
        data["ranges"] = ranges
        index_path.write_text(json.dumps(data), encoding="utf-8", newline="\n")
        with pytest.raises(SidecarValidationError):
            resolver.resolve({"AA"}, str(tknd))
