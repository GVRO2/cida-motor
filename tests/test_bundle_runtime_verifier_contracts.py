import hashlib
import json

import pytest

from cida.infrastructure.bundle_runtime_verifier import BundleRuntimeVerifier, find_bundle_root


def _write_manifest(root, file_entries, **overrides):
    tknd = root / "tknd"
    tknd.mkdir(parents=True, exist_ok=True)
    payload = {
        "format": "cida-bundle-manifest",
        "schema_version": 2,
        "source_manifest_sha256": "a" * 64,
        "files": file_entries,
    }
    payload.update(overrides)
    manifest = dict(payload)
    manifest["bundle_sha256"] = hashlib.sha256(
        json.dumps(
            {
                "format": payload.get("format"),
                "schema_version": payload.get("schema_version"),
                "source_manifest_sha256": payload.get("source_manifest_sha256"),
                "files": payload.get("files", []),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    (tknd / "bundle-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def test_bundle_runtime_verifier_accepts_valid_artifact_and_caches_manifest(tmp_path):
    root = tmp_path / "bundle"
    artifact = root / "doc.md"
    artifact.parent.mkdir()
    data = b"# doc\n"
    artifact.write_bytes(data)
    _write_manifest(
        root,
        [
            {
                "path": "doc.md",
                "artifact_type": "content_output",
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        ],
    )

    verifier = BundleRuntimeVerifier(root)
    verified = verifier.verify_artifact(artifact, expected_type="content_output")

    assert verified.bytes_data == data
    assert verifier.read_verified_bytes(artifact) == data
    assert verifier.manifest is verifier.manifest
    assert verifier.entries_by_path is verifier.entries_by_path
    assert find_bundle_root(artifact) == root.resolve()
    assert find_bundle_root(root) == root.resolve()
    assert find_bundle_root(tmp_path / "outside.txt") is None


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (None, "file entry"),
        ({"path": "", "artifact_type": "content", "size_bytes": 1, "sha256": "a" * 64}, "path"),
        ({"path": "../x", "artifact_type": "content", "size_bytes": 1, "sha256": "a" * 64}, "Unsafe"),
        ({"path": "x", "artifact_type": "", "size_bytes": 1, "sha256": "a" * 64}, "artifact type"),
        ({"path": "x", "artifact_type": "content", "size_bytes": -1, "sha256": "a" * 64}, "size"),
        ({"path": "x", "artifact_type": "content", "size_bytes": 1, "sha256": "bad"}, "sha256"),
    ],
)
def test_bundle_manifest_rejects_invalid_entry_shapes(tmp_path, entry, message):
    root = tmp_path / "bundle"
    _write_manifest(root, [entry])

    with pytest.raises(ValueError, match=message):
        BundleRuntimeVerifier(root).manifest


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"format": "other"}, "format"),
        ({"schema_version": 99}, "schema"),
        ({"files": {}}, "files must be a list"),
    ],
)
def test_bundle_manifest_rejects_invalid_manifest_shape(tmp_path, overrides, message):
    root = tmp_path / "bundle"
    _write_manifest(root, [], **overrides)

    with pytest.raises(ValueError, match=message):
        BundleRuntimeVerifier(root).manifest


def test_bundle_manifest_rejects_manifest_hash_mismatch(tmp_path):
    root = tmp_path / "bundle"
    manifest = _write_manifest(root, [])
    manifest["source_manifest_sha256"] = "b" * 64
    (root / "tknd" / "bundle-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="hash mismatch"):
        BundleRuntimeVerifier(root).manifest


def test_bundle_verifier_rejects_missing_wrong_type_size_hash_and_outside_artifact(tmp_path):
    root = tmp_path / "bundle"
    artifact = root / "doc.md"
    artifact.parent.mkdir()
    artifact.write_bytes(b"abc")
    _write_manifest(
        root,
        [
            {
                "path": "doc.md",
                "artifact_type": "content_output",
                "size_bytes": 3,
                "sha256": hashlib.sha256(b"abc").hexdigest(),
            }
        ],
    )
    verifier = BundleRuntimeVerifier(root)

    with pytest.raises(ValueError, match="missing"):
        verifier.verify_artifact(root / "missing.md")
    with pytest.raises(ValueError, match="type mismatch"):
        verifier.verify_artifact(artifact, expected_type="alias_index")
    with pytest.raises(ValueError, match="outside bundle root"):
        verifier.verify_artifact(tmp_path / "outside.md")

    artifact.write_bytes(b"abcd")
    with pytest.raises(ValueError, match="size mismatch"):
        verifier.verify_artifact(artifact)

    artifact.write_bytes(b"xyz")
    verifier.entries_by_path["doc.md"]["size_bytes"] = 3
    with pytest.raises(ValueError, match="hash mismatch"):
        verifier.verify_artifact(artifact)
