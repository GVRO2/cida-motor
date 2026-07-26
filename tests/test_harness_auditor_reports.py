import copy
import json

from harness.benchmark_policy_auditor import BenchmarkPolicyAuditor, load_benchmark_policy, summarize_samples


def _samples(durations, rss_values=None):
    rss_values = rss_values or [100] * len(durations)
    return [
        {
            "duration_seconds": duration,
            "process_tree_peak_rss": rss,
        }
        for duration, rss in zip(durations, rss_values)
    ]


def _summary(durations, rss_values=None):
    raw_samples = _samples(durations, rss_values)
    sample_summary = summarize_samples(durations)
    peak_rss = max(sample["process_tree_peak_rss"] for sample in raw_samples)
    return {
        "raw_samples": raw_samples,
        "raw_durations": list(durations),
        "sample_count": len(raw_samples),
        "median": sample_summary["median"],
        "p95": sample_summary["p95"],
        "cv": sample_summary["cv"],
        "peak_rss": peak_rss,
        "process_tree_peak_rss": peak_rss,
    }


def _valid_report():
    policy = load_benchmark_policy()
    return {
        "overall_result": "PASS",
        "policy_sha256": policy.sha256,
        "budgets": {
            "allow_timing_skip": False,
            "allow_parent_only_rss": False,
            "allow_system_site_packages": False,
        },
        "python_environments": {"base": {"system_site_packages": False}},
        "scenarios": {
            "stable": {
                "runs": 2,
                "attempt": 1,
                "base": _summary([1.0, 1.0], [100, 100]),
                "head": _summary([1.01, 1.01], [101, 101]),
                "comparison": {
                    "median_delta": 0.010000000000000009,
                    "p95_delta": 0.010000000000000009,
                    "peak_rss_delta": 0.01,
                },
                "budget_result": "PASS",
                "raw_budget_result": "PASS",
                "gate_result": "PASS",
                "attempts": [
                    {
                        "base_cv": 0.0,
                        "head_cv": 0.0,
                        "budget_result": "PASS",
                        "raw_budget_result": "PASS",
                        "comparison": {
                            "median_delta": 0.010000000000000009,
                            "p95_delta": 0.010000000000000009,
                            "peak_rss_delta": 0.01,
                        },
                    }
                ],
            }
        },
    }


def test_performance_auditor_accepts_recomputed_raw_sample_report():
    auditor = BenchmarkPolicyAuditor(load_benchmark_policy())

    assert auditor.audit_report(_valid_report()) == []


def test_performance_auditor_rejects_tampered_report_fields():
    report = _valid_report()
    report["overall_result"] = "FAIL"
    report["policy_sha256"] = "0" * 64
    report["budgets"]["allow_timing_skip"] = True
    report["budgets"]["allow_parent_only_rss"] = True
    report["budgets"]["allow_system_site_packages"] = True
    report["python_environments"]["base"]["system_site_packages"] = True
    scenario = report["scenarios"]["stable"]
    scenario["timing_gate_skipped_reason"] = "SKIPPED"
    scenario["base"]["sample_count"] = 99
    scenario["head"]["median"] = 99
    scenario["head"]["peak_rss"] = 1
    scenario["comparison"]["median_delta"] = 99
    scenario["budget_result"] = "FAIL"
    scenario["gate_result"] = "FAIL"
    scenario["attempts"].insert(
        0,
        {
            "base_cv": 0.0,
            "head_cv": 0.0,
            "budget_result": "PASS",
            "comparison": {"median_delta": 0.5, "p95_delta": 0.0, "peak_rss_delta": 0.0},
        },
    )

    codes = {violation.code for violation in BenchmarkPolicyAuditor(load_benchmark_policy()).audit_report(report)}

    assert {
        "OVERALL_NOT_PASS",
        "POLICY_HASH_MISMATCH",
        "TIMING_SKIP_POLICY_NOT_FALSE",
        "PARENT_ONLY_RSS_POLICY_NOT_FALSE",
        "SYSTEM_SITE_PACKAGES_POLICY_NOT_FALSE",
        "SYSTEM_SITE_PACKAGES_USED",
        "TIMING_GATE_SKIPPED",
        "SAMPLE_COUNT_MISMATCH",
        "SUMMARY_NOT_RECOMPUTED",
        "COMPARISON_NOT_RECOMPUTED",
        "BUDGET_RESULT_NOT_RECOMPUTED",
        "ATTEMPT_BUDGET_RESULT_NOT_RECOMPUTED",
        "STABLE_REGRESSION_RETRIED",
        "GATE_RESULT_NOT_RECOMPUTED",
        "RSS_NOT_PROCESS_TREE",
    } <= codes


def test_performance_auditor_rejects_missing_or_incomplete_samples():
    report = _valid_report()
    scenario = report["scenarios"]["stable"]
    scenario["base"]["raw_samples"] = []
    scenario["head"]["raw_samples"] = scenario["head"]["raw_samples"][:1]

    codes = {violation.code for violation in BenchmarkPolicyAuditor(load_benchmark_policy()).audit_report(report)}

    assert "RAW_SAMPLES_MISSING" in codes
    assert "INCOMPLETE_SAMPLE_SET" in codes
    assert "SCENARIO_GATE_FAIL" in codes


def test_policy_loader_hashes_exact_policy_file(tmp_path):
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "median_budget": 0.05,
                "p95_budget": 0.10,
                "rss_budget": 0.10,
                "stability_cv_limit": 0.10,
                "balanced_runs": 5,
                "balanced_warmups": 1,
                "allow_timing_skip": False,
                "allow_parent_only_rss": False,
                "allow_system_site_packages": False,
            }
        ),
        encoding="utf-8",
    )

    policy = load_benchmark_policy(policy_path)

    assert policy.schema_version == 1
    assert policy.sha256


def test_summarize_samples_empty_and_nonempty():
    assert summarize_samples([]) == {"median": 0.0, "p95": 0.0, "cv": 0.0}
    result = summarize_samples([1.0, 2.0, 3.0])
    assert result["median"] == 2.0
    assert result["p95"] == 3.0
    assert result["cv"] > 0


def test_valid_report_fixture_is_copyable_without_mutating_source():
    report = _valid_report()
    clone = copy.deepcopy(report)
    clone["scenarios"]["stable"]["base"]["median"] = 2

    assert report["scenarios"]["stable"]["base"]["median"] == 1.0
