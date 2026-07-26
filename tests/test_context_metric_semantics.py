import json
import os
import subprocess
import sys
from pathlib import Path


def test_context_report_v4_separates_model_context_from_local_retrieval(tmp_path):
    output_json = tmp_path / "context-usage-report-v4.json"
    env = os.environ.copy()
    env["TIKTOKEN_CACHE_DIR"] = str(Path(__file__).resolve().parent.parent / "resources")

    result = subprocess.run(
        [
            sys.executable,
            "benchmarks/context_usage_compare.py",
            "--output-json",
            str(output_json),
            "--output-markdown",
            str(tmp_path / "context-usage-report-v4.md"),
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
    first = report["scenarios"][0]["tknc"]

    assert report["schema_version"] == 4
    assert "tokens_loaded_once" not in json.dumps(report)
    assert "model_total_context_tokens" in first["model_context"]
    assert "search_index_bytes" in first["local_retrieval"]
