import json

from cida.application.optimize_corpus import CorpusOptimizerUsecase
from cida.application.selective_alias_resolution import ALIAS_INDEX_FILENAME, SelectiveAliasResolver, corpus_chunk_filename
from cida.infrastructure.filesystem import PhysicalFilesystem
from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec
from cida.markdown.dictionary import generate_alias_candidates


class _TokenCounter:
    def count(self, text: str, content_hash: str | None = None) -> int:
        return max(1, len(text) // 4)


def _corpus_dict(alias_count: int) -> dict[str, str]:
    aliases = generate_alias_candidates(set(), limit=alias_count)
    return {f"word_{i:06d}": alias for i, alias in enumerate(aliases)}


def test_corpus_chunk_filename_contract_for_required_scales():
    for alias_count in (1, 500, 501, 3_000, 3_001, 10_000, 100_000):
        chunk_count = (alias_count + 499) // 500
        filenames = [corpus_chunk_filename(index) for index in range(chunk_count)]

        assert len(filenames) == len(set(filenames))
        assert filenames == sorted(filenames)
        assert filenames[0] == "chunk-000000.cidatkn"
        assert all(name.startswith("chunk-") and name.endswith(".cidatkn") for name in filenames)


def test_writer_generates_unique_chunks_and_roundtrip_beyond_three_thousand_aliases(tmp_path):
    fs = PhysicalFilesystem()
    hs = HashService()
    jc = JsonCodec()
    usecase = CorpusOptimizerUsecase(_TokenCounter(), fs, hs, jc, dictionary_builder=None)
    corpus_dict = _corpus_dict(3_001)
    manifest_sha256 = hs.sha256(b"manifest")

    usecase.write_corpus_sidecars(corpus_dict, manifest_sha256, str(tmp_path))

    tknd = tmp_path / "tknd"
    chunk_files = sorted(path.name for path in tknd.glob("*.cidatkn"))
    assert chunk_files == [corpus_chunk_filename(index) for index in range(7)]
    assert len(chunk_files) == len(set(chunk_files))

    index = json.loads((tknd / ALIAS_INDEX_FILENAME).read_text(encoding="utf-8"))
    assert index["alias_count"] == 3_001
    assert index["chunk_count"] == 7
    assert index["chunks"][corpus_chunk_filename(6)]["entry_count"] == 1

    resolver = SelectiveAliasResolver(fs, jc, hs)
    target_alias = generate_alias_candidates(set(), limit=3_001)[3_000]
    result = resolver.resolve({target_alias}, str(tknd))
    assert result.resolved == {target_alias: "word_003000"}
    assert result.chunks_loaded == (corpus_chunk_filename(6),)
