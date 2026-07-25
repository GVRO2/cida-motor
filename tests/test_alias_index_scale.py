from cida.application.selective_alias_resolution import build_alias_index, corpus_chunk_filename
from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec


def test_alias_index_v2_stays_compact_for_one_hundred_thousand_aliases():
    hs = HashService()
    jc = JsonCodec()
    alias_to_chunk = {}
    chunk_hashes = {}
    chunk_entry_counts = {}
    for i in range(100_000):
        chunk_name = corpus_chunk_filename(i // 500)
        alias_to_chunk[f"A{i:06d}"] = chunk_name
        chunk_entry_counts[chunk_name] = chunk_entry_counts.get(chunk_name, 0) + 1
    for chunk_name in chunk_entry_counts:
        chunk_hashes[chunk_name] = hs.sha256(chunk_name.encode("utf-8"))

    index = build_alias_index(
        alias_to_chunk,
        hs.sha256(b"dictionary"),
        chunk_hashes,
        hs,
        jc,
        manifest_sha256=hs.sha256(b"manifest"),
        chunk_entry_counts=chunk_entry_counts,
    )
    encoded = jc.encode(index, indent=4).encode("utf-8")

    assert index["alias_count"] == 100_000
    assert index["chunk_count"] == 200
    assert len(index["ranges"]) == 200
    assert len(encoded) < 2_000_000
    assert "aliases" not in index
