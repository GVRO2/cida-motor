import subprocess
import sys
from pathlib import Path


def test_runtime_harness_recursive_audit_passes():
    result = subprocess.run(
        [sys.executable, "scripts/check_runtime_harness_references.py"],
        cwd=str(Path(__file__).resolve().parent.parent),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "files scanned" in result.stdout
