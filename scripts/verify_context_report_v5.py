from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REQUIRED_SESSION_CORPORA = ("ten_chunks", "hundred_chunks")


def main() -> int:
    parser = argparse.ArgumentParser(description="Independently verify a CIDA context report v5.")
    parser.add_argument("report")
    args = parser.parse_args()

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    errors = []
    if report.get("schema_version") != 5:
        errors.append("SCHEMA_VERSION_NOT_5")
    if report.get("summary", {}).get("overall_result") != "PASS":
        errors.append("OVERALL_NOT_PASS")
    sessions_by_corpus = report.get("sessions_by_corpus", {})
    for corpus in REQUIRED_SESSION_CORPORA:
        session = sessions_by_corpus.get(corpus)
        if not isinstance(session, dict) or session.get("result") != "PASS":
            errors.append(f"{corpus.upper()}_SESSION_NOT_PASS")
        if session and session.get("break_even_query_count") is None:
            errors.append(f"{corpus.upper()}_BREAK_EVEN_MISSING")
    scenarios = report.get("scenarios", [])
    for item in scenarios:
        if item.get("result") != "PASS":
            errors.append(f"SCENARIO_NOT_PASS:{item.get('question_id')}")
        if item.get("tknc", {}).get("lookup_pass") is not True:
            errors.append(f"LOOKUP_NOT_PASS:{item.get('question_id')}")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("INDEPENDENT_CONTEXT_VERIFIER: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
