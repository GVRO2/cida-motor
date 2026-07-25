from benchmarks.context_usage_compare import _summarize


def _scenario(lookup_pass: bool = True):
    return {
        "original": {"full_total_context_tokens": 1000, "total_context_tokens": 200},
        "tknc": {
            "full_total_context_tokens": 500,
            "total_context_tokens": 100,
            "lookup_pass": lookup_pass,
            "all_aliases_resolvable": True,
            "global_dictionary_preload": False,
            "token_accounting_pass": True,
            "read_events": [],
        },
        "accuracy": {"original": {"accuracy": 1.0}, "tknc": {"accuracy": 1.0}},
    }


def _sessions(result: str = "PASS"):
    return {
        "result": result,
        "break_even_query_count": 10 if result == "PASS" else None,
        "query_counts": {
            "1": {"original": 200, "tknc": 190, "delta": 10},
            "10": {"original": 2000, "tknc": 1000, "delta": 1000},
            "50": {"original": 10000, "tknc": 4000, "delta": 6000},
            "100": {"original": 20000, "tknc": 7000, "delta": 13000},
        },
    }


def _corpora():
    return {"ten_chunks": {"alias_count": 5000, "chunk_count": 10}}


def _production():
    return {"ten_chunks": {"exit_code": 0}}


def test_overall_result_fails_when_lookup_gate_fails():
    report = _summarize("head", _corpora(), [_scenario(lookup_pass=False)], _production(), _sessions())

    assert report["summary"]["lookup"]["result"] == "FAIL"
    assert report["summary"]["overall_result"] == "FAIL"


def test_overall_result_fails_when_warm_or_multi_query_gate_fails():
    report = _summarize("head", _corpora(), [_scenario()], _production(), _sessions(result="FAIL"))

    assert report["summary"]["selective_warm"]["result"] == "FAIL"
    assert report["summary"]["multi_query"]["result"] == "FAIL"
    assert report["summary"]["overall_result"] == "FAIL"
