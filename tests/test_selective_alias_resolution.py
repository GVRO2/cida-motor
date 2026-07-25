import json

import pytest

from cida.application.selective_alias_resolution import (
    ALIAS_INDEX_FILENAME,
    SelectiveAliasResolver,
    build_alias_index,
)
from cida.domain.errors import SidecarValidationError
from cida.infrastructure.filesystem import PhysicalFilesystem
from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec


def _sidecar(entries: dict[str, str], digest: str = "a" * 64) -> dict:
    return {
        "format": "cida-token-sidecar",
        "version": 1,
        "source": "corpus",
        "source_sha256": digest,
        "entries": entries,
    }


def _write_indexed_tknd(tmp_path, chunks: dict[str, dict[str, str]]) -> tuple[SelectiveAliasResolver, object]:
    tknd = tmp_path / "tknd"
    tknd.mkdir()
    fs = PhysicalFilesystem()
    hs = HashService()
    jc = JsonCodec()
    alias_to_chunk = {}
    chunk_hashes = {}
    for chunk_name, entries in chunks.items():
        serialized = jc.encode(_sidecar(entries), indent=4)
        (tknd / chunk_name).write_text(serialized, encoding="utf-8", newline="\n")
        chunk_hashes[chunk_name] = hs.sha256(serialized.encode("utf-8"))
        for alias in entries:
            alias_to_chunk[alias] = chunk_name
    index_data = build_alias_index(alias_to_chunk, "dict-1", chunk_hashes, hs, jc)
    (tknd / ALIAS_INDEX_FILENAME).write_text(jc.encode(index_data, indent=4), encoding="utf-8", newline="\n")
    return SelectiveAliasResolver(fs, jc, hs), tknd


def test_lookup_one_alias_opens_one_chunk(tmp_path):
    resolver, tknd = _write_indexed_tknd(
        tmp_path,
        {
            "A0.cidatkn": {"AA": "alpha", "AB": "beta"},
            "B0.cidatkn": {"BA": "gamma"},
        },
    )

    result = resolver.resolve({"AA"}, str(tknd))

    assert result.resolved == {"AA": "alpha"}
    assert result.chunks_loaded == ("A0.cidatkn",)
    assert result.entries_loaded == 2


def test_lookup_aliases_in_same_chunk_loads_one_chunk(tmp_path):
    resolver, tknd = _write_indexed_tknd(
        tmp_path,
        {
            "A0.cidatkn": {"AA": "alpha", "AB": "beta"},
            "B0.cidatkn": {"BA": "gamma"},
        },
    )

    result = resolver.resolve({"AA", "AB"}, str(tknd))

    assert result.resolved == {"AA": "alpha", "AB": "beta"}
    assert result.chunks_loaded == ("A0.cidatkn",)


def test_lookup_aliases_in_different_chunks_loads_only_needed_chunks(tmp_path):
    resolver, tknd = _write_indexed_tknd(
        tmp_path,
        {
            "A0.cidatkn": {"AA": "alpha"},
            "B0.cidatkn": {"BA": "gamma"},
            "C0.cidatkn": {"CA": "delta"},
        },
    )

    result = resolver.resolve({"AA", "CA"}, str(tknd))

    assert result.resolved == {"AA": "alpha", "CA": "delta"}
    assert result.chunks_loaded == ("A0.cidatkn", "C0.cidatkn")


def test_alias_absent_does_not_load_sidecar_chunks(tmp_path):
    resolver, tknd = _write_indexed_tknd(tmp_path, {"A0.cidatkn": {"AA": "alpha"}})

    result = resolver.resolve({"ZZ"}, str(tknd))

    assert result.resolved == {}
    assert result.unresolved == {"ZZ"}
    assert result.chunks_loaded == tuple()


def test_chunk_missing_fails(tmp_path):
    resolver, tknd = _write_indexed_tknd(tmp_path, {"A0.cidatkn": {"AA": "alpha"}})
    (tknd / "A0.cidatkn").unlink()

    with pytest.raises(SidecarValidationError, match="missing"):
        resolver.resolve({"AA"}, str(tknd))


