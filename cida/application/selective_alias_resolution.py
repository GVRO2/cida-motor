import re
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from cida.domain.alias_codec import DEFAULT_ALIAS_CODEC, AliasCodec
from cida.domain.errors import SidecarValidationError
from cida.domain.sidecar import validate_sidecar_schema


ALIAS_INDEX_FILENAME = "alias-index.json"
ALIAS_INDEX_FORMAT = "cida-alias-index"
ALIAS_SEGMENT_FORMAT = "cida-alias-segment"
ALIAS_INDEX_SCHEMA_VERSION = 3
ALIAS_INDEX_LEGACY_SCHEMA_VERSION = 2
ALIAS_SEGMENT_SCHEMA_VERSION = 1
EXACT_MEMBERSHIP = "EXACT_MEMBERSHIP"
APPROXIMATE_MEMBERSHIP = "APPROXIMATE_MEMBERSHIP"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CHUNK_FILENAME_RE = re.compile(r"^chunk-[0-9]{6}\.cidatkn$")
SEGMENT_FILENAME_RE = re.compile(r"^segments/segment-[0-9]+-[UL]-[A-Za-z]\.json$")
MAX_ALIAS_COUNT = 100_000
MAX_CHUNKS = 500
MAX_SEGMENTS = 2_000
MAX_VALUE_LENGTH = 100_000
MAX_TOTAL_RESOLVED_BYTES = 2_000_000


def corpus_chunk_filename(chunk_index: int) -> str:
    if chunk_index < 0:
        raise ValueError(f"chunk_index must be non-negative: {chunk_index}")
    return f"chunk-{chunk_index:06d}.cidatkn"


@dataclass(frozen=True)
class AliasResolutionResult:
    resolved: dict[str, str]
    unresolved: set[str]
    chunks_loaded: tuple[str, ...]
    entries_loaded: int
    bytes_loaded: int
    tokens_loaded: int
    index_parse_duration_ms: float = 0.0
    sidecar_parse_duration_ms: float = 0.0
    alias_resolution_duration_ms: float = 0.0
    segments_loaded: tuple[str, ...] = ()
    segment_parse_duration_ms: float = 0.0
    membership_mode: str = EXACT_MEMBERSHIP


@dataclass(frozen=True)
class AliasIndexArtifacts:
    root: dict[str, Any]
    segments: dict[str, dict[str, Any]]


class AliasDetector:
    """Detect structurally valid aliases without receiving expected aliases."""

    _string_re = re.compile(r"(['\"])(?:\\.|(?!\1).)*\1")

    def __init__(self, codec: AliasCodec = DEFAULT_ALIAS_CODEC) -> None:
        self.codec = codec

    def candidates(self, text: str) -> set[str]:
        if not isinstance(text, str) or not text:
            return set()
        scrubbed = self._string_re.sub(" ", text)
        return {
            match.group(0)
            for match in self.codec.candidate_pattern().finditer(scrubbed)
            if self.codec.is_structurally_valid(match.group(0))
        }

    def detect(self, text: str, index: dict[str, Any]) -> set[str]:
        candidates = self.candidates(text)
        if index.get("schema_version") == ALIAS_INDEX_SCHEMA_VERSION:
            exact_aliases = index.get("exact_aliases")
            if isinstance(exact_aliases, list):
                return candidates & set(exact_aliases)
            return set()
        if index.get("schema_version") == ALIAS_INDEX_LEGACY_SCHEMA_VERSION or "ranges" in index:
            ranges = index.get("ranges", [])
            if not isinstance(ranges, list):
                return set()
            range_pairs: list[tuple[str, str]] = []
            for item in ranges:
                if not isinstance(item, dict):
                    continue
                first_alias = item.get("first_alias")
                last_alias = item.get("last_alias")
                if isinstance(first_alias, str) and isinstance(last_alias, str):
                    range_pairs.append((first_alias, last_alias))
            return {
                alias
                for alias in candidates
                if any(first_alias <= alias <= last_alias for first_alias, last_alias in range_pairs)
            }
        return set()


def _safe_chunk_filename(filename: str) -> str:
    if not isinstance(filename, str) or not filename.endswith(".cidatkn"):
        raise SidecarValidationError(f"Invalid sidecar chunk filename: {filename}")
    normalized = filename.replace("\\", "/")
    if "/" in normalized or normalized in (".cidatkn", ALIAS_INDEX_FILENAME):
        raise SidecarValidationError(f"Unsafe sidecar chunk filename: {filename}")
    return normalized


def _safe_segment_path(path: str) -> str:
    if not isinstance(path, str):
        raise SidecarValidationError(f"Invalid alias segment path: {path}")
    normalized = path.replace("\\", "/")
    parsed = PurePosixPath(normalized)
    if parsed.is_absolute() or ".." in parsed.parts:
        raise SidecarValidationError(f"Unsafe alias segment path: {path}")
    if SEGMENT_FILENAME_RE.fullmatch(normalized) is None:
        raise SidecarValidationError(f"Invalid alias segment path: {path}")
    return normalized


