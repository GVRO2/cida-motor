from pathlib import Path

import pytest

from harness.benchmark_policy_auditor import BenchmarkPolicyAuditor, load_benchmark_policy
from harness.phase_contract import REQUIRED_PHASES, validate_required_phases
from harness.runtime_harness_probe import OriginalProjectHarness


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "harness-policy"


@pytest.mark.parametrize(
    ("fixture_name", "code"),
    [
        ("timing-skip-by-fingerprint.py", "TIMING_SKIP_BY_FINGERPRINT"),
        ("budget-pass-by-skip.py", "BUDGET_PASS_BY_SKIP"),
        ("stable-regression-retry.py", "STABLE_REGRESSION_RETRY"),
        ("parent-only-rss.py", "PARENT_ONLY_RSS"),
        ("system-site-packages.py", "SYSTEM_SITE_PACKAGES"),
        ("fake-measured-harness.py", "FAKE_MEASURED_HARNESS"),
        ("one-chunk-warm-session.py", "ONE_CHUNK_WARM_SESSION"),
        ("self-approved-report.py", "SELF_APPROVED_REPORT"),
        ("hardcoded-search-answer.py", "BENCHMARK_COUPLED_SEARCH"),
    ],
)
def test_harness_rejects_policy_fixture(fixture_name: str, code: str):
    auditor = BenchmarkPolicyAuditor(load_benchmark_policy())

    violations = auditor.audit_source((FIXTURES / fixture_name).read_text(encoding="utf-8"))

    assert code in {violation.code for violation in violations}


def test_harness_rejects_timing_skip_by_fingerprint():
    test_harness_rejects_policy_fixture("timing-skip-by-fingerprint.py", "TIMING_SKIP_BY_FINGERPRINT")


def test_harness_rejects_budget_override():
    test_harness_rejects_policy_fixture("budget-pass-by-skip.py", "BUDGET_PASS_BY_SKIP")


def test_harness_rejects_stable_regression_retry():
    test_harness_rejects_policy_fixture("stable-regression-retry.py", "STABLE_REGRESSION_RETRY")


def test_harness_rejects_parent_only_rss():
    test_harness_rejects_policy_fixture("parent-only-rss.py", "PARENT_ONLY_RSS")


def test_harness_rejects_system_site_packages():
    test_harness_rejects_policy_fixture("system-site-packages.py", "SYSTEM_SITE_PACKAGES")


def test_harness_rejects_fake_measured_harness():
    test_harness_rejects_policy_fixture("fake-measured-harness.py", "FAKE_MEASURED_HARNESS")


def test_harness_rejects_one_chunk_warm_claim():
    test_harness_rejects_policy_fixture("one-chunk-warm-session.py", "ONE_CHUNK_WARM_SESSION")


def test_harness_rejects_self_approved_report():
    test_harness_rejects_policy_fixture("self-approved-report.py", "SELF_APPROVED_REPORT")


def test_harness_rejects_benchmark_coupled_search():
    test_harness_rejects_policy_fixture("hardcoded-search-answer.py", "BENCHMARK_COUPLED_SEARCH")


def test_phase_contract_requires_every_phase():
    result = validate_required_phases(
        [{"phase": "PRODUCTION_CLI_START"}, {"phase": "BENCHMARK_COMPLETE"}],
        REQUIRED_PHASES,
    )

    assert result.measured is False
    assert "PRODUCTION_CLI_COMPLETE" in result.missing_phases


def test_original_project_harness_inspects_go_to_python_graph():
    graph = OriginalProjectHarness().inspect(Path(__file__).resolve().parent.parent, "go")

    assert graph.runner == "go"
    assert "motor_v3.go" in graph.entrypoints
    assert "token_optimizer.py" in graph.runtime_files
    assert "cida/interfaces/cli.py" in graph.runtime_files
    assert "go.mod" in graph.dependency_files
