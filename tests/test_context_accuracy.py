from benchmarks.context_usage_compare import Question, _score


def test_context_accuracy_scores_files_symbols_facts_and_penalties():
    question = Question(
        "QX",
        "where is alpha",
        ("a.py", "b.py"),
        ("alpha", "beta"),
        ("fact-one", "fact-two"),
        ("forbidden",),
    )

    score = _score(question, ("a.py", "b.py"), ["alpha beta fact-one fact-two"])
    penalized = _score(question, ("a.py",), ["alpha fact-one forbidden"])

    assert score["accuracy"] == 1.0
    assert penalized["accuracy"] < score["accuracy"]
    assert penalized["contradiction_penalty"] > 0