def test_chunk_hash_mismatch_fails(tmp_path):
    resolver, tknd = _write_indexed_tknd(tmp_path, {"A0.cidatkn": {"AA": "alpha"}})
    (tknd / "A0.cidatkn").write_text(json.dumps(_sidecar({"AA": "changed"})), encoding="utf-8", newline="\n")

    with pytest.raises(SidecarValidationError, match="hash mismatch"):
        resolver.resolve({"AA"}, str(tknd))


def test_index_hash_mismatch_fails(tmp_path):
    resolver, tknd = _write_indexed_tknd(tmp_path, {"A0.cidatkn": {"AA": "alpha"}})
    data = json.loads((tknd / ALIAS_INDEX_FILENAME).read_text(encoding="utf-8"))
    data["aliases"]["AA"] = "B0.cidatkn"
    (tknd / ALIAS_INDEX_FILENAME).write_text(json.dumps(data), encoding="utf-8", newline="\n")

    with pytest.raises(SidecarValidationError, match="index hash mismatch"):
        resolver.resolve({"AA"}, str(tknd))


def test_duplicate_alias_between_loaded_chunks_fails(tmp_path):
    resolver, tknd = _write_indexed_tknd(
        tmp_path,
        {
            "A0.cidatkn": {"AA": "alpha"},
            "B0.cidatkn": {"BA": "gamma"},
        },
    )
    data = json.loads((tknd / "B0.cidatkn").read_text(encoding="utf-8"))
    data["entries"]["AA"] = "other"
    serialized = json.dumps(data, indent=4)
    (tknd / "B0.cidatkn").write_text(serialized, encoding="utf-8", newline="\n")

    jc = JsonCodec()
    hs = HashService()
    index_data = json.loads((tknd / ALIAS_INDEX_FILENAME).read_text(encoding="utf-8"))
    index_data["chunks"]["B0.cidatkn"]["sha256"] = hs.sha256(serialized.encode("utf-8"))
    payload = {
        "schema_version": index_data["schema_version"],
        "dictionary_id": index_data["dictionary_id"],
        "aliases": index_data["aliases"],
        "chunks": index_data["chunks"],
    }
    index_data["index_sha256"] = hs.sha256(jc.canonical_encode(payload).encode("utf-8"))
    (tknd / ALIAS_INDEX_FILENAME).write_text(json.dumps(index_data), encoding="utf-8", newline="\n")

    with pytest.raises(SidecarValidationError, match="Duplicate alias"):
        resolver.resolve({"AA", "BA"}, str(tknd))


def test_malformed_alias_rejected(tmp_path):
    resolver, tknd = _write_indexed_tknd(tmp_path, {"A0.cidatkn": {"AA": "alpha"}})

    with pytest.raises(SidecarValidationError, match="Malformed alias"):
        resolver.resolve({"../AA"}, str(tknd))


def test_sidecar_size_limit_fails(tmp_path):
    resolver, tknd = _write_indexed_tknd(tmp_path, {"A0.cidatkn": {"AA": "alpha"}})
    resolver.max_sidecar_bytes = 8

    with pytest.raises(SidecarValidationError, match="size limit"):
        resolver.resolve({"AA"}, str(tknd))


def test_large_dictionary_lookup_still_loads_one_chunk(tmp_path):
    chunks = {
        "A0.cidatkn": {f"A{i}": f"alpha_{i}" for i in range(200)},
        "B0.cidatkn": {f"B{i}": f"beta_{i}" for i in range(200)},
        "C0.cidatkn": {f"C{i}": f"gamma_{i}" for i in range(200)},
    }
    resolver, tknd = _write_indexed_tknd(tmp_path, chunks)

    result = resolver.resolve({"B42"}, str(tknd))

    assert result.resolved == {"B42": "beta_42"}
    assert result.chunks_loaded == ("B0.cidatkn",)
    assert result.entries_loaded == 200
