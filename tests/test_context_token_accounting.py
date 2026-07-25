from benchmarks.context_usage_compare import _build_tknc_corpus, _measure_question, _question_set, _write_fixture_corpus
from cida.infrastructure.tokenizer import OfflineTokenizer


def test_tknc_token_categories_sum_without_overlap(tmp_path):
    original, relpaths = _write_fixture_corpus(tmp_path, "accounting", 700)
    tknc = tmp_path / "accounting" / "tknc"
    _build_tknc_corpus(original, tknc, relpaths)

    measured = _measure_question(OfflineTokenizer(), original, tknc, _question_set()[0])
    tknc_data = measured["tknc"]

    assert tknc_data["token_accounting_pass"] is True
    assert tknc_data["total_tokens"] == (
        tknc_data["content_tokens"]
        + tknc_data["search_tokens"]
        + tknc_data["instruction_tokens"]
        + tknc_data["index_tokens"]
        + tknc_data["manifest_tokens"]
        + tknc_data["sidecar_tokens"]
        + tknc_data["translation_tokens"]
    )
