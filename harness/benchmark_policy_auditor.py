from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any


POLICY_PATH = Path(__file__).resolve().parent.parent / "benchmarks" / "performance-policy.json"
ROBUST_MAD_MULTIPLIER = 3.0


@dataclass(frozen=True)
class BenchmarkPolicy:
    schema_version: int
    median_budget: float
    p95_budget: float
    rss_budget: float
    stability_cv_limit: float
    balanced_runs: int
    balanced_warmups: int
    allow_timing_skip: bool
    allow_parent_only_rss: bool
    allow_system_site_packages: bool
    sha256: str


@dataclass(frozen=True)
class BenchmarkPolicyViolation:
    code: str
    message: str
    scenario: str = ""


class BenchmarkPolicyAuditor:
    FORBIDDEN_SOURCE_MARKERS = {
        "TIMING_SKIP_BY_FINGERPRINT": ("timing_gate_skipped",),
        "BUDGET_PASS_BY_SKIP": ("budget_result = raw_budget_result or",),
        "STABLE_REGRESSION_RETRY": ("max_attempts", "attempt <"),
        "PARENT_ONLY_RSS": ("parent_only_rss", "peak_rss_bytes = parent"),
        "SYSTEM_SITE_PACKAGES": ("system_site_packages=True",),
        "FAKE_MEASURED_HARNESS": ("measured = True", "missing_phases"),
        "ONE_CHUNK_WARM_SESSION": ("corpus_name = \"one_chunk\"", "break_even_query_count"),
        "SELF_APPROVED_REPORT": ("overall_result", "producer_pass"),
        "BENCHMARK_COUPLED_SEARCH": ("TERM_EXPANSIONS", "processarEComparar", "ResourceProfiles", "count_tokens"),
    }

    def __init__(self, policy: BenchmarkPolicy) -> None:
        self.policy = policy

    def audit_source(self, text: str) -> list[BenchmarkPolicyViolation]:
        violations: list[BenchmarkPolicyViolation] = []
        lowered = text
        for code, markers in self.FORBIDDEN_SOURCE_MARKERS.items():
            if all(marker in lowered for marker in markers):
                violations.append(BenchmarkPolicyViolation(code, f"Forbidden benchmark weakening marker detected: {code}"))
        return violations

    def audit_report(self, report: dict[str, Any]) -> list[BenchmarkPolicyViolation]:
        violations: list[BenchmarkPolicyViolation] = []
        if report.get("overall_result") != "PASS":
            violations.append(BenchmarkPolicyViolation("OVERALL_NOT_PASS", "Performance report overall result is not PASS"))
        if report.get("policy_sha256") != self.policy.sha256:
            violations.append(BenchmarkPolicyViolation("POLICY_HASH_MISMATCH", "Performance report policy hash does not match versioned policy"))
        budgets = report.get("budgets", {})
        if budgets.get("allow_timing_skip") is not False:
            violations.append(BenchmarkPolicyViolation("TIMING_SKIP_POLICY_NOT_FALSE", "Timing skip policy must be false"))
        if budgets.get("allow_parent_only_rss") is not False:
            violations.append(BenchmarkPolicyViolation("PARENT_ONLY_RSS_POLICY_NOT_FALSE", "Parent-only RSS policy must be false"))
        if budgets.get("allow_system_site_packages") is not False:
            violations.append(BenchmarkPolicyViolation("SYSTEM_SITE_PACKAGES_POLICY_NOT_FALSE", "system_site_packages policy must be false"))
        environments = report.get("python_environments", {})
        for name, data in environments.items():
            if isinstance(data, dict) and data.get("system_site_packages") is not False:
                violations.append(BenchmarkPolicyViolation("SYSTEM_SITE_PACKAGES_USED", f"Python environment is not isolated: {name}"))

        for scenario_name, scenario in sorted(report.get("scenarios", {}).items()):
            violations.extend(self._audit_scenario(str(scenario_name), scenario))
        return violations

    def _audit_scenario(self, scenario_name: str, scenario: dict[str, Any]) -> list[BenchmarkPolicyViolation]:
        violations: list[BenchmarkPolicyViolation] = []
        if scenario.get("timing_gate_skipped_reason"):
            violations.append(BenchmarkPolicyViolation("TIMING_GATE_SKIPPED", "Fingerprint timing skip is forbidden", scenario_name))
        base = scenario.get("base", {})
        head = scenario.get("head", {})
        base_summary, base_violations = self._audit_sample_summary(scenario_name, "base", base, scenario)
        head_summary, head_violations = self._audit_sample_summary(scenario_name, "head", head, scenario)
        violations.extend(base_violations)
        violations.extend(head_violations)
        comparison = {
            "median_delta": self._delta(head_summary["median"], base_summary["median"]),
            "p95_delta": self._delta(head_summary["p95"], base_summary["p95"]),
            "peak_rss_delta": self._delta(head_summary["peak_rss"], base_summary["peak_rss"]),
        }
        for key, expected in comparison.items():
            actual = scenario.get("comparison", {}).get(key)
            if not self._float_close(actual, expected):
                violations.append(BenchmarkPolicyViolation("COMPARISON_NOT_RECOMPUTED", f"{key} does not match raw samples", scenario_name))
        raw_budget_pass = self._raw_budget_pass_from_comparison(comparison)
        if scenario.get("budget_result") != self._pass_fail(raw_budget_pass):
            violations.append(BenchmarkPolicyViolation("BUDGET_RESULT_NOT_RECOMPUTED", "Budget result does not match recomputed policy", scenario_name))
        stable = (
            base_summary["cv"] <= self.policy.stability_cv_limit
            and head_summary["cv"] <= self.policy.stability_cv_limit
        )
        for attempt in scenario.get("attempts", [])[:-1]:
            attempt_stable = (
                float(attempt.get("base_cv", math.inf)) <= self.policy.stability_cv_limit
                and float(attempt.get("head_cv", math.inf)) <= self.policy.stability_cv_limit
            )
            attempt_budget_pass = self._raw_budget_pass_from_comparison(attempt.get("comparison", {}))
            if attempt.get("budget_result") != self._pass_fail(attempt_budget_pass):
                violations.append(BenchmarkPolicyViolation("ATTEMPT_BUDGET_RESULT_NOT_RECOMPUTED", "Attempt budget result does not match recomputed policy", scenario_name))
            if attempt_stable and not attempt_budget_pass:
                violations.append(BenchmarkPolicyViolation("STABLE_REGRESSION_RETRIED", "Stable regression was retried instead of failing immediately", scenario_name))
        expected_gate = raw_budget_pass and stable
        if scenario.get("gate_result") != self._pass_fail(expected_gate):
            violations.append(BenchmarkPolicyViolation("GATE_RESULT_NOT_RECOMPUTED", "Gate result does not match recomputed policy", scenario_name))
        if not expected_gate:
            violations.append(BenchmarkPolicyViolation("SCENARIO_GATE_FAIL", "Scenario fails recomputed performance gate", scenario_name))
        if isinstance(head, dict) and head.get("peak_rss") != head.get("process_tree_peak_rss"):
            violations.append(BenchmarkPolicyViolation("RSS_NOT_PROCESS_TREE", "Head RSS budget must use process-tree peak RSS", scenario_name))
        return violations

    def _audit_sample_summary(
        self,
        scenario_name: str,
        version: str,
        summary: dict[str, Any],
        scenario: dict[str, Any],
    ) -> tuple[dict[str, float], list[BenchmarkPolicyViolation]]:
        violations: list[BenchmarkPolicyViolation] = []
        raw_samples = summary.get("raw_samples")
        if not isinstance(raw_samples, list) or not raw_samples:
            violations.append(BenchmarkPolicyViolation("RAW_SAMPLES_MISSING", f"{version} raw samples are required", scenario_name))
            return {"median": math.inf, "p95": math.inf, "cv": math.inf, "peak_rss": math.inf}, violations

        expected_sample_count = int(scenario.get("runs", 0)) * int(scenario.get("attempt", 1))
        if len(raw_samples) != expected_sample_count:
            violations.append(
                BenchmarkPolicyViolation(
                    "INCOMPLETE_SAMPLE_SET",
                    f"{version} raw sample count {len(raw_samples)} does not match runs*attempt {expected_sample_count}",
                    scenario_name,
                )
            )
        if int(summary.get("sample_count", -1)) != len(raw_samples):
            violations.append(BenchmarkPolicyViolation("SAMPLE_COUNT_MISMATCH", f"{version} sample_count does not match raw samples", scenario_name))

        recomputed = self._summary_from_raw_samples(raw_samples)
        for key in ("median", "p95", "cv", "peak_rss", "process_tree_peak_rss"):
            expected = recomputed["peak_rss"] if key == "process_tree_peak_rss" else recomputed[key]
            if not self._float_close(summary.get(key), expected):
                violations.append(BenchmarkPolicyViolation("SUMMARY_NOT_RECOMPUTED", f"{version}.{key} does not match raw samples", scenario_name))
        return recomputed, violations

    def _raw_budget_pass_from_comparison(self, comparison: dict[str, Any]) -> bool:
        return (
            float(comparison.get("median_delta", math.inf)) <= self.policy.median_budget
            and float(comparison.get("p95_delta", math.inf)) <= self.policy.p95_budget
            and float(comparison.get("peak_rss_delta", math.inf)) <= self.policy.rss_budget
        )

    def _summary_from_raw_samples(self, raw_samples: list[dict[str, Any]]) -> dict[str, float]:
        durations = [float(sample["duration_seconds"]) for sample in raw_samples]
        duration_summary = summarize_samples(durations)
        peak_rss = max(float(sample["process_tree_peak_rss"]) for sample in raw_samples)
        return {
            "median": duration_summary["median"],
            "p95": duration_summary["p95"],
            "cv": duration_summary["cv"],
            "peak_rss": peak_rss,
        }

    @staticmethod
    def _delta(head: float, base: float) -> float:
        if base <= 0:
            return 0.0
        return (head - base) / base

    @staticmethod
    def _float_close(actual: Any, expected: float) -> bool:
        try:
            actual_float = float(actual)
        except (TypeError, ValueError):
            return False
        return math.isclose(actual_float, expected, rel_tol=1e-9, abs_tol=1e-9)

    @staticmethod
    def _pass_fail(value: bool) -> str:
        return "PASS" if value else "FAIL"


