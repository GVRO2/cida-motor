import pytest

from benchmarks.context_usage_compare import _build_tknc_corpus, _question_set, _write_fixture_corpus
from cida.domain.errors import SidecarValidationError
from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec
from cida.infrastructure.tknc_context_session import ContextFilesystem, TkncContextSession
from cida.infrastructure.tokenizer import OfflineTokenizer


def test_context_session_close_clears_managed_state_and_blocks_reuse(tmp_path):
    original, relpaths = _write_fixture_corpus(tmp_path, "close", 600)
    tknc = tmp_path / "close" / "tknc"
    _build_tknc_corpus(original, tknc, relpaths)
    fs = ContextFilesystem()
    session = TkncContextSession(tknc, fs, JsonCodec(), HashService(), OfflineTokenizer())
    search = session.search(_question_set()[0].question, query_id="close-search")
    aliases = session.aliases_in_index(set(search.alias_candidates), query_id="close-aliases")
    session.resolve(aliases, query_id="close-resolve")

    assert fs.cache_metrics()["cache_current_bytes"] > 0

    session.close()

    assert session.index_data is None
    assert session.index_text == ""
    assert session.manifest_data is None
    assert session.manifest_text == ""
    assert session.resolved_aliases == {}
    assert fs.cache_metrics()["cache_current_bytes"] == 0
    with pytest.raises(SidecarValidationError, match="closed"):
        session.search(_question_set()[0].question, query_id="after-close")
