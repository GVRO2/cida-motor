from pathlib import Path

from cida.application.selective_alias_resolution import ALIAS_INDEX_FILENAME, build_alias_index_artifacts, corpus_chunk_filename
from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec
from cida.infrastructure.tknc_context_session import ContextFilesystem, TkncContextSession
from cida.infrastructure.tokenizer import OfflineTokenizer


COMMON_IDENTIFIERS = {"if", "as", "in", "is", "go", "do", "id", "io", "os", "re", "to", "my", "no", "on", "or"}


def _write_tknd(root: Path, entries: dict[str, str]) -> Path:
    tknd = root / "tknd"
    tknd.mkdir(parents=True)
    hs = HashService()
    jc = JsonCodec()
    manifest_sha = hs.sha256(b"manifest")
    dictionary_id = hs.sha256(b"dictionary")
    chunk_name = corpus_chunk_filename(0)
    entries_sha = hs.sha256(jc.canonical_encode(entries).encode("utf-8"))
    sidecar = {
        "format": "cida-token-sidecar",
        "version": 2,
        "source": "corpus",
        "dictionary_id": dictionary_id,
        "manifest_sha256": manifest_sha,
        "chunk_index": 0,
        "chunk_count": 1,
        "entries_sha256": entries_sha,
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
        manifest_sha256=manifest_sha,
        chunk_entry_counts={chunk_name: len(entries)},
        chunk_entries_sha256={chunk_name: entries_sha},
    )
    for segment_path, segment in artifacts.segments.items():
        full_segment = tknd / segment_path
        full_segment.parent.mkdir(parents=True, exist_ok=True)
        full_segment.write_text(jc.encode(segment, indent=4), encoding="utf-8", newline="\n")
    (tknd / ALIAS_INDEX_FILENAME).write_text(jc.encode(artifacts.root, indent=4), encoding="utf-8", newline="\n")
    manifest = {"format": "cida-corpus-manifest", "schema_version": 1, "files": []}
    manifest["manifest_sha256"] = manifest_sha
    (root / "tknc-manifest.json").write_text(jc.encode(manifest, indent=4), encoding="utf-8", newline="\n")
    return tknd


def test_common_identifiers_are_not_aliases_without_exact_membership(tmp_path):
    _write_tknd(tmp_path, {"AA": "alpha"})
    fs = ContextFilesystem()
    session = TkncContextSession(tmp_path, fs, JsonCodec(), HashService(), OfflineTokenizer())

    found = session.aliases_in_index(COMMON_IDENTIFIERS, query_id="common")
    chunks = session.required_chunks(COMMON_IDENTIFIERS, query_id="common")

    assert found == set()
    assert chunks == tuple()
    assert not any(event.artifact_type == "sidecar" for event in fs.reads)


def test_real_alias_resolves_under_exact_membership(tmp_path):
    _write_tknd(tmp_path, {"AA": "alpha"})
    fs = ContextFilesystem()
    session = TkncContextSession(tmp_path, fs, JsonCodec(), HashService(), OfflineTokenizer())

    assert session.aliases_in_index({"AA"}, query_id="real") == {"AA"}
    assert session.required_chunks({"AA"}, query_id="real") == (corpus_chunk_filename(0),)
