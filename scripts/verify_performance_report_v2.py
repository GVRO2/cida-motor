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
    parser = argparse.ArgumentParser(description="Independently verify a CIDA performance report.")
    parser.add_argument("report")
    args = parser.parse_args()

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    auditor = BenchmarkPolicyAuditor(load_benchmark_policy())
    violations = auditor.audit_report(report)
    if violations:
        for violation in violations:
            location = f" [{violation.scenario}]" if violation.scenario else ""
            print(f"{violation.code}{location}: {violation.message}", file=sys.stderr)
        return 1
    print("INDEPENDENT_PERFORMANCE_VERIFIER: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
