from cida.application.selective_alias_resolution import build_alias_index_artifacts, corpus_chunk_filename
from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec
from cida.markdown.dictionary import generate_alias_candidates


def test_alias_index_v3_root_stays_compact_for_one_hundred_thousand_aliases():
    hs = HashService()
    jc = JsonCodec()
    aliases = generate_alias_candidates(set(), limit=100_000)
    alias_to_chunk = {}
    chunk_hashes = {}
    chunk_entry_counts = {}
    for i, alias in enumerate(aliases):
        chunk_name = corpus_chunk_filename(i // 500)
        alias_to_chunk[alias] = chunk_name
        chunk_entry_counts[chunk_name] = chunk_entry_counts.get(chunk_name, 0) + 1
    for chunk_name in chunk_entry_counts:
        chunk_hashes[chunk_name] = hs.sha256(chunk_name.encode("utf-8"))

    artifacts = build_alias_index_artifacts(
        alias_to_chunk,
        hs.sha256(b"dictionary"),
        chunk_hashes,
        hs,
        jc,
        manifest_sha256=hs.sha256(b"manifest"),
        chunk_entry_counts=chunk_entry_counts,
    )
    index = artifacts.root
    encoded = jc.encode(index, indent=4).encode("utf-8")

    assert index["schema_version"] == 3
    assert index["membership"] == "EXACT_MEMBERSHIP"
    assert index["alias_count"] == 100_000
    assert index["chunk_count"] == 200
    assert index["segment_count"] == len(index["segments"])
    assert artifacts.segments
    assert len(encoded) < 2_000_000
    assert "aliases" not in index
    assert "ranges" not in index
