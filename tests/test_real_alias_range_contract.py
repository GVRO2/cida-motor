from pathlib import Path

from cida.application.optimize_corpus import CorpusOptimizerUsecase
from cida.application.selective_alias_resolution import ALIAS_INDEX_FILENAME, SelectiveAliasResolver
from cida.infrastructure.filesystem import PhysicalFilesystem
from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec
from cida.infrastructure.tokenizer import OfflineTokenizer
from cida.markdown.dictionary import generate_alias_candidates

ROOT = Path(__file__).resolve().parent.parent


class _UnusedDictionaryBuilder:
    def build_corpus_dictionary(self, all_files_content, token_counter, min_margin=5):
        return {}


def _write_real_alias_product_chunks(tmp_path, alias_count: int):
    aliases = generate_alias_candidates(set(), limit=alias_count)
    corpus_dict = {f"real_alias_word_{index:05d}": alias for index, alias in enumerate(aliases)}
    usecase = CorpusOptimizerUsecase(
        OfflineTokenizer(),
        PhysicalFilesystem(),
        HashService(),
        JsonCodec(),
        _UnusedDictionaryBuilder(),
    )
    manifest_sha = HashService().sha256(f"manifest-{alias_count}".encode("utf-8"))
    usecase.write_corpus_sidecars(corpus_dict, manifest_sha, str(tmp_path))
    return aliases, tmp_path / "tknd"


def test_real_generated_alias_membership_resolves_across_generator_transitions(tmp_path):
    for alias_count in (676, 677, 1352, 1353, 2000, 5000, 10000):
        aliases, tknd = _write_real_alias_product_chunks(tmp_path / f"case-{alias_count}", alias_count)
        index = JsonCodec().decode((tknd / ALIAS_INDEX_FILENAME).read_text(encoding="utf-8"))
        resolver = SelectiveAliasResolver(
            PhysicalFilesystem(),
            JsonCodec(),
            HashService(),
            OfflineTokenizer(cache_dir=str(ROOT / "resources")),
        )

        resolved = resolver.resolve(set(aliases), str(tknd))

        assert index["schema_version"] == 3
        assert index["membership"] == "EXACT_MEMBERSHIP"
        assert set(resolved.resolved) == set(aliases)
        assert resolved.unresolved == set()
        assert len(resolved.chunks_loaded) == index["chunk_count"]
        assert resolved.segments_loaded
        assert "ZZ" in aliases
        if alias_count >= 677:
            assert "aa" in aliases
        if alias_count >= 1352:
            assert "zz" in aliases
        if alias_count >= 1353:
            assert "AAA" in aliases
        assert "ranges" not in index
