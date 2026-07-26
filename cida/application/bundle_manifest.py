from typing import Any


BUNDLE_MANIFEST_FILENAME = "bundle-manifest.json"
BUNDLE_MANIFEST_FORMAT = "cida-bundle-manifest"
BUNDLE_MANIFEST_SCHEMA_VERSION = 2


def build_bundle_manifest(
    *,
    dst_abs: str,
    file_repo: Any,
    hash_service: Any,
    json_codec: Any,
    source_manifest_sha256: str,
    precomputed_hashes: dict[str, str] | None = None,
    precomputed_sizes: dict[str, int] | None = None,
    artifact_paths: set[str] | None = None,
) -> dict[str, Any]:
    files = []
    manifest_rel = f"tknd/{BUNDLE_MANIFEST_FILENAME}"
    precomputed_hashes = precomputed_hashes or {}
    precomputed_sizes = precomputed_sizes or {}
    if artifact_paths is None:
        paths = [
            (file_repo.relpath(path, dst_abs).replace("\\", "/"), path)
            for path in file_repo.list_files(dst_abs)
        ]
    else:
        paths = [
            (rel, file_repo.join(dst_abs, *rel.split("/")))
            for rel in artifact_paths
        ]
    for rel, path in sorted(paths):
        if rel == manifest_rel:
            continue
        artifact_type = _artifact_type(rel)
        if artifact_type == "other":
            continue
        files.append(
            {
                "path": rel,
                "sha256": precomputed_hashes.get(rel) or hash_service.sha256(file_repo.read_bytes(path)),
                "size_bytes": precomputed_sizes.get(rel) or file_repo.file_size(path),
                "artifact_type": artifact_type,
            }
        )
    payload: dict[str, Any] = {
        "format": BUNDLE_MANIFEST_FORMAT,
        "schema_version": BUNDLE_MANIFEST_SCHEMA_VERSION,
        "source_manifest_sha256": source_manifest_sha256,
        "files": files,
    }
    payload["bundle_sha256"] = hash_service.sha256(json_codec.canonical_encode(_canonical_payload(payload)).encode("utf-8"))
    return payload


def validate_bundle_manifest(manifest: dict[str, Any], *, hash_service: Any, json_codec: Any) -> None:
    if manifest.get("format") != BUNDLE_MANIFEST_FORMAT:
        raise ValueError(f"Unsupported bundle manifest format: {manifest.get('format')}")
    if manifest.get("schema_version") != BUNDLE_MANIFEST_SCHEMA_VERSION:
        raise ValueError(f"Unsupported bundle manifest schema: {manifest.get('schema_version')}")
    expected = manifest.get("bundle_sha256")
    actual = hash_service.sha256(json_codec.canonical_encode(_canonical_payload(manifest)).encode("utf-8"))
    if actual != expected:
        raise ValueError("Bundle manifest hash mismatch")


def _artifact_type(rel: str) -> str:
    if rel.endswith(".cidatkn"):
        return "alias_chunk"
    if rel == "tknd/alias-index.json":
        return "alias_index"
    if rel.startswith("tknd/segments/"):
        return "alias_segment"
    if rel == "tknd/content-search-index.json":
        return "content_search_index"
    if rel.startswith("tknd/search-index/"):
        return "content_search_segment"
    if rel == "tknc-manifest.json":
        return "source_manifest"
    if not rel.startswith("tknd/"):
        return "content_output"
    return "other"


def _canonical_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": manifest.get("format"),
        "schema_version": manifest.get("schema_version"),
        "source_manifest_sha256": manifest.get("source_manifest_sha256"),
        "files": manifest.get("files", []),
    }
