from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


@dataclass(frozen=True)
class VerifiedArtifact:
    path: str
    artifact_type: str
    size_bytes: int
    sha256: str
    bytes_data: bytes


class BundleRuntimeVerifier:
    def __init__(self, bundle_root: Path) -> None:
        self.bundle_root = bundle_root.resolve()
        self.manifest_path = self.bundle_root / "tknd" / "bundle-manifest.json"
        self._manifest: dict[str, Any] | None = None
        self._entries_by_path: dict[str, dict[str, Any]] | None = None

    @property
    def manifest(self) -> dict[str, Any]:
        if self._manifest is None:
            self._manifest = self._load_and_validate_manifest()
        return self._manifest

    @property
    def entries_by_path(self) -> dict[str, dict[str, Any]]:
        if self._entries_by_path is None:
            self._entries_by_path = {
                str(item["path"]): item
                for item in self.manifest.get("files", [])
                if isinstance(item, dict) and isinstance(item.get("path"), str)
            }
        return self._entries_by_path

    def verify_artifact(self, artifact_path: Path, *, expected_type: str | None = None) -> VerifiedArtifact:
        artifact_path = artifact_path.resolve()
        rel = _relative_posix(artifact_path, self.bundle_root)
        entry = self.entries_by_path.get(rel)
        if entry is None:
            raise ValueError(f"Artifact is missing from bundle manifest: {rel}")
        artifact_type = entry.get("artifact_type")
        if expected_type is not None and artifact_type != expected_type:
            raise ValueError(f"Bundle artifact type mismatch for {rel}: expected {expected_type}, got {artifact_type}")
        data = artifact_path.read_bytes()
        actual_size = len(data)
        expected_size = entry.get("size_bytes")
        if expected_size != actual_size:
            raise ValueError(f"Bundle artifact size mismatch for {rel}")
        actual_sha = hashlib.sha256(data).hexdigest()
        expected_sha = entry.get("sha256")
        if expected_sha != actual_sha:
            raise ValueError(f"Bundle artifact hash mismatch for {rel}")
        return VerifiedArtifact(
            path=rel,
            artifact_type=str(artifact_type),
            size_bytes=actual_size,
            sha256=actual_sha,
            bytes_data=data,
        )

    def read_verified_bytes(self, artifact_path: Path, *, expected_type: str | None = None) -> bytes:
        return self.verify_artifact(artifact_path, expected_type=expected_type).bytes_data

    def _load_and_validate_manifest(self) -> dict[str, Any]:
        data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if data.get("format") != "cida-bundle-manifest":
            raise ValueError(f"Unsupported bundle manifest format: {data.get('format')}")
        if data.get("schema_version") != 2:
            raise ValueError(f"Unsupported bundle manifest schema: {data.get('schema_version')}")
        files = data.get("files")
        if not isinstance(files, list):
            raise ValueError("Bundle manifest files must be a list")
        for item in files:
            _validate_entry_shape(item)
        expected = data.get("bundle_sha256")
        actual = hashlib.sha256(_canonical_manifest_bytes(data)).hexdigest()
        if actual != expected:
            raise ValueError("Bundle manifest hash mismatch")
        return data


def find_bundle_root(path: Path) -> Path | None:
    resolved = path.resolve()
    search_root = resolved if resolved.is_dir() else resolved.parent
    for candidate in (search_root, *search_root.parents):
        if (candidate / "tknd" / "bundle-manifest.json").exists():
            return candidate
    return None


def _validate_entry_shape(item: Any) -> None:
    if not isinstance(item, dict):
        raise ValueError("Bundle manifest file entry must be an object")
    rel = item.get("path")
    if not isinstance(rel, str) or not rel:
        raise ValueError("Bundle manifest file path is invalid")
    parsed = PurePosixPath(rel)
    if parsed.is_absolute() or ".." in parsed.parts or "\\" in rel:
        raise ValueError(f"Unsafe bundle manifest file path: {rel}")
    if not isinstance(item.get("artifact_type"), str) or not item["artifact_type"]:
        raise ValueError(f"Bundle manifest artifact type is invalid: {rel}")
    if not isinstance(item.get("size_bytes"), int) or item["size_bytes"] < 0:
        raise ValueError(f"Bundle manifest size is invalid: {rel}")
    sha = item.get("sha256")
    if not isinstance(sha, str) or len(sha) != 64 or any(ch not in "0123456789abcdef" for ch in sha):
        raise ValueError(f"Bundle manifest sha256 is invalid: {rel}")


def _relative_posix(path: Path, root: Path) -> str:
    try:
        rel = path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Artifact is outside bundle root: {path}") from exc
    return rel.as_posix()


def _canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    payload = {
        "format": manifest.get("format"),
        "schema_version": manifest.get("schema_version"),
        "source_manifest_sha256": manifest.get("source_manifest_sha256"),
        "files": manifest.get("files", []),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
