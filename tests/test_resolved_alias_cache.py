from pathlib import Path

from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec
from cida.infrastructure.tknc_context_session import ContextFilesystem, TkncContextSession
from cida.infrastructure.tokenizer import OfflineTokenizer


def test_resolved_alias_cache_is_bounded_by_count_and_bytes(tmp_path):
    session = TkncContextSession(
        Path(tmp_path),
        ContextFilesystem(),
        JsonCodec(),
        HashService(),
        OfflineTokenizer(),
        max_resolved_aliases=2,
        max_resolved_alias_bytes=12,
    )

    session._store_resolved_aliases({"AA": "alpha", "AB": "beta", "AC": "gamma"})

    assert len(session.resolved_aliases) <= 2
    assert session.resolved_alias_bytes <= 12
    assert session.resolved_alias_evictions > 0
