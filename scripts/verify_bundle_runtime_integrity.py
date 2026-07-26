from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.artifact_verifier import BundleRuntimeVerifier  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify every artifact listed in a CIDA bundle manifest.")
    parser.add_argument("bundle_root")
    args = parser.parse_args()

    verifier = BundleRuntimeVerifier(Path(args.bundle_root))
    errors = []
    for rel in sorted(verifier.entries_by_path):
        try:
            verifier.verify_artifact(Path(args.bundle_root) / Path(*rel.split("/")))
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("BUNDLE_RUNTIME_INTEGRITY: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