def _canonical_v2_index_payload(index_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": index_data.get("format"),
        "schema_version": index_data.get("schema_version"),
        "dictionary_id": index_data.get("dictionary_id"),
        "manifest_sha256": index_data.get("manifest_sha256"),
        "alias_count": index_data.get("alias_count"),
        "chunk_count": index_data.get("chunk_count"),
        "ranges": index_data.get("ranges", []),
        "chunks": index_data.get("chunks", {}),
    }


def _canonical_v3_index_payload(index_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": index_data.get("format"),
        "schema_version": index_data.get("schema_version"),
        "corpus_id": index_data.get("corpus_id"),
        "source_manifest_sha256": index_data.get("source_manifest_sha256"),
        "dictionary_id": index_data.get("dictionary_id"),
        "alias_codec_version": index_data.get("alias_codec_version"),
        "membership": index_data.get("membership"),
        "alias_count": index_data.get("alias_count"),
        "chunk_count": index_data.get("chunk_count"),
        "segment_count": index_data.get("segment_count"),
        "segments": index_data.get("segments", {}),
        "chunks": index_data.get("chunks", {}),
    }


def _canonical_segment_payload(segment_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": segment_data.get("format"),
        "schema_version": segment_data.get("schema_version"),
        "corpus_id": segment_data.get("corpus_id"),
        "segment_id": segment_data.get("segment_id"),
        "alias_codec_version": segment_data.get("alias_codec_version"),
        "aliases": segment_data.get("aliases", {}),
    }


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _segment_path(segment_id: str) -> str:
    return f"segments/segment-{segment_id}.json"


def build_alias_index_artifacts(
    alias_to_chunk: dict[str, str],
    dictionary_id: str,
    chunk_hashes: dict[str, str],
    hash_service: Any,
    json_codec: Any,
    manifest_sha256: str | None = None,
    chunk_entry_counts: dict[str, int] | None = None,
    chunk_entries_sha256: dict[str, str] | None = None,
    codec: AliasCodec = DEFAULT_ALIAS_CODEC,
) -> AliasIndexArtifacts:
    if len(alias_to_chunk) > MAX_ALIAS_COUNT:
        raise SidecarValidationError(f"Alias index exceeds max_alias_count={MAX_ALIAS_COUNT}")
    if len(chunk_hashes) > MAX_CHUNKS:
        raise SidecarValidationError(f"Alias index exceeds max_chunks={MAX_CHUNKS}")
    manifest_sha256 = manifest_sha256 or dictionary_id
    if not _is_sha256(dictionary_id):
        raise SidecarValidationError("Alias index dictionary_id must be a SHA-256 hex digest")
    if not _is_sha256(manifest_sha256):
        raise SidecarValidationError("Alias index manifest_sha256 must be a SHA-256 hex digest")

    aliases: dict[str, str] = {}
    for alias, chunk_name in alias_to_chunk.items():
        if not codec.is_structurally_valid(alias):
            raise SidecarValidationError(f"Malformed alias rejected: {alias}")
        safe_name = _safe_chunk_filename(chunk_name)
        if CHUNK_FILENAME_RE.fullmatch(safe_name) is None:
            raise SidecarValidationError(f"Invalid corpus chunk filename: {safe_name}")
        aliases[alias] = safe_name
    aliases = dict(sorted(aliases.items(), key=lambda item: codec.decode_alias(item[0]).ordinal))

    chunks = {}
    for chunk_name, digest in sorted(chunk_hashes.items()):
        safe_name = _safe_chunk_filename(chunk_name)
        if CHUNK_FILENAME_RE.fullmatch(safe_name) is None:
            raise SidecarValidationError(f"Invalid corpus chunk filename: {safe_name}")
        if not _is_sha256(digest):
            raise SidecarValidationError(f"Invalid sidecar chunk hash for {safe_name}")
        entry_count = (chunk_entry_counts or {}).get(safe_name)
        if not isinstance(entry_count, int) or entry_count < 0:
            raise SidecarValidationError(f"Missing entry_count for sidecar chunk: {safe_name}")
        metadata: dict[str, Any] = {"sha256": digest, "entry_count": entry_count}
        entries_digest = (chunk_entries_sha256 or {}).get(safe_name)
        if entries_digest is not None:
            if not _is_sha256(entries_digest):
                raise SidecarValidationError(f"Invalid entries_sha256 for sidecar chunk: {safe_name}")
            metadata["entries_sha256"] = entries_digest
        chunks[safe_name] = metadata

    referenced_chunks = set(aliases.values())
    if referenced_chunks != set(chunks):
        missing = sorted(referenced_chunks - set(chunks))
        extra = sorted(set(chunks) - referenced_chunks)
        raise SidecarValidationError(f"Alias index chunk metadata mismatch: missing={missing}, extra={extra}")

    segment_aliases: dict[str, dict[str, str]] = {}
    for alias, chunk_name in aliases.items():
        segment_aliases.setdefault(codec.segment_id(alias), {})[alias] = chunk_name
    if len(segment_aliases) > MAX_SEGMENTS:
        raise SidecarValidationError(f"Alias index exceeds max_segments={MAX_SEGMENTS}")

    segment_payloads: dict[str, dict[str, Any]] = {}
    segment_metadata: dict[str, dict[str, Any]] = {}
    for segment_id, values in sorted(segment_aliases.items()):
        segment_data: dict[str, Any] = {
            "format": ALIAS_SEGMENT_FORMAT,
            "schema_version": ALIAS_SEGMENT_SCHEMA_VERSION,
            "corpus_id": manifest_sha256,
            "segment_id": segment_id,
            "alias_codec_version": codec.version,
            "aliases": dict(sorted(values.items(), key=lambda item: codec.decode_alias(item[0]).ordinal)),
        }
        segment_hash = hash_service.sha256(json_codec.canonical_encode(_canonical_segment_payload(segment_data)).encode("utf-8"))
        segment_data["segment_sha256"] = segment_hash
        path = _segment_path(segment_id)
        segment_payloads[path] = segment_data
        segment_metadata[segment_id] = {
            "path": path,
            "sha256": segment_hash,
            "alias_count": len(values),
        }

    index_data: dict[str, Any] = {
        "format": ALIAS_INDEX_FORMAT,
        "schema_version": ALIAS_INDEX_SCHEMA_VERSION,
        "corpus_id": manifest_sha256,
        "source_manifest_sha256": manifest_sha256,
        "dictionary_id": dictionary_id,
        "alias_codec_version": codec.version,
        "membership": EXACT_MEMBERSHIP,
        "alias_count": len(aliases),
        "chunk_count": len(chunks),
        "segment_count": len(segment_metadata),
        "segments": segment_metadata,
        "chunks": chunks,
    }
    payload_bytes = json_codec.canonical_encode(_canonical_v3_index_payload(index_data)).encode("utf-8")
    index_data["index_sha256"] = hash_service.sha256(payload_bytes)
    return AliasIndexArtifacts(root=index_data, segments=segment_payloads)


