import json
import os
import subprocess
import sys
from pathlib import Path


def test_full_vs_full_contract(tmp_path):
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

    assert report["schema_version"] == 5
    assert report["summary"]["overall_result"] == "PASS"
    assert report["summary"]["full_vs_full"]["result"] == "PASS"
    assert report["summary"]["selective_warm"]["result"] == "PASS"
    assert report["summary"]["multi_query"]["result"] == "PASS"
    assert report["summary"]["lookup"]["result"] == "PASS"
    assert report["summary"]["search"]["result"] == "PASS"
    assert report["cache"]["result"] == "PASS"
    assert report["summary"]["accuracy"]["tknc_score"] >= report["summary"]["accuracy"]["original_score"]
    assert len(report["scenarios"]) == 24
    assert "hundred_chunks" in report["sessions_by_corpus"]
    assert output_md.read_text(encoding="utf-8").startswith("# CIDA .tknc Context Usage Report v5")


def test_lookup_reads_only_required_chunks(tmp_path):
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
    localized = [item for item in report["scenarios"] if item["tknc"]["aliases_detected"]]

    assert localized
    assert all(item["tknc"]["global_dictionary_preload"] is False for item in localized)
    assert all(set(item["tknc"]["chunks_loaded"]) == set(item["tknc"]["required_chunks"]) for item in localized)


def test_selective_cold_contract():
    from benchmarks.context_usage_compare import _summarize

    scenario = {
        "original": {"full_total_context_tokens": 1000, "total_context_tokens": 100},
        "tknc": {
            "full_total_context_tokens": 500,
            "total_context_tokens": 200,
            "lookup_pass": True,
            "all_aliases_resolvable": True,
            "global_dictionary_preload": False,
            "token_accounting_pass": True,
            "read_events": [],
        },
        "accuracy": {"original": {"accuracy": 1.0}, "tknc": {"accuracy": 1.0}},
    }
    sessions = {
        "result": "PASS",
        "break_even_query_count": 50,
        "query_counts": {
            "1": {"original": 100, "tknc": 200, "delta": -100},
            "10": {"original": 1000, "tknc": 900, "delta": 100},
            "50": {"original": 5000, "tknc": 3000, "delta": 2000},
            "100": {"original": 10000, "tknc": 5000, "delta": 5000},
        },
    }

    report = _summarize("head", {"scale": {"alias_count": 500, "chunk_count": 100}}, [scenario], {"scale": {"exit_code": 0}}, sessions)

    assert report["summary"]["selective_cold"]["required"] is False
    assert report["summary"]["selective_cold"]["result"] == "INFORMATIONAL"
    assert report["summary"]["overall_result"] == "PASS"


def test_selective_warm_session_contract():
    from benchmarks.context_usage_compare import _summarize

    sessions = {
        "result": "PASS",
        "break_even_query_count": 50,
        "query_counts": {
            "1": {"original": 100, "tknc": 200, "delta": -100},
            "10": {"original": 1000, "tknc": 900, "delta": 100},
            "50": {"original": 5000, "tknc": 3000, "delta": 2000},
            "100": {"original": 10000, "tknc": 5000, "delta": 5000},
        },
    }
    scenario = {
        "original": {"full_total_context_tokens": 1000, "total_context_tokens": 100},
        "tknc": {
            "full_total_context_tokens": 500,
            "total_context_tokens": 200,
            "lookup_pass": True,
            "all_aliases_resolvable": True,
            "global_dictionary_preload": False,
            "token_accounting_pass": True,
            "read_events": [],
        },
        "accuracy": {"original": {"accuracy": 1.0}, "tknc": {"accuracy": 1.0}},
    }

    report = _summarize("head", {"scale": {"alias_count": 500, "chunk_count": 100}}, [scenario], {"scale": {"exit_code": 0}}, sessions)

    assert report["summary"]["selective_warm"]["result"] == "PASS"


def test_multi_query_break_even_contract():
    from benchmarks.context_usage_compare import _summarize

    sessions = {
        "result": "PASS",
        "break_even_query_count": 50,
        "query_counts": {
            "1": {"original": 100, "tknc": 200, "delta": -100},
            "10": {"original": 1000, "tknc": 900, "delta": 100},
            "50": {"original": 5000, "tknc": 3000, "delta": 2000},
            "100": {"original": 10000, "tknc": 5000, "delta": 5000},
        },
    }
    scenario = {
        "original": {"full_total_context_tokens": 1000, "total_context_tokens": 100},
        "tknc": {
            "full_total_context_tokens": 500,
            "total_context_tokens": 200,
            "lookup_pass": True,
            "all_aliases_resolvable": True,
            "global_dictionary_preload": False,
            "token_accounting_pass": True,
            "read_events": [],
        },
        "accuracy": {"original": {"accuracy": 1.0}, "tknc": {"accuracy": 1.0}},
    }

    report = _summarize("head", {"scale": {"alias_count": 500, "chunk_count": 100}}, [scenario], {"scale": {"exit_code": 0}}, sessions)

    assert report["summary"]["multi_query"]["break_even_query_count"] == 50
    assert report["summary"]["multi_query"]["result"] == "PASS"
