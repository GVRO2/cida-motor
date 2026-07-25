"""Static workflow lint tests.

These tests fail if the CI workflow contains masked commands or
continue-on-error on required gates. This prevents regressions where
CI failures are silently suppressed.
"""
import pathlib
import re

import pytest

WORKFLOW_PATH = pathlib.Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"

REQUIRED_GATE_JOB_NAMES = {
    "test",
    "io-failure-contract",
    "sidecar-path-security",
    "bundle-integrity",
    "go-lossless-auto-contract",
    "coverage",
    "benchmark",
    "compare",
}


def _load_workflow() -> str:
    assert WORKFLOW_PATH.exists(), f"Workflow file not found: {WORKFLOW_PATH}"
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _workflow_job_blocks(content: str) -> dict[str, list[str]]:
    jobs_started = False
    current_job = None
    jobs: dict[str, list[str]] = {}
    for line in content.splitlines():
        if re.match(r"^jobs:\s*$", line):
            jobs_started = True
            continue
        if not jobs_started:
            continue
        job_match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if job_match:
            current_job = job_match.group(1)
            jobs[current_job] = []
            continue
        if current_job and (line.startswith("    ") or not line.strip()):
            jobs[current_job].append(line)
    return jobs


class TestWorkflowLint:
    """Static analysis of CI workflow for masked commands and suppressed errors."""

    def test_no_or_true_in_critical_run_steps(self):
        """No '|| true' should appear in gate steps."""
        content = _load_workflow()
        lines_with_or_true = [
            (i + 1, line.rstrip())
            for i, line in enumerate(content.splitlines())
            if "|| true" in line
        ]
        assert not lines_with_or_true, (
            "Found '|| true' in CI workflow (masks errors) at lines:\n"
            + "\n".join(f"  L{ln}: {txt}" for ln, txt in lines_with_or_true)
        )

    def test_no_continue_on_error_true_in_required_gates(self):
        """Required gate jobs must not use 'continue-on-error: true'."""
        content = _load_workflow()
        matches = [
            (i + 1, line.rstrip())
            for i, line in enumerate(content.splitlines())
            if re.search(r"continue-on-error\s*:\s*true", line)
        ]
        assert not matches, (
            "Found 'continue-on-error: true' in CI workflow at lines:\n"
            + "\n".join(f"  L{ln}: {txt}" for ln, txt in matches)
        )

    def test_all_jobs_have_timeout(self):
        """Every job should have timeout-minutes set."""
        jobs = _workflow_job_blocks(_load_workflow())
        missing_timeout = [
            job_name
            for job_name, lines in jobs.items()
            if not any(re.match(r"^    timeout-minutes\s*:", line) for line in lines)
        ]
        assert not missing_timeout, f"Jobs missing timeout-minutes: {missing_timeout}"

    def test_sha_evidence_uses_python_not_sha1sum(self):
        """SHA evidence step must not use sha1sum."""
        content = _load_workflow()
        if "sha1sum" in content:
            lines_with_sha1sum = [
                (i + 1, line.rstrip())
                for i, line in enumerate(content.splitlines())
                if "sha1sum" in line
            ]
            pytest.fail(
                "Found 'sha1sum' in CI workflow at lines:\n"
                + "\n".join(f"  L{ln}: {txt}" for ln, txt in lines_with_sha1sum)
            )

    def test_required_jobs_present(self):
        """Required gate jobs must exist in the workflow."""
        jobs = set(_workflow_job_blocks(_load_workflow()).keys())
        required = {"test", "io-failure-contract", "sidecar-path-security", "bundle-integrity"}
        missing = required - jobs
        assert not missing, f"Required jobs missing from CI: {missing}"
