import json

import pytest

from cida.application.selective_alias_resolution import (
    ALIAS_INDEX_FILENAME,
    SelectiveAliasResolver,
    build_alias_index_artifacts,
    corpus_chunk_filename,
)
from cida.domain.errors import SidecarValidationError
from cida.infrastructure.filesystem import PhysicalFilesystem
from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec
from cida.markdown.dictionary import generate_alias_candidates


def _sidecar(
    entries: dict[str, str],
    hs: HashService,
    jc: JsonCodec,
    dictionary_id: str,
    manifest_sha256: str,
    chunk_index: int = 0,
    chunk_count: int = 1,
) -> dict:
    return {
        "format": "cida-token-sidecar",
        "version": 2,
        "source": "corpus",
        "dictionary_id": dictionary_id,
        "manifest_sha256": manifest_sha256,
        "chunk_index": chunk_index,
        "chunk_count": chunk_count,
        "entries_sha256": hs.sha256(jc.canonical_encode(entries).encode("utf-8")),
        "entries": entries,
    }


def _write_indexed_tknd(tmp_path, chunks: dict[str, dict[str, str]]) -> tuple[SelectiveAliasResolver, object]:
    tknd = tmp_path / "tknd"
    tknd.mkdir()
    fs = PhysicalFilesystem()
    hs = HashService()
    jc = JsonCodec()
    dictionary_id = hs.sha256(b"dictionary")
    manifest_sha256 = hs.sha256(b"manifest")
    alias_to_chunk = {}
    chunk_hashes = {}
    chunk_entry_counts = {}
    chunk_entries_sha256 = {}
    chunk_count = len(chunks)
    for chunk_index, (chunk_name, entries) in enumerate(chunks.items()):
        entries_sha = hs.sha256(jc.canonical_encode(entries).encode("utf-8"))
        serialized = jc.encode(_sidecar(entries, hs, jc, dictionary_id, manifest_sha256, chunk_index, chunk_count), indent=4)
        (tknd / chunk_name).write_text(serialized, encoding="utf-8", newline="\n")
        chunk_hashes[chunk_name] = hs.sha256(serialized.encode("utf-8"))
        chunk_entry_counts[chunk_name] = len(entries)
        chunk_entries_sha256[chunk_name] = entries_sha
        for alias in entries:
            alias_to_chunk[alias] = chunk_name
    artifacts = build_alias_index_artifacts(
        alias_to_chunk,
        dictionary_id,
        chunk_hashes,
        hs,
        jc,
        manifest_sha256=manifest_sha256,
        chunk_entry_counts=chunk_entry_counts,
        chunk_entries_sha256=chunk_entries_sha256,
    )
    for segment_path, segment_data in artifacts.segments.items():
        full_segment = tknd / segment_path
        full_segment.parent.mkdir(parents=True, exist_ok=True)
        full_segment.write_text(jc.encode(segment_data, indent=4), encoding="utf-8", newline="\n")
    (tknd / ALIAS_INDEX_FILENAME).write_text(jc.encode(artifacts.root, indent=4), encoding="utf-8", newline="\n")
    return SelectiveAliasResolver(fs, jc, hs), tknd


def test_lookup_one_alias_opens_one_chunk(tmp_path):
    resolver, tknd = _write_indexed_tknd(
        tmp_path,
        {
            corpus_chunk_filename(0): {"AA": "alpha", "AB": "beta"},
            corpus_chunk_filename(1): {"BA": "gamma"},
        },
    )

    result = resolver.resolve({"AA"}, str(tknd))

    assert result.resolved == {"AA": "alpha"}
    assert result.chunks_loaded == (corpus_chunk_filename(0),)
    assert result.entries_loaded == 2


def test_lookup_aliases_in_same_chunk_loads_one_chunk(tmp_path):
    resolver, tknd = _write_indexed_tknd(
        tmp_path,
        {
            corpus_chunk_filename(0): {"AA": "alpha", "AB": "beta"},
            corpus_chunk_filename(1): {"BA": "gamma"},
        },
    )

    result = resolver.resolve({"AA", "AB"}, str(tknd))

    assert result.resolved == {"AA": "alpha", "AB": "beta"}
    assert result.chunks_loaded == (corpus_chunk_filename(0),)


def test_lookup_aliases_in_different_chunks_loads_only_needed_chunks(tmp_path):
    resolver, tknd = _write_indexed_tknd(
        tmp_path,
        {
            corpus_chunk_filename(0): {"AA": "alpha"},
            corpus_chunk_filename(1): {"BA": "gamma"},
            corpus_chunk_filename(2): {"CA": "delta"},
        },
    )

    result = resolver.resolve({"AA", "CA"}, str(tknd))

    assert result.resolved == {"AA": "alpha", "CA": "delta"}
    assert result.chunks_loaded == (corpus_chunk_filename(0), corpus_chunk_filename(2))


