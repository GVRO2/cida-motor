import json

import pytest

from benchmarks.context_usage_compare import _build_tknc_corpus, _write_fixture_corpus
from cida.application.bundle_manifest import build_bundle_manifest, validate_bundle_manifest
from cida.infrastructure.filesystem import PhysicalFilesystem
from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec


def test_production_writes_bundle_manifest_v2_for_outputs_and_lookup_artifacts(tmp_path):
    original, relpaths = _write_fixture_corpus(tmp_path, "bundle-manifest", 600)
    tknc = tmp_path / "bundle-manifest" / "tknc"
    _build_tknc_corpus(original, tknc, relpaths)

    manifest_path = tknc / "tknd" / "bundle-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact_types = {item["artifact_type"] for item in manifest["files"]}

    assert manifest["format"] == "cida-bundle-manifest"
    assert manifest["schema_version"] == 2
    assert "content_output" in artifact_types
    assert "alias_index" in artifact_types
    assert "alias_chunk" in artifact_types
    assert "alias_segment" in artifact_types
    assert "content_search_index" in artifact_types
    assert "content_search_segment" in artifact_types
    assert "tknd/bundle-manifest.json" not in {item["path"] for item in manifest["files"]}
    validate_bundle_manifest(manifest, hash_service=HashService(), json_codec=JsonCodec())


def test_bundle_manifest_skips_self_and_unknown_tknd_artifacts(tmp_path):
    hs = HashService()
    jc = JsonCodec()
    repo = PhysicalFilesystem()
    dst = tmp_path / "bundle"
    (dst / "tknd").mkdir(parents=True)
    (dst / "app.py.tknc").write_text("content", encoding="utf-8")
    (dst / "tknd" / "alias-index.json").write_text("{}", encoding="utf-8")
    (dst / "tknd" / "bundle-manifest.json").write_text("old", encoding="utf-8")
    (dst / "tknd" / "unknown.tmp").write_text("skip", encoding="utf-8")

    manifest = build_bundle_manifest(
        dst_abs=str(dst),
        file_repo=repo,
        hash_service=hs,
        json_codec=jc,
        source_manifest_sha256=hs.sha256(b"source"),
    )

    paths = {item["path"] for item in manifest["files"]}
    assert paths == {"app.py.tknc", "tknd/alias-index.json"}
    validate_bundle_manifest(manifest, hash_service=hs, json_codec=jc)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda manifest: manifest.update({"format": "wrong"}), "format"),
        (lambda manifest: manifest.update({"schema_version": 99}), "schema"),
        (lambda manifest: manifest.update({"files": []}), "hash"),
    ],
)
def test_bundle_manifest_rejects_invalid_v2_payloads(tmp_path, mutation, message):
    hs = HashService()
    jc = JsonCodec()
    repo = PhysicalFilesystem()
    dst = tmp_path / "bundle"
    dst.mkdir()
    (dst / "app.py.tknc").write_text("content", encoding="utf-8")
    manifest = build_bundle_manifest(
        dst_abs=str(dst),
        file_repo=repo,
        hash_service=hs,
        json_codec=jc,
        source_manifest_sha256=hs.sha256(b"source"),
    )
    mutation(manifest)

    with pytest.raises(ValueError, match=message):
        validate_bundle_manifest(manifest, hash_service=hs, json_codec=jc)
