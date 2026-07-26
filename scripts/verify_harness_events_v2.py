from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.phase_contract import REQUIRED_PHASES, read_phase_events, validate_required_phases  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify runtime harness phase events.")
    parser.add_argument("events_json")
    args = parser.parse_args()

    path = Path(args.events_json)
    if path.suffix == ".jsonl":
        events = read_phase_events(path)
    else:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict) and "phases" in data:
            events = data["phases"]
        elif isinstance(data, list):
            events = data
        else:
            events = []
    result = validate_required_phases(events, REQUIRED_PHASES)
    if not result.measured:
        print("MISSING_HARNESS_PHASES=" + ",".join(result.missing_phases), file=sys.stderr)
        return 1
    print("HARNESS_EVENTS_V2: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
