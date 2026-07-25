import json
import os
import subprocess
import sys
from pathlib import Path


def test_context_usage_benchmark_generates_passing_reports(tmp_path):
    output_json = tmp_path / "context-usage-report.json"
    output_md = tmp_path / "context-usage-report.md"
    env = os.environ.copy()
    env["TIKTOKEN_CACHE_DIR"] = str(Path(__file__).resolve().parent.parent / "resources")

    result = subprocess.run(
        [
            sys.executable,
            "benchmarks/context_usage_compare.py",
            "--output-json",
            str(output_json),
            "--output-markdown",
            str(output_md),
        ],
        cwd=str(Path(__file__).resolve().parent.parent),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    report = json.loads(output_json.read_text(encoding="utf-8"))

    assert report["schema_version"] == 1
    assert report["summary"]["overall_result"] == "PASS"
    assert report["summary"]["tknc_total_context_tokens"] < report["summary"]["original_total_context_tokens"]
    assert report["summary"]["tknc_accuracy"] >= report["summary"]["original_accuracy"]
    assert len(report["scenarios"]) == 15
    assert output_md.read_text(encoding="utf-8").startswith("# CIDA .tknc Context Usage Report")


def test_context_usage_benchmark_records_no_global_preload(tmp_path):
    output_json = tmp_path / "context-usage-report.json"
    env = os.environ.copy()
    env["TIKTOKEN_CACHE_DIR"] = str(Path(__file__).resolve().parent.parent / "resources")

    result = subprocess.run(
        [
            sys.executable,
            "benchmarks/context_usage_compare.py",
            "--output-json",
            str(output_json),
            "--output-markdown",
            str(tmp_path / "context-usage-report.md"),
        ],
        cwd=str(Path(__file__).resolve().parent.parent),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    report = json.loads(output_json.read_text(encoding="utf-8"))
    localized = [item for item in report["scenarios"] if len(item["tknc"]["aliases_detected"]) == 1]

    assert localized
    assert all(item["tknc"]["global_dictionary_preload"] is False for item in localized)
    assert all(len(item["tknc"]["chunks_loaded"]) <= 1 for item in localized)
