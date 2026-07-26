from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.runtime_harness_probe import OriginalProjectHarness  # noqa: E402


def _export_ref(ref: str, destination: Path) -> None:
    archive = subprocess.run(
        ["git", "archive", "--format=tar", ref],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if archive.returncode != 0:
        raise RuntimeError(archive.stderr.decode("utf-8", errors="replace"))
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=BytesIO(archive.stdout), mode="r:") as tar:
        tar.extractall(destination)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect original and HEAD runtime dependency graphs.")
    parser.add_argument("--base-ref", default="upstream/main")
    parser.add_argument("--runner", default="go", choices=["go", "python"])
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    temp_root = Path(tempfile.mkdtemp(prefix="cida-original-harness-"))
    try:
        base_dir = temp_root / "base"
        _export_ref(args.base_ref, base_dir)
        inspector = OriginalProjectHarness()
        base = inspector.inspect(base_dir, args.runner)
        head = inspector.inspect(ROOT, args.runner)
        result = {
            "base_ref": args.base_ref,
            "runner": args.runner,
            "base": base.__dict__,
            "head": head.__dict__,
            "base_runtime_files": len(base.runtime_files),
            "head_runtime_files": len(head.runtime_files),
            "go_to_python_detected": ("motor_v3.go", "token_optimizer.py") in head.subprocess_edges
            or ("motor_v3.go", "python") in head.subprocess_edges
            or ("motor_v3.go", "python3") in head.subprocess_edges,
            "result": "PASS" if base.runtime_files and head.runtime_files else "FAIL",
        }
        if args.output:
            Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        return 0 if result["result"] == "PASS" else 1
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
