import os
from pathlib import Path

from benchmarks.context_usage_compare import _build_tknc_corpus, _question_set, _search, _write_fixture_corpus
from cida.application.selective_alias_resolution import SelectiveAliasResolver
from cida.infrastructure.filesystem import PhysicalFilesystem
from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec


def test_tknc_search_detects_aliases_without_expected_alias_input(tmp_path):
    os.environ["TIKTOKEN_CACHE_DIR"] = str(Path(__file__).resolve().parent.parent / "resources")
    original, relpaths = _write_fixture_corpus(tmp_path, "search_tknc", 20)
    tknc = tmp_path / "tknc"
    _build_tknc_corpus(original, tknc, relpaths)

    question = _question_set()[-1]
    search = _search(tknc, question.question)
    resolver = SelectiveAliasResolver(PhysicalFilesystem(), JsonCodec(), HashService())
    aliases = set(resolver.locate_aliases(set(search.alias_candidates), str(tknc / "tknd")))
    resolved = resolver.resolve(aliases, str(tknc / "tknd"))

    assert search.files
    assert search.search_mode == "INDEXED"
    assert aliases
    assert resolved.resolved
    assert len(resolved.chunks_loaded) <= len(aliases)