def test_alias_absent_does_not_load_sidecar_chunks(tmp_path):
    resolver, tknd = _write_indexed_tknd(tmp_path, {corpus_chunk_filename(0): {"AA": "alpha"}})

    result = resolver.resolve({"ZZ"}, str(tknd))

    assert result.resolved == {}
    assert result.unresolved == {"ZZ"}
    assert result.chunks_loaded == tuple()


def test_chunk_missing_fails(tmp_path):
    resolver, tknd = _write_indexed_tknd(tmp_path, {corpus_chunk_filename(0): {"AA": "alpha"}})
    (tknd / corpus_chunk_filename(0)).unlink()

    with pytest.raises(SidecarValidationError, match="missing"):
        resolver.resolve({"AA"}, str(tknd))


def test_chunk_hash_mismatch_fails(tmp_path):
    resolver, tknd = _write_indexed_tknd(tmp_path, {corpus_chunk_filename(0): {"AA": "alpha"}})
    hs = HashService()
    jc = JsonCodec()
    index_data = json.loads((tknd / ALIAS_INDEX_FILENAME).read_text(encoding="utf-8"))
    changed = _sidecar(
        {"AA": "changed"},
        hs,
        jc,
        index_data["dictionary_id"],
        index_data["source_manifest_sha256"],
    )
    (tknd / corpus_chunk_filename(0)).write_text(json.dumps(changed), encoding="utf-8", newline="\n")

    with pytest.raises(SidecarValidationError, match="hash mismatch"):
        resolver.resolve({"AA"}, str(tknd))


def test_index_hash_mismatch_fails(tmp_path):
    resolver, tknd = _write_indexed_tknd(tmp_path, {corpus_chunk_filename(0): {"AA": "alpha"}})
    data = json.loads((tknd / ALIAS_INDEX_FILENAME).read_text(encoding="utf-8"))
    data["dictionary_id"] = "b" * 64
    (tknd / ALIAS_INDEX_FILENAME).write_text(json.dumps(data), encoding="utf-8", newline="\n")

    with pytest.raises(SidecarValidationError, match="index hash mismatch"):
        resolver.resolve({"AA"}, str(tknd))


def test_duplicate_alias_between_loaded_chunks_fails(tmp_path):
    resolver, tknd = _write_indexed_tknd(
        tmp_path,
        {
            corpus_chunk_filename(0): {"AA": "alpha"},
            corpus_chunk_filename(1): {"BA": "gamma"},
        },
    )
    data = json.loads((tknd / corpus_chunk_filename(1)).read_text(encoding="utf-8"))
    data["entries"]["AA"] = "other"
    data["entries_sha256"] = HashService().sha256(JsonCodec().canonical_encode(data["entries"]).encode("utf-8"))
    serialized = json.dumps(data, indent=4)
    (tknd / corpus_chunk_filename(1)).write_text(serialized, encoding="utf-8", newline="\n")

    with pytest.raises(SidecarValidationError, match="hash mismatch|entries_sha256 mismatch|entry_count mismatch"):
        resolver.resolve({"AA", "BA"}, str(tknd))


def test_malformed_alias_rejected(tmp_path):
    resolver, tknd = _write_indexed_tknd(tmp_path, {corpus_chunk_filename(0): {"AA": "alpha"}})

    with pytest.raises(SidecarValidationError, match="Malformed alias"):
        resolver.resolve({"../AA"}, str(tknd))


def test_sidecar_size_limit_fails(tmp_path):
    resolver, tknd = _write_indexed_tknd(tmp_path, {corpus_chunk_filename(0): {"AA": "alpha"}})
    resolver.max_sidecar_bytes = 8

    with pytest.raises(SidecarValidationError, match="size limit"):
        resolver.resolve({"AA"}, str(tknd))


def test_large_dictionary_lookup_still_loads_one_chunk(tmp_path):
    aliases = generate_alias_candidates(set(), limit=600)
    chunks = {
        corpus_chunk_filename(0): {alias: f"alpha_{i}" for i, alias in enumerate(aliases[:200])},
        corpus_chunk_filename(1): {alias: f"beta_{i}" for i, alias in enumerate(aliases[200:400])},
        corpus_chunk_filename(2): {alias: f"gamma_{i}" for i, alias in enumerate(aliases[400:600])},
    }
    resolver, tknd = _write_indexed_tknd(tmp_path, chunks)
    target_alias = aliases[242]

    result = resolver.resolve({target_alias}, str(tknd))

    assert result.resolved == {target_alias: "beta_42"}
    assert result.chunks_loaded == (corpus_chunk_filename(1),)
    assert result.entries_loaded == 200
