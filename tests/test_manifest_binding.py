import json

import pytest

from benchmarks.context_usage_compare import _build_tknc_corpus, _question_set, _write_fixture_corpus
from cida.infrastructure.tknc_context_session import ContextFilesystem, TkncContextSession
from cida.domain.errors import SidecarValidationError
from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec
from cida.infrastructure.tokenizer import OfflineTokenizer


def _session_for(tknc):
    return TkncContextSession(tknc, ContextFilesystem(), JsonCodec(), HashService(), OfflineTokenizer())


def test_session_validates_manifest_hash_and_index_binding(tmp_path):
    original, relpaths = _write_fixture_corpus(tmp_path, "manifest-ok", 600)
    tknc = tmp_path / "manifest-ok" / "tknc"
    _build_tknc_corpus(original, tknc, relpaths)
    session = _session_for(tknc)
    search = session.search(_question_set()[-1].question, query_id="Q")
    aliases = session.aliases_in_index(set(search.alias_candidates), query_id="Q")

    result = session.resolve(aliases, query_id="Q")

    assert result.resolved
    assert session.manifest_data is not None


def test_session_rejects_tampered_manifest(tmp_path):
    original, relpaths = _write_fixture_corpus(tmp_path, "manifest-tampered", 600)
    tknc = tmp_path / "manifest-tampered" / "tknc"
    _build_tknc_corpus(original, tknc, relpaths)
    manifest_path = tknc / "tknc-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].append({"path": "evil.md", "sha256": "0" * 64})
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    session = _session_for(tknc)
    search = session.search(_question_set()[-1].question, query_id="Q")
    aliases = session.aliases_in_index(set(search.alias_candidates), query_id="Q")

    with pytest.raises(SidecarValidationError, match="manifest hash mismatch"):
        session.resolve(aliases, query_id="Q")


def test_session_rejects_missing_manifest(tmp_path):
    original, relpaths = _write_fixture_corpus(tmp_path, "manifest-missing", 600)
    tknc = tmp_path / "manifest-missing" / "tknc"
    _build_tknc_corpus(original, tknc, relpaths)
    (tknc / "tknc-manifest.json").unlink()
    session = _session_for(tknc)
    search = session.search(_question_set()[-1].question, query_id="Q")
    aliases = session.aliases_in_index(set(search.alias_candidates), query_id="Q")

    with pytest.raises(SidecarValidationError, match="manifest is missing"):
        session.resolve(aliases, query_id="Q")
