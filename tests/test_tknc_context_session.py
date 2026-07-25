from benchmarks.context_usage_compare import _build_tknc_corpus, _question_set, _read_selected, _write_fixture_corpus
from cida.infrastructure.tknc_context_session import ContextFilesystem, TkncContextSession
from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec
from cida.infrastructure.tokenizer import OfflineTokenizer


def test_warm_session_reuses_index_manifest_and_chunk_cache(tmp_path):
    original, relpaths = _write_fixture_corpus(tmp_path, "warm", 900)
    tknc = tmp_path / "warm" / "tknc"
    _build_tknc_corpus(original, tknc, relpaths)
    tokenizer = OfflineTokenizer()
    fs = ContextFilesystem()
    session = TkncContextSession(tknc, fs, JsonCodec(), HashService(), tokenizer)
    question = _question_set()[-1]

    first = session.search(question.question, query_id="Q1")
    _read_selected(tknc, first.files, fs, query_id="Q1", reason="test_selected")
    aliases = session.aliases_in_index(set(first.alias_candidates), query_id="Q1")
    first_resolution = session.resolve(aliases, query_id="Q1")
    first_physical_reads = sum(1 for event in fs.reads if not event.cache_hit)

    second = session.search(question.question, query_id="Q2")
    _read_selected(tknc, second.files, fs, query_id="Q2", reason="test_selected")
    second_aliases = session.aliases_in_index(set(second.alias_candidates), query_id="Q2")
    second_resolution = session.resolve(second_aliases, query_id="Q2")
    second_physical_reads = sum(1 for event in fs.reads if not event.cache_hit)

    assert first_resolution.resolved
    assert second_resolution.resolved == first_resolution.resolved
    assert second_physical_reads == first_physical_reads
    assert any(event.cache_hit for event in fs.reads)
    assert fs.physical_read_count("alias_index") == 1
    assert fs.physical_read_count("manifest") == 1