def build_alias_index(
    alias_to_chunk: dict[str, str],
    dictionary_id: str,
    chunk_hashes: dict[str, str],
    hash_service: Any,
    json_codec: Any,
    manifest_sha256: str | None = None,
    chunk_entry_counts: dict[str, int] | None = None,
    chunk_entries_sha256: dict[str, str] | None = None,
) -> dict[str, Any]:
    return build_alias_index_artifacts(
        alias_to_chunk,
        dictionary_id,
        chunk_hashes,
        hash_service,
        json_codec,
        manifest_sha256=manifest_sha256,
        chunk_entry_counts=chunk_entry_counts,
        chunk_entries_sha256=chunk_entries_sha256,
    ).root


def build_alias_index_v2(
    alias_to_chunk: dict[str, str],
    dictionary_id: str,
    chunk_hashes: dict[str, str],
    hash_service: Any,
    json_codec: Any,
    manifest_sha256: str | None = None,
    chunk_entry_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    manifest_sha256 = manifest_sha256 or dictionary_id
    if not _is_sha256(dictionary_id):
        raise SidecarValidationError("Alias index dictionary_id must be a SHA-256 hex digest")
    if not _is_sha256(manifest_sha256):
        raise SidecarValidationError("Alias index manifest_sha256 must be a SHA-256 hex digest")
    aliases: dict[str, str] = {}
    for alias, chunk_name in sorted(alias_to_chunk.items()):
        if not DEFAULT_ALIAS_CODEC.is_structurally_valid(alias):
            raise SidecarValidationError(f"Malformed alias rejected: {alias}")
        aliases[alias] = _safe_chunk_filename(chunk_name)
    chunks = {}
    for chunk_name, digest in sorted(chunk_hashes.items()):
        safe_name = _safe_chunk_filename(chunk_name)
        entry_count = (chunk_entry_counts or {}).get(safe_name)
        if not isinstance(entry_count, int) or entry_count < 0:
            raise SidecarValidationError(f"Missing entry_count for sidecar chunk: {safe_name}")
        chunks[safe_name] = {"sha256": digest, "entry_count": entry_count}
    ranges = []
    for chunk_name in sorted(chunks):
        chunk_aliases = sorted(alias for alias, mapped in aliases.items() if mapped == chunk_name)
        if not chunk_aliases:
            raise SidecarValidationError(f"Alias index chunk has no aliases: {chunk_name}")
        ranges.append({"first_alias": chunk_aliases[0], "last_alias": chunk_aliases[-1], "path": chunk_name})
    index_data: dict[str, Any] = {
        "format": ALIAS_INDEX_FORMAT,
        "schema_version": ALIAS_INDEX_LEGACY_SCHEMA_VERSION,
        "dictionary_id": dictionary_id,
        "manifest_sha256": manifest_sha256,
        "membership": APPROXIMATE_MEMBERSHIP,
        "alias_count": len(aliases),
        "chunk_count": len(chunks),
        "ranges": ranges,
        "chunks": chunks,
    }
    index_data["index_sha256"] = hash_service.sha256(
        json_codec.canonical_encode(_canonical_v2_index_payload(index_data)).encode("utf-8")
    )
    return index_data


class SelectiveAliasResolver:
    def __init__(
        self,
        file_repo: Any,
        json_codec: Any,
        hash_service: Any,
        token_counter: Any | None = None,
        max_index_bytes: int = 2_000_000,
        max_segment_bytes: int = 1_000_000,
        max_sidecar_bytes: int = 2_000_000,
        max_entries_per_chunk: int = 1_000,
        max_alias_count: int = MAX_ALIAS_COUNT,
        max_chunks: int = MAX_CHUNKS,
        max_segments: int = MAX_SEGMENTS,
        max_value_length: int = MAX_VALUE_LENGTH,
        max_total_resolved_bytes: int = MAX_TOTAL_RESOLVED_BYTES,
        codec: AliasCodec = DEFAULT_ALIAS_CODEC,
    ):
        self.file_repo = file_repo
        self.json_codec = json_codec
        self.hash_service = hash_service
        self.token_counter = token_counter
        self.max_index_bytes = max_index_bytes
        self.max_segment_bytes = max_segment_bytes
        self.max_sidecar_bytes = max_sidecar_bytes
        self.max_entries_per_chunk = max_entries_per_chunk
        self.max_alias_count = max_alias_count
        self.max_chunks = max_chunks
        self.max_segments = max_segments
        self.max_value_length = max_value_length
        self.max_total_resolved_bytes = max_total_resolved_bytes
        self.codec = codec

    def resolve(self, aliases: set[str], tknd_dir: str) -> AliasResolutionResult:
        start = time.perf_counter()
        requested = self._validate_requested_aliases(aliases)
        if not requested:
            return AliasResolutionResult({}, set(), tuple(), 0, 0, 0)
        index_path = self.file_repo.join(tknd_dir, ALIAS_INDEX_FILENAME)
        if self.file_repo.exists(index_path):
            return self._resolve_with_index(requested, tknd_dir, index_path, start)
        return self._resolve_legacy_single_sidecar(requested, tknd_dir, start)

    def locate_aliases(self, aliases: set[str], tknd_dir: str) -> dict[str, str]:
        requested = self._validate_requested_aliases(aliases)
        if not requested:
            return {}
        index_path = self.file_repo.join(tknd_dir, ALIAS_INDEX_FILENAME)
        if not self.file_repo.exists(index_path):
            return {}
        index_text, _ = self._read_limited_text(index_path, self.max_index_bytes, operation="lookup", reason="locate_alias_index")
        index_data = self.json_codec.decode(index_text)
        self._validate_index(index_data)
        if index_data.get("schema_version") == ALIAS_INDEX_SCHEMA_VERSION:
            return self._locate_v3_aliases(requested, tknd_dir, index_data)[0]
        return self._locate_v2_aliases(requested, index_data)

    def _validate_requested_aliases(self, aliases: set[str]) -> set[str]:
        requested = set(aliases)
        for alias in requested:
            if not self.codec.is_structurally_valid(alias):
                raise SidecarValidationError(f"Malformed alias rejected: {alias}")
        return requested

    def _read_limited_text(
        self,
        path: str,
        max_bytes: int,
        *,
        operation: str = "read",
        reason: str = "",
        query_id: str = "",
    ) -> tuple[str, int]:
        if hasattr(self.file_repo, "read_bytes_limited"):
            try:
                raw = self.file_repo.read_bytes_limited(
                    path,
                    max_bytes,
                    operation=operation,
                    reason=reason,
                    query_id=query_id,
                )
            except TypeError:
                raw = self.file_repo.read_bytes_limited(path, max_bytes)
        else:
            if hasattr(self.file_repo, "file_size") and self.file_repo.file_size(path) > max_bytes:
                raise SidecarValidationError(f"Sidecar artifact exceeds size limit before read: {path}")
            raw = self.file_repo.read_bytes(path)
        byte_count = len(raw)
        if byte_count > max_bytes:
            raise SidecarValidationError(f"Sidecar artifact exceeds size limit: {path}")
        try:
            return raw.decode("utf-8"), byte_count
        except UnicodeDecodeError as exc:
            raise SidecarValidationError(f"Invalid UTF-8 sidecar artifact: {path}") from exc

    def _count_tokens(self, text: str) -> int:
        if self.token_counter is None:
            return 0
        return self.token_counter.count(text)

    def _validate_index(self, index_data: dict[str, Any]) -> None:
        if not isinstance(index_data, dict):
            raise SidecarValidationError("Alias index must be a JSON object")
        if index_data.get("format") != ALIAS_INDEX_FORMAT:
            raise SidecarValidationError(f"Unsupported alias index format: {index_data.get('format')}")
        schema_version = index_data.get("schema_version")
        if schema_version == ALIAS_INDEX_SCHEMA_VERSION:
            self._validate_v3_index(index_data)
            return
        if schema_version == ALIAS_INDEX_LEGACY_SCHEMA_VERSION:
            self._validate_v2_index(index_data)
            return
        raise SidecarValidationError(f"Unsupported alias index schema: {schema_version}")

    def _validate_common_index_fields(self, index_data: dict[str, Any]) -> None:
        if not _is_sha256(index_data.get("dictionary_id")):
            raise SidecarValidationError("Alias index dictionary_id must be a SHA-256 hex digest")
        alias_count = index_data.get("alias_count")
        chunk_count = index_data.get("chunk_count")
        if not isinstance(alias_count, int) or alias_count < 0 or alias_count > self.max_alias_count:
            raise SidecarValidationError("Alias index alias_count is invalid")
        if not isinstance(chunk_count, int) or chunk_count < 0 or chunk_count > self.max_chunks:
            raise SidecarValidationError("Alias index chunk_count is invalid")
        chunks = index_data.get("chunks", {})
        if not isinstance(chunks, dict):
            raise SidecarValidationError("Alias index chunks must be an object")
        if len(chunks) != chunk_count:
            raise SidecarValidationError("Alias index chunk_count does not match chunks")
        total_entries = 0
        for chunk_name, metadata in chunks.items():
            safe_name = _safe_chunk_filename(chunk_name)
            if CHUNK_FILENAME_RE.fullmatch(safe_name) is None:
                raise SidecarValidationError(f"Invalid corpus chunk filename: {safe_name}")
            if not isinstance(metadata, dict):
                raise SidecarValidationError(f"Alias index chunk metadata must be an object: {chunk_name}")
            digest = metadata.get("sha256")
            if not _is_sha256(digest):
                raise SidecarValidationError(f"Invalid alias index chunk hash: {chunk_name}")
            entry_count = metadata.get("entry_count")
            if not isinstance(entry_count, int) or entry_count < 0 or entry_count > self.max_entries_per_chunk:
                raise SidecarValidationError(f"Invalid alias index chunk entry_count: {chunk_name}")
            entries_digest = metadata.get("entries_sha256")
            if entries_digest is not None and not _is_sha256(entries_digest):
                raise SidecarValidationError(f"Invalid alias index entries_sha256: {chunk_name}")
            total_entries += entry_count
        if total_entries != alias_count:
            raise SidecarValidationError("Alias index alias_count does not match chunk entry counts")

    def _validate_v3_index(self, index_data: dict[str, Any]) -> None:
        self._validate_common_index_fields(index_data)
        for key in ("corpus_id", "source_manifest_sha256"):
            if not _is_sha256(index_data.get(key)):
                raise SidecarValidationError(f"Alias index {key} must be a SHA-256 hex digest")
        if index_data.get("alias_codec_version") != self.codec.version:
            raise SidecarValidationError("Alias index codec version mismatch")
        if index_data.get("membership") != EXACT_MEMBERSHIP:
            raise SidecarValidationError("Alias index v3 must use exact membership")
        segments = index_data.get("segments")
        segment_count = index_data.get("segment_count")
        if not isinstance(segment_count, int) or segment_count < 0 or segment_count > self.max_segments:
            raise SidecarValidationError("Alias index segment_count is invalid")
        if not isinstance(segments, dict):
            raise SidecarValidationError("Alias index segments must be an object")
        if len(segments) != segment_count:
            raise SidecarValidationError("Alias index segment_count does not match segments")
        segment_alias_total = 0
        for segment_id, metadata in segments.items():
            if not isinstance(segment_id, str) or not segment_id:
                raise SidecarValidationError("Alias index segment_id is invalid")
            if not isinstance(metadata, dict):
                raise SidecarValidationError(f"Alias index segment metadata must be an object: {segment_id}")
            segment_path = metadata.get("path")
            if not isinstance(segment_path, str):
                raise SidecarValidationError(f"Invalid alias segment path: {segment_id}")
            _safe_segment_path(segment_path)
            if not _is_sha256(metadata.get("sha256")):
                raise SidecarValidationError(f"Invalid alias segment hash: {segment_id}")
            alias_count = metadata.get("alias_count")
            if not isinstance(alias_count, int) or alias_count < 0 or alias_count > self.max_alias_count:
                raise SidecarValidationError(f"Invalid alias segment alias_count: {segment_id}")
            segment_alias_total += alias_count
        if segment_alias_total != index_data.get("alias_count"):
            raise SidecarValidationError("Alias index segment alias counts do not match alias_count")
        expected_hash = index_data.get("index_sha256")
        if not _is_sha256(expected_hash):
            raise SidecarValidationError("Alias index hash is missing or malformed")
        actual_hash = self.hash_service.sha256(
            self.json_codec.canonical_encode(_canonical_v3_index_payload(index_data)).encode("utf-8")
        )
        if actual_hash != expected_hash:
            raise SidecarValidationError("Alias index hash mismatch")

    def _validate_v2_index(self, index_data: dict[str, Any]) -> None:
        self._validate_common_index_fields(index_data)
        if not _is_sha256(index_data.get("manifest_sha256")):
            raise SidecarValidationError("Alias index manifest_sha256 must be a SHA-256 hex digest")
        ranges = index_data.get("ranges")
        chunks = index_data.get("chunks", {})
        if not isinstance(ranges, list):
            raise SidecarValidationError("Alias index ranges must be an array")
        if len(ranges) != index_data.get("chunk_count"):
            raise SidecarValidationError("Alias index ranges do not match chunk_count")
        seen_paths = set()
        previous_last = ""
        for item in ranges:
            if not isinstance(item, dict):
                raise SidecarValidationError("Alias index range must be an object")
            first_alias = item.get("first_alias")
            last_alias = item.get("last_alias")
            path = item.get("path")
            if not isinstance(first_alias, str) or not self.codec.is_structurally_valid(first_alias):
                raise SidecarValidationError(f"Malformed first_alias in index: {first_alias}")
            if not isinstance(last_alias, str) or not self.codec.is_structurally_valid(last_alias):
                raise SidecarValidationError(f"Malformed last_alias in index: {last_alias}")
            if not isinstance(path, str):
                raise SidecarValidationError(f"Alias index range path is invalid: {path}")
            safe_path = _safe_chunk_filename(path)
            if safe_path not in chunks:
                raise SidecarValidationError(f"Alias index range references missing chunk: {path}")
            if first_alias > last_alias:
                raise SidecarValidationError(f"Alias index range is inverted: {path}")
            if previous_last and first_alias <= previous_last:
                raise SidecarValidationError("Alias index ranges overlap or are unsorted")
            previous_last = last_alias
            seen_paths.add(safe_path)
        if seen_paths != set(chunks):
            raise SidecarValidationError("Alias index ranges do not cover all chunks")
        expected_hash = index_data.get("index_sha256")
        if not _is_sha256(expected_hash):
            raise SidecarValidationError("Alias index hash is missing or malformed")
        actual_hash = self.hash_service.sha256(
            self.json_codec.canonical_encode(_canonical_v2_index_payload(index_data)).encode("utf-8")
        )
        if actual_hash != expected_hash:
            raise SidecarValidationError("Alias index hash mismatch")

    def _resolve_with_index(
        self,
        requested: set[str],
        tknd_dir: str,
        index_path: str,
        start: float,
    ) -> AliasResolutionResult:
        index_text, index_bytes = self._read_limited_text(
            index_path,
            self.max_index_bytes,
            operation="lookup",
            reason="load_alias_index",
        )
        parse_start = time.perf_counter()
        index_data = self.json_codec.decode(index_text)
        self._validate_index(index_data)
        index_parse_ms = (time.perf_counter() - parse_start) * 1000.0
        if index_data.get("schema_version") == ALIAS_INDEX_SCHEMA_VERSION:
            return self._resolve_with_v3_index(requested, tknd_dir, index_data, index_text, index_bytes, index_parse_ms, start)
        return self._resolve_with_v2_index(requested, tknd_dir, index_data, index_text, index_bytes, index_parse_ms, start)

    def _locate_v3_aliases(
        self,
        requested: set[str],
        tknd_dir: str,
        index_data: dict[str, Any],
    ) -> tuple[dict[str, str], list[str], int, int, float]:
        segment_ids = sorted({self.codec.segment_id(alias) for alias in requested})
        segments = index_data["segments"]
        alias_map: dict[str, str] = {}
        loaded_segments: list[str] = []
        bytes_loaded = 0
        tokens_loaded = 0
        parse_ms = 0.0
        for segment_id in segment_ids:
            metadata = segments.get(segment_id)
            if metadata is None:
                continue
            segment_path = _safe_segment_path(metadata["path"])
            physical_path = self.file_repo.join(tknd_dir, *segment_path.split("/"))
            text, byte_count = self._read_limited_text(
                physical_path,
                self.max_segment_bytes,
                operation="lookup",
                reason="load_alias_segment",
            )
            expected_sha = metadata["sha256"]
            parse_start = time.perf_counter()
            segment = self.json_codec.decode(text)
            self._validate_segment(segment, segment_id, index_data)
            if segment.get("segment_sha256") != expected_sha:
                raise SidecarValidationError(f"Alias segment hash mismatch: {segment_id}")
            parse_ms += (time.perf_counter() - parse_start) * 1000.0
            if len(segment["aliases"]) != metadata["alias_count"]:
                raise SidecarValidationError(f"Alias segment alias_count mismatch: {segment_id}")
            loaded_segments.append(segment_path)
            bytes_loaded += byte_count
            tokens_loaded += self._count_tokens(text)
            for alias in requested:
                chunk = segment["aliases"].get(alias)
                if chunk is not None:
                    alias_map[alias] = _safe_chunk_filename(chunk)
        return alias_map, loaded_segments, bytes_loaded, tokens_loaded, parse_ms

    def _validate_segment(self, segment: dict[str, Any], segment_id: str, index_data: dict[str, Any]) -> None:
        if not isinstance(segment, dict):
            raise SidecarValidationError("Alias segment must be a JSON object")
        if segment.get("format") != ALIAS_SEGMENT_FORMAT:
            raise SidecarValidationError(f"Unsupported alias segment format: {segment.get('format')}")
        if segment.get("schema_version") != ALIAS_SEGMENT_SCHEMA_VERSION:
            raise SidecarValidationError(f"Unsupported alias segment schema: {segment.get('schema_version')}")
        if segment.get("corpus_id") != index_data["corpus_id"]:
            raise SidecarValidationError(f"Alias segment corpus_id mismatch: {segment_id}")
        if segment.get("segment_id") != segment_id:
            raise SidecarValidationError(f"Alias segment id mismatch: {segment_id}")
        if segment.get("alias_codec_version") != self.codec.version:
            raise SidecarValidationError("Alias segment codec version mismatch")
        aliases = segment.get("aliases")
        if not isinstance(aliases, dict):
            raise SidecarValidationError("Alias segment aliases must be an object")
        chunks = index_data["chunks"]
        for alias, chunk_name in aliases.items():
            if not self.codec.is_structurally_valid(alias):
                raise SidecarValidationError(f"Malformed alias in segment: {alias}")
            if self.codec.segment_id(alias) != segment_id:
                raise SidecarValidationError(f"Alias stored in wrong segment: {alias}")
            safe_chunk = _safe_chunk_filename(chunk_name)
            if safe_chunk not in chunks:
                raise SidecarValidationError(f"Alias segment references missing chunk: {safe_chunk}")
        expected_hash = segment.get("segment_sha256")
        if not _is_sha256(expected_hash):
            raise SidecarValidationError("Alias segment hash is missing or malformed")
        actual_hash = self.hash_service.sha256(
            self.json_codec.canonical_encode(_canonical_segment_payload(segment)).encode("utf-8")
        )
        if actual_hash != expected_hash:
            raise SidecarValidationError(f"Alias segment hash mismatch: {segment_id}")

    def _locate_v2_aliases(self, requested: set[str], index_data: dict[str, Any]) -> dict[str, str]:
        alias_map: dict[str, str] = {}
        for alias in sorted(requested):
            for item in index_data["ranges"]:
                if item["first_alias"] <= alias <= item["last_alias"]:
                    alias_map[alias] = _safe_chunk_filename(item["path"])
                    break
        return alias_map

    def _resolve_with_v3_index(
        self,
        requested: set[str],
        tknd_dir: str,
        index_data: dict[str, Any],
        index_text: str,
        index_bytes: int,
        index_parse_ms: float,
        start: float,
    ) -> AliasResolutionResult:
        alias_map, segments_loaded, segment_bytes, segment_tokens, segment_parse_ms = self._locate_v3_aliases(
            requested,
            tknd_dir,
            index_data,
        )
        return self._resolve_chunks(
            requested,
            alias_map,
            tknd_dir,
            index_data,
            index_text,
            index_bytes + segment_bytes,
            self._count_tokens(index_text) + segment_tokens,
            index_parse_ms,
            start,
            tuple(segments_loaded),
            segment_parse_ms,
            EXACT_MEMBERSHIP,
            missing_alias_is_error=True,
        )

    def _resolve_with_v2_index(
        self,
        requested: set[str],
        tknd_dir: str,
        index_data: dict[str, Any],
        index_text: str,
        index_bytes: int,
        index_parse_ms: float,
        start: float,
    ) -> AliasResolutionResult:
        return self._resolve_chunks(
            requested,
            self._locate_v2_aliases(requested, index_data),
            tknd_dir,
            index_data,
            index_text,
            index_bytes,
            self._count_tokens(index_text),
            index_parse_ms,
            start,
            (),
            0.0,
            APPROXIMATE_MEMBERSHIP,
            missing_alias_is_error=False,
        )

    def _resolve_chunks(
        self,
        requested: set[str],
        alias_map: dict[str, str],
        tknd_dir: str,
        index_data: dict[str, Any],
        index_text: str,
        bytes_loaded: int,
        tokens_loaded: int,
        index_parse_ms: float,
        start: float,
        segments_loaded: tuple[str, ...],
        segment_parse_ms: float,
        membership_mode: str,
        *,
        missing_alias_is_error: bool,
    ) -> AliasResolutionResult:
        del index_text
        chunk_hashes: dict[str, dict[str, Any]] = index_data.get("chunks", {})
        chunk_names = sorted(set(alias_map.values()))
        resolved: dict[str, str] = {}
        unresolved = {alias for alias in requested if alias not in alias_map}
        chunks_loaded: list[str] = []
        entries_loaded = 0
        sidecar_parse_ms = 0.0
        seen_alias_locations: dict[str, str] = {}
        for chunk_name in chunk_names:
            chunk_path = self.file_repo.join(tknd_dir, chunk_name)
            if not self.file_repo.exists(chunk_path):
                raise SidecarValidationError(f"Alias sidecar chunk missing: {chunk_name}")
            chunk_text, chunk_bytes = self._read_limited_text(
                chunk_path,
                self.max_sidecar_bytes,
                operation="lookup",
                reason="load_alias_sidecar",
            )
            expected_sha = chunk_hashes.get(chunk_name, {}).get("sha256")
            if expected_sha and self.hash_service.sha256(chunk_text.encode("utf-8")) != expected_sha:
                raise SidecarValidationError(f"Alias sidecar chunk hash mismatch: {chunk_name}")
            parse_start = time.perf_counter()
            sidecar_data = self.json_codec.decode(chunk_text)
            validate_sidecar_schema(sidecar_data)
            self._validate_sidecar_binding(sidecar_data, index_data, chunk_name)
            entries = sidecar_data["entries"]
            if len(entries) > self.max_entries_per_chunk:
                raise SidecarValidationError(f"Alias sidecar chunk has too many entries: {chunk_name}")
            if len(entries) != chunk_hashes[chunk_name]["entry_count"]:
                raise SidecarValidationError(f"Alias sidecar entry_count mismatch: {chunk_name}")
            entries_sha = self.hash_service.sha256(self.json_codec.canonical_encode(entries).encode("utf-8"))
            if sidecar_data.get("entries_sha256") != entries_sha:
                raise SidecarValidationError(f"Alias sidecar entries_sha256 mismatch: {chunk_name}")
            expected_entries_sha = chunk_hashes[chunk_name].get("entries_sha256")
            if expected_entries_sha and entries_sha != expected_entries_sha:
                raise SidecarValidationError(f"Alias index entries_sha256 mismatch: {chunk_name}")
            sidecar_parse_ms += (time.perf_counter() - parse_start) * 1000.0
            chunks_loaded.append(chunk_name)
            entries_loaded += len(entries)
            bytes_loaded += chunk_bytes
            tokens_loaded += self._count_tokens(chunk_text)
            for alias in entries:
                previous = seen_alias_locations.get(alias)
                if previous and previous != chunk_name:
                    raise SidecarValidationError(f"Duplicate alias across sidecar chunks: {alias}")
                seen_alias_locations[alias] = chunk_name
            for alias in sorted(requested):
                if alias_map.get(alias) != chunk_name:
                    continue
                if alias in entries:
                    if len(entries[alias]) > self.max_value_length:
                        raise SidecarValidationError(f"Alias value exceeds max_value_length: {alias}")
                    resolved[alias] = entries[alias]
                elif missing_alias_is_error:
                    raise SidecarValidationError(f"Alias index points to chunk without alias: {alias}")
                else:
                    unresolved.add(alias)
        unresolved -= set(resolved)
        total_resolved_bytes = sum(len(value.encode("utf-8")) for value in resolved.values())
        if total_resolved_bytes > self.max_total_resolved_bytes:
            raise SidecarValidationError("Resolved alias payload exceeds max_total_resolved_bytes")
        return AliasResolutionResult(
            resolved=resolved,
            unresolved=unresolved,
            chunks_loaded=tuple(chunks_loaded),
            entries_loaded=entries_loaded,
            bytes_loaded=bytes_loaded,
            tokens_loaded=tokens_loaded,
            index_parse_duration_ms=index_parse_ms,
            sidecar_parse_duration_ms=sidecar_parse_ms,
            alias_resolution_duration_ms=(time.perf_counter() - start) * 1000.0,
            segments_loaded=segments_loaded,
            segment_parse_duration_ms=segment_parse_ms,
            membership_mode=membership_mode,
        )

    def _validate_sidecar_binding(self, sidecar_data: dict[str, Any], index_data: dict[str, Any], chunk_name: str) -> None:
        if sidecar_data.get("dictionary_id") != index_data["dictionary_id"]:
            raise SidecarValidationError(f"Alias sidecar dictionary_id mismatch: {chunk_name}")
        expected_manifest = index_data.get("source_manifest_sha256") or index_data.get("manifest_sha256")
        if sidecar_data.get("manifest_sha256") != expected_manifest:
            raise SidecarValidationError(f"Alias sidecar manifest_sha256 mismatch: {chunk_name}")
        if sidecar_data.get("chunk_count") != index_data["chunk_count"]:
            raise SidecarValidationError(f"Alias sidecar chunk_count mismatch: {chunk_name}")

    def _resolve_legacy_single_sidecar(
        self,
        requested: set[str],
        tknd_dir: str,
        start: float,
    ) -> AliasResolutionResult:
        chunk_names = sorted(
            _safe_chunk_filename(name)
            for name in self.file_repo.list_dir(tknd_dir)
            if name.endswith(".cidatkn")
        )
        if not chunk_names:
            return AliasResolutionResult({}, set(requested), tuple(), 0, 0, 0, membership_mode=APPROXIMATE_MEMBERSHIP)
        if len(chunk_names) > 1:
            raise SidecarValidationError(f"Alias index '{ALIAS_INDEX_FILENAME}' is required for multi-chunk lookup")
        chunk_name = chunk_names[0]
        chunk_path = self.file_repo.join(tknd_dir, chunk_name)
        chunk_text, chunk_bytes = self._read_limited_text(chunk_path, self.max_sidecar_bytes)
        parse_start = time.perf_counter()
        sidecar_data = self.json_codec.decode(chunk_text)
        validate_sidecar_schema(sidecar_data)
        entries = sidecar_data["entries"]
        sidecar_parse_ms = (time.perf_counter() - parse_start) * 1000.0
        resolved = {alias: entries[alias] for alias in requested if alias in entries}
        unresolved = requested - set(resolved)
        return AliasResolutionResult(
            resolved=resolved,
            unresolved=unresolved,
            chunks_loaded=(chunk_name,),
            entries_loaded=len(entries),
            bytes_loaded=chunk_bytes,
            tokens_loaded=self._count_tokens(chunk_text),
            sidecar_parse_duration_ms=sidecar_parse_ms,
            alias_resolution_duration_ms=(time.perf_counter() - start) * 1000.0,
            membership_mode=APPROXIMATE_MEMBERSHIP,
        )
