import os
import sys
import subprocess
from pathlib import Path
from unittest.mock import patch

from cida.application.selective_alias_resolution import build_alias_index, SelectiveAliasResolver
from cida.infrastructure.filesystem import PhysicalFilesystem
from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec
from cida.interfaces.cli import main
from tests.runtime_harness_probe import RuntimeHarnessProbe


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


def test_python_runtime_has_zero_harness_activity_under_probe(tmp_path):
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    (src_dir / "sample.md").write_text("# Hello\n\nPlain runtime content.\n", encoding="utf-8")

    args = [
        "token_optimizer.py",
        "--src", str(src_dir),
        "--dst", str(dst_dir),
        "--validation-level", "balanced",
        "--report", "json",
        "--report-path", str(dst_dir / "report"),
    ]

    with patch.object(sys, "argv", args), RuntimeHarnessProbe() as probe:
        main()

    assert probe.counters == {
        "harness_imports": 0,
        "harness_file_reads": 0,
        "harness_subprocesses": 0,
        "harness_environment_accesses": 0,
        "harness_module_discovery": 0,
        "harness_initializations": 0,
        "harness_tokens_loaded": 0,
    }


def test_tknc_alias_lookup_has_zero_harness_activity_under_probe(tmp_path):
    tknd = tmp_path / "tknd"
    tknd.mkdir()
    fs = PhysicalFilesystem()
    hs = HashService()
    jc = JsonCodec()
    sidecar_data = {
        "format": "cida-token-sidecar",
        "version": 1,
        "source": "corpus",
        "source_sha256": "a" * 64,
        "entries": {"AA": "replacement"},
    }
    serialized = jc.encode(sidecar_data, indent=4)
    (tknd / "A0.cidatkn").write_text(serialized, encoding="utf-8", newline="\n")
    index_data = build_alias_index(
        {"AA": "A0.cidatkn"},
        "dict-1",
        {"A0.cidatkn": hs.sha256(serialized.encode("utf-8"))},
        hs,
        jc,
    )
    (tknd / "alias-index.json").write_text(jc.encode(index_data, indent=4), encoding="utf-8", newline="\n")

    resolver = SelectiveAliasResolver(fs, jc, hs)
    with RuntimeHarnessProbe() as probe:
        result = resolver.resolve({"AA"}, str(tknd))

    assert result.resolved == {"AA": "replacement"}
    assert result.chunks_loaded == ("A0.cidatkn",)
    assert all(value == 0 for value in probe.counters.values())
