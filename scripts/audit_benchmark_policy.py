from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.benchmark_policy_auditor import BenchmarkPolicyAuditor, load_benchmark_policy  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit benchmark policy and optional report artifacts.")
    parser.add_argument("reports", nargs="*", help="Performance report JSON files to verify.")
    args = parser.parse_args()

    policy = load_benchmark_policy()
    auditor = BenchmarkPolicyAuditor(policy)
    violations = []
    for rel in (
        "benchmarks/performance_compare.py",
        "benchmarks/context_usage_compare.py",
        "cida/infrastructure/tknc_context_session.py",
    ):
        violations.extend(auditor.audit_source((ROOT / rel).read_text(encoding="utf-8", errors="replace")))
    for report_path in args.reports:
        data = json.loads(Path(report_path).read_text(encoding="utf-8"))
        violations.extend(auditor.audit_report(data))

    if violations:
        for violation in violations:
            location = f" [{violation.scenario}]" if violation.scenario else ""
            print(f"{violation.code}{location}: {violation.message}", file=sys.stderr)
        return 1
    print(f"BENCHMARK_POLICY_AUDIT: PASS policy_sha256={policy.sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