def load_benchmark_policy(path: Path = POLICY_PATH) -> BenchmarkPolicy:
    raw = path.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    return BenchmarkPolicy(
        schema_version=int(data["schema_version"]),
        median_budget=float(data["median_budget"]),
        p95_budget=float(data["p95_budget"]),
        rss_budget=float(data["rss_budget"]),
        stability_cv_limit=float(data["stability_cv_limit"]),
        balanced_runs=int(data["balanced_runs"]),
        balanced_warmups=int(data["balanced_warmups"]),
        allow_timing_skip=bool(data["allow_timing_skip"]),
        allow_parent_only_rss=bool(data["allow_parent_only_rss"]),
        allow_system_site_packages=bool(data["allow_system_site_packages"]),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def summarize_samples(samples: list[float]) -> dict[str, float]:
    if not samples:
        return {
            "median": 0.0,
            "p95": 0.0,
            "cv": 0.0,
            "raw_median": 0.0,
            "raw_p95": 0.0,
            "raw_cv": 0.0,
            "duration_cap": 0.0,
            "duration_outliers_capped": 0.0,
        }
    raw_ordered = sorted(float(sample) for sample in samples)
    raw_mean = statistics.mean(raw_ordered)
    raw_stddev = statistics.pstdev(raw_ordered) if len(raw_ordered) > 1 else 0.0
    raw_median = statistics.median(raw_ordered)
    deviations = [abs(value - raw_median) for value in raw_ordered]
    mad = statistics.median(deviations) if deviations else 0.0
    if mad > 0:
        duration_cap = raw_median + (ROBUST_MAD_MULTIPLIER * mad)
    else:
        duration_cap = raw_median
    robust_values = [min(value, duration_cap) for value in raw_ordered]
    capped = sum(1 for raw, robust in zip(raw_ordered, robust_values) if raw != robust)
    mean = statistics.mean(robust_values)
    stddev = statistics.pstdev(robust_values) if len(robust_values) > 1 else 0.0
    return {
        "median": statistics.median(robust_values),
        "p95": robust_values[min(len(robust_values) - 1, math.ceil(len(robust_values) * 0.95) - 1)],
        "cv": (stddev / mean) if mean > 0 else 0.0,
        "raw_median": raw_median,
        "raw_p95": raw_ordered[min(len(raw_ordered) - 1, math.ceil(len(raw_ordered) * 0.95) - 1)],
        "raw_cv": (raw_stddev / raw_mean) if raw_mean > 0 else 0.0,
        "duration_cap": duration_cap,
        "duration_outliers_capped": float(capped),
    }
