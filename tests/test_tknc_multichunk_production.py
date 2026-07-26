from benchmarks.context_usage_compare import _build_tknc_corpus, _write_fixture_corpus
from cida.application.selective_alias_resolution import ALIAS_INDEX_FILENAME
from cida.infrastructure.json_codec import JsonCodec


def test_production_cli_generates_real_multichunk_corpora(tmp_path):
    for name, alias_target, expected_chunks in (("chunks-1", 500, 1), ("chunks-2", 1000, 2), ("chunks-5", 2500, 5)):
        original, relpaths = _write_fixture_corpus(tmp_path, name, alias_target)
        tknc = tmp_path / name / "tknc"
        _build_tknc_corpus(original, tknc, relpaths)

        index = JsonCodec().decode((tknc / "tknd" / ALIAS_INDEX_FILENAME).read_text(encoding="utf-8"))

        assert index["alias_count"] >= alias_target
        assert index["chunk_count"] >= expected_chunks
        assert index["schema_version"] == 3
        assert index["membership"] == "EXACT_MEMBERSHIP"
        assert index["segment_count"] == len(index["segments"])
        assert "ranges" not in index
