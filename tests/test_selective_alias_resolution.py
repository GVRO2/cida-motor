import json

import pytest

from cida.application.selective_alias_resolution import (
    ALIAS_INDEX_FILENAME,
    SelectiveAliasResolver,
    build_alias_index,
    corpus_chunk_filename,
)
from cida.domain.errors import SidecarValidationError
from cida.infrastructure.filesystem import PhysicalFilesystem
from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec


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
    chunk_count = len(chunks)
    for chunk_index, (chunk_name, entries) in enumerate(chunks.items()):
        serialized = jc.encode(_sidecar(entries, hs, jc, dictionary_id, manifest_sha256, chunk_index, chunk_count), indent=4)
        (tknd / chunk_name).write_text(serialized, encoding="utf-8", newline="\n")
        chunk_hashes[chunk_name] = hs.sha256(serialized.encode("utf-8"))
        chunk_entry_counts[chunk_name] = len(entries)
        for alias in entries:
            alias_to_chunk[alias] = chunk_name
    index_data = build_alias_index(
        alias_to_chunk,
        dictionary_id,
        chunk_hashes,
        hs,
        jc,
        manifest_sha256=manifest_sha256,
        chunk_entry_counts=chunk_entry_counts,
    )
    (tknd / ALIAS_INDEX_FILENAME).write_text(jc.encode(index_data, indent=4), encoding="utf-8", newline="\n")
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
        index_data["manifest_sha256"],
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

    jc = JsonCodec()
    hs = HashService()
    index_data = json.loads((tknd / ALIAS_INDEX_FILENAME).read_text(encoding="utf-8"))
    index_data["chunks"][corpus_chunk_filename(1)]["sha256"] = hs.sha256(serialized.encode("utf-8"))
    payload = {key: index_data[key] for key in ("format", "schema_version", "dictionary_id", "manifest_sha256", "alias_count", "chunk_count", "ranges", "chunks")}
    index_data["index_sha256"] = hs.sha256(jc.canonical_encode(payload).encode("utf-8"))
    (tknd / ALIAS_INDEX_FILENAME).write_text(json.dumps(index_data), encoding="utf-8", newline="\n")

    with pytest.raises(SidecarValidationError, match="entries_sha256 mismatch|entry_count mismatch"):
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
    chunks = {
        corpus_chunk_filename(0): {f"A{i}": f"alpha_{i}" for i in range(200)},
        corpus_chunk_filename(1): {f"B{i}": f"beta_{i}" for i in range(200)},
        corpus_chunk_filename(2): {f"C{i}": f"gamma_{i}" for i in range(200)},
    }
    resolver, tknd = _write_indexed_tknd(tmp_path, chunks)

    result = resolver.resolve({"B42"}, str(tknd))

    assert result.resolved == {"B42": "beta_42"}
    assert result.chunks_loaded == (corpus_chunk_filename(1),)
    assert result.entries_loaded == 200
