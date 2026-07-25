import subprocess
import sys
import textwrap
from pathlib import Path

from scripts.check_runtime_dependencies import collect_violations


def _violations(source: str):
    return collect_violations(Path("sample.py"), textwrap.dedent(source))


def test_stdlib_import_allowed():
    assert _violations("import json\nfrom pathlib import Path\n") == []


def test_local_import_allowed():
    assert _violations("from cida.domain.errors import CidaError\nimport markdown.sidecar\n") == []


def test_tiktoken_exception_allowed():
    assert _violations("import tiktoken\n") == []


def test_yaml_import_blocked():
    violations = _violations("import yaml\n")
    assert violations == [(1, "yaml")]


def test_fictitious_external_import_blocked():
    violations = _violations("from imaginary_runtime_dep import thing\n")
    assert violations == [(1, "imaginary_runtime_dep")]


def test_relative_import_allowed():
    assert _violations("from .frontmatter_codec import FrontmatterCodec\n") == []


def test_dynamic_import_literals_block_forbidden_modules():
    violations = _violations("__import__('yaml')\n")
    assert violations == [(1, "yaml")]

    violations = _violations("import importlib\nimportlib.import_module('yaml')\n")
    assert violations == [(2, "yaml")]

    violations = _violations("from importlib import import_module\nimport_module('yaml')\n")
    assert violations == [(2, "yaml")]


def test_dynamic_import_non_literals_are_not_dependency_violations():
    assert _violations("__import__(name)\n") == []
    assert _violations("import importlib\nimportlib.import_module(name)\n") == []
    assert _violations("eval('1 + 1')\nexec('value = 1')\n") == []


def test_runtime_gate_passes_repository():
    result = subprocess.run(
        [sys.executable, "scripts/check_runtime_dependencies.py"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RUNTIME_DEPENDENCY_POLICY_PASSED" in result.stdout
