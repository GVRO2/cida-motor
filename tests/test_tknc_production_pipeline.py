import json
import os
from pathlib import Path

from benchmarks.context_usage_compare import _run_production_tknc, _write_fixture_corpus
from cida.application.selective_alias_resolution import ALIAS_INDEX_FILENAME


def test_context_benchmark_uses_production_cli_to_generate_tknc_artifacts(tmp_path):
    os.environ["TIKTOKEN_CACHE_DIR"] = str(Path(__file__).resolve().parent.parent / "resources")
    original, _ = _write_fixture_corpus(tmp_path, "production", 12)
    tknc = tmp_path / "tknc"

    outcome = _run_production_tknc(original, tknc, tmp_path / "report" / "context")

    assert outcome["exit_code"] == 0, outcome["stderr"] + outcome["stdout"]
    assert (tknc / "tknc-manifest.json").exists()
    assert (tknc / "tknd" / ALIAS_INDEX_FILENAME).exists()
    index = json.loads((tknc / "tknd" / ALIAS_INDEX_FILENAME).read_text(encoding="utf-8"))
    assert index["schema_version"] == 3
    assert index["membership"] == "EXACT_MEMBERSHIP"
    assert index["chunk_count"] >= 1
    assert index["segments"]
