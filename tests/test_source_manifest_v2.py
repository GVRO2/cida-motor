import json

from benchmarks.context_usage_compare import _build_tknc_corpus, _write_fixture_corpus


def test_production_writes_source_manifest_v2(tmp_path):
    original, relpaths = _write_fixture_corpus(tmp_path, "source-manifest", 600)
    tknc = tmp_path / "source-manifest" / "tknc"
    _build_tknc_corpus(original, tknc, relpaths)

    manifest = json.loads((tknc / "tknc-manifest.json").read_text(encoding="utf-8"))

    assert manifest["format"] == "cida-corpus-manifest"
    assert manifest["schema_version"] == 2
    assert manifest["manifest_sha256"]
    assert manifest["files"]
