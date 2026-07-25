import subprocess
import sys
import textwrap
from pathlib import Path

from scripts.check_go_runtime_dependencies import (
    collect_go_dependency_violations,
    go_sum_violations,
    parse_go_mod,
)


def _violations(source: str):
    return parse_go_mod(textwrap.dedent(source))[1]


def test_go_mod_without_require_passes():
    module, violations = parse_go_mod("module cida-motor\n\ngo 1.26.5\n")

    assert module == "cida-motor"
    assert violations == []


def test_go_mod_direct_require_is_blocked():
    violations = _violations(
        """
        module cida-motor
        require github.com/acme/dep v1.2.3
        """
    )

    assert [(violation.line, violation.module, violation.reason) for violation in violations] == [
        (3, "github.com/acme/dep", "require")
    ]


def test_go_mod_indirect_require_is_blocked():
    violations = _violations(
        """
        module cida-motor
        require (
            github.com/acme/direct v1.0.0
            github.com/acme/indirect v1.0.0 // indirect
        )
        """
    )

    assert [violation.module for violation in violations] == [
        "github.com/acme/direct",
        "github.com/acme/indirect",
    ]


def test_go_mod_replace_local_without_require_is_allowed():
    violations = _violations(
        """
        module cida-motor
        replace example.com/local => ../local
        """
    )

    assert violations == []


def test_go_mod_replace_external_is_blocked():
    violations = _violations(
        """
        module cida-motor
        replace example.com/dep => github.com/acme/dep v1.0.0
        """
    )

    assert [(violation.module, violation.reason) for violation in violations] == [
        ("example.com/dep", "replace_external")
    ]


def test_go_mod_tool_dependency_is_blocked():
    violations = _violations(
        """
        module cida-motor
        tool github.com/acme/tool
        """
    )

    assert [(violation.module, violation.reason) for violation in violations] == [
        ("github.com/acme/tool", "tool")
    ]


def test_go_sum_entries_are_blocked():
    violations = go_sum_violations("github.com/acme/dep v1.0.0 h1:abc\n")

    assert [(violation.line, violation.module, violation.reason) for violation in violations] == [
        (1, "github.com/acme/dep", "go_sum")
    ]


def test_go_dependency_gate_passes_repository():
    result = subprocess.run(
        [sys.executable, "scripts/check_go_runtime_dependencies.py"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "GO_RUNTIME_DEPENDENCY_POLICY_PASSED" in result.stdout


def test_collect_reports_missing_go_mod(tmp_path: Path):
    assert collect_go_dependency_violations(tmp_path, run_go_list=False) == ["go.mod:0: missing go.mod"]
