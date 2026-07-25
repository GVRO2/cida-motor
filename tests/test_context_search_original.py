from benchmarks.context_usage_compare import _question_set, _search, _write_fixture_corpus


def test_original_search_discovers_files_from_question_without_expected_file_input(tmp_path):
    original, _ = _write_fixture_corpus(tmp_path, "search", 10)
    question = _question_set()[2]

    result = _search(original, question.question)

    assert "cida/application/optimize_corpus.py" in result.files
    assert result.files_scanned >= 8
    assert "write_corpus_sidecars" in result.terms
