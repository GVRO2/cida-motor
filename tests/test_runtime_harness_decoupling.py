import os
import sys
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

RUNTIME_FILES = [
    "motor_v3.go",
    "token_optimizer.py",
    "decompress.py",
    "translate.py",
    "cida/interfaces/cli.py",
    "cida/application/optimize_file.py",
    "cida/application/optimize_corpus.py",
    "cida/application/validate_sidecar.py",
    "cida/application/strict_auditing.py",
    "cida/application/decompress_file.py",
    "cida/domain/sidecar.py",
    "cida/domain/reconstruction.py",
    "cida/markdown/semantic_equivalence.py",
    "cida/infrastructure/filesystem.py",
    "cida/infrastructure/tokenizer.py",
]

FORBIDDEN_HARNESS_TERMS = [
    "harness",
    "CidaHarness",
    "Invoke-Cida",
    "INDEPENDENT_HARNESS",
    "POST_MERGE_REMEDIATION",
    "REMEDIATION_BLOCKED_BY_HARNESS",
    "powershell",
    "pwsh",
]


def test_no_harness_references_in_runtime():
    """Verify static absence of external harness references in product runtime source code."""
    for rel_path in RUNTIME_FILES:
        full_path = REPO_ROOT / rel_path
        if not full_path.exists():
            continue
        text = full_path.read_text(encoding="utf-8", errors="replace")
        for term in FORBIDDEN_HARNESS_TERMS:
            assert term not in text, f"Forbidden harness term '{term}' found in runtime file {rel_path}"


def test_runtime_executes_without_external_harness(tmp_path):
    """Verify runtime CLI operates normally when external harness directory is absent."""
    non_existent_harness = Path("C:/Users/KABUM/CidaHarness_NONEXISTENT_TEST")
    assert not non_existent_harness.exists()

    for validation_level in ("balanced", "strict"):
        src_dir = tmp_path / f"src-{validation_level}"
        dst_dir = tmp_path / f"dst-{validation_level}"
        src_dir.mkdir()
        (src_dir / "sample.md").write_text("# Hello World\nSample content.\n", encoding="utf-8")

        cmd = [
            sys.executable, "-m", "cida.interfaces.cli",
            "--src", str(src_dir),
            "--dst", str(dst_dir),
            "--validation-level", validation_level,
        ]
        env = os.environ.copy()
        env["TIKTOKEN_CACHE_DIR"] = str(REPO_ROOT / "resources")

        result = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True)

        combined_output = f"{result.stdout}\n{result.stderr}"
        assert result.returncode == 0
        assert "INDEPENDENT_HARNESS" not in combined_output
        assert "POST_MERGE_REMEDIATION" not in combined_output
        assert "HARNESS" not in combined_output.upper()
        assert (dst_dir / "sample.md").exists()
