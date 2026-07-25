import os
from pathlib import Path

from benchmarks.context_usage_compare import (
    _build_tknc_corpus,
    _measure_question,
    _question_set,
    _write_fixture_corpus,
)
from cida.infrastructure.tokenizer import OfflineTokenizer


def test_tknc_selective_context_counts_required_components(tmp_path):
    os.environ["TIKTOKEN_CACHE_DIR"] = str(Path(__file__).resolve().parent.parent / "resources")
    original, relpaths = _write_fixture_corpus(tmp_path, "small", 8)
    tknc = tmp_path / "small" / "tknc"
    _build_tknc_corpus(original, tknc, relpaths)

    measured = _measure_question(OfflineTokenizer(), original, tknc, _question_set()[0])
    tknc_data = measured["tknc"]

    expected_total = (
        tknc_data["content_tokens"]
        + tknc_data["instruction_tokens"]
        + tknc_data["index_tokens"]
        + tknc_data["sidecar_tokens"]
        + tknc_data["manifest_tokens"]
        + tknc_data["translation_tokens"]
    )
    assert tknc_data["total_context_tokens"] == expected_total
    assert tknc_data["index_tokens"] > 0
    assert tknc_data["manifest_tokens"] > 0
    assert tknc_data["sidecar_tokens"] > 0
    assert tknc_data["translation_tokens"] > 0


def test_tknc_selective_context_gate_and_accuracy(tmp_path):
    os.environ["TIKTOKEN_CACHE_DIR"] = str(Path(__file__).resolve().parent.parent / "resources")
    original, relpaths = _write_fixture_corpus(tmp_path, "medium", 80)
    tknc = tmp_path / "medium" / "tknc"
    _build_tknc_corpus(original, tknc, relpaths)

    measured = _measure_question(OfflineTokenizer(), original, tknc, _question_set()[2])

    assert measured["tknc"]["global_dictionary_preload"] is False
    assert measured["tknc"]["chunks_loaded"] == ["B0.cidatkn"]
    assert measured["tknc"]["entries_loaded"] == 2
    assert measured["tknc"]["total_context_tokens"] < measured["original"]["full_total_context_tokens"]
    assert measured["accuracy"]["tknc"] >= measured["accuracy"]["original"]
    assert measured["result"] == "PASS"
