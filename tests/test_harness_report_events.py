from benchmarks.context_usage_compare import _harness_summary


def test_harness_summary_reports_measured_python_events_and_unmeasured_go():
    summary = _harness_summary()

    assert summary["original_python"]["measured"] is True
    assert "events" in summary["original_python"]
    assert summary["original_go"]["measured"] is False
    assert summary["original_go"]["reason"]
