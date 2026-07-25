import re
import time
from dataclasses import dataclass
from typing import Any

from cida.domain.errors import SidecarValidationError
from cida.domain.sidecar import validate_sidecar_schema


ALIAS_INDEX_FILENAME = "alias-index.json"
ALIAS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CHUNK_FILENAME_RE = re.compile(r"^chunk-[0-9]{6}\.cidatkn$")
ALIAS_INDEX_FORMAT = "cida-alias-index"
ALIAS_INDEX_SCHEMA_VERSION = 2
MAX_ALIAS_COUNT = 100_000
MAX_CHUNKS = 500
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


class AliasDetector:
    """Detect aliases from selected .tknc text without receiving expected aliases."""

    _token_re = re.compile(r"\b[A-Za-z][A-Za-z0-9_]{1,31}\b")
    _string_re = re.compile(r"(['\"])(?:\\.|(?!\1).)*\1")

    def detect(self, text: str, index: dict[str, Any]) -> set[str]:
        if not isinstance(text, str) or not text:
            return set()
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
        scrubbed = self._string_re.sub(" ", text)
        detected = set()
        for match in self._token_re.finditer(scrubbed):
            alias = match.group(0)
            if any(first_alias <= alias <= last_alias for first_alias, last_alias in range_pairs):
                detected.add(alias)
        return detected


def _safe_chunk_filename(filename: str) -> str:
    if not isinstance(filename, str) or not filename.endswith(".cidatkn"):
        raise SidecarValidationError(f"Invalid sidecar chunk filename: {filename}")
    normalized = filename.replace("\\", "/")
    if "/" in normalized or normalized in (".cidatkn", ALIAS_INDEX_FILENAME):
        raise SidecarValidationError(f"Unsafe sidecar chunk filename: {filename}")
    return normalized


def _canonical_index_payload(index_data: dict[str, Any]) -> dict[str, Any]:
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


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def build_alias_index(
    alias_to_chunk: dict[str, str],
    dictionary_id: str,
    chunk_hashes: dict[str, str],
    hash_service: Any,
    json_codec: Any,
    manifest_sha256: str | None = None,
    chunk_entry_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
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
    for alias, chunk_name in sorted(alias_to_chunk.items()):
        if not ALIAS_RE.fullmatch(alias):
            raise SidecarValidationError(f"Malformed alias rejected: {alias}")
        safe_name = _safe_chunk_filename(chunk_name)
        if not CHUNK_FILENAME_RE.fullmatch(safe_name):
            raise SidecarValidationError(f"Invalid corpus chunk filename: {safe_name}")
        aliases[alias] = safe_name

    chunks = {}
    for chunk_name, digest in sorted(chunk_hashes.items()):
        safe_name = _safe_chunk_filename(chunk_name)
        if not CHUNK_FILENAME_RE.fullmatch(safe_name):
            raise SidecarValidationError(f"Invalid corpus chunk filename: {safe_name}")
        if not _is_sha256(digest):
            raise SidecarValidationError(f"Invalid sidecar chunk hash for {safe_name}")
        entry_count = (chunk_entry_counts or {}).get(safe_name)
        if not isinstance(entry_count, int) or entry_count < 0:
            raise SidecarValidationError(f"Missing entry_count for sidecar chunk: {safe_name}")
        chunks[safe_name] = {"sha256": digest, "entry_count": entry_count}

    referenced_chunks = set(aliases.values())
    if referenced_chunks != set(chunks):
        missing = sorted(referenced_chunks - set(chunks))
        extra = sorted(set(chunks) - referenced_chunks)
        raise SidecarValidationError(f"Alias index chunk metadata mismatch: missing={missing}, extra={extra}")

    ranges = []
    for chunk_name in sorted(chunks):
        chunk_aliases = sorted(alias for alias, mapped in aliases.items() if mapped == chunk_name)
        if not chunk_aliases:
            raise SidecarValidationError(f"Alias index chunk has no aliases: {chunk_name}")
        ranges.append({
            "first_alias": chunk_aliases[0],
            "last_alias": chunk_aliases[-1],
            "path": chunk_name,
        })

    index_data: dict[str, Any] = {
        "format": ALIAS_INDEX_FORMAT,
        "schema_version": ALIAS_INDEX_SCHEMA_VERSION,
        "dictionary_id": dictionary_id,
        "manifest_sha256": manifest_sha256,
        "alias_count": len(aliases),
        "chunk_count": len(chunks),
        "ranges": ranges,
        "chunks": chunks,
    }
    payload_bytes = json_codec.canonical_encode(_canonical_index_payload(index_data)).encode("utf-8")
    index_data["index_sha256"] = hash_service.sha256(payload_bytes)
    return index_data


class SelectiveAliasResolver:
    def __init__(
        self,
        file_repo: Any,
        json_codec: Any,
        hash_service: Any,
        token_counter: Any | None = None,
        max_index_bytes: int = 2_000_000,
        max_sidecar_bytes: int = 2_000_000,
        max_entries_per_chunk: int = 1_000,
        max_alias_count: int = MAX_ALIAS_COUNT,
        max_chunks: int = MAX_CHUNKS,
        max_value_length: int = MAX_VALUE_LENGTH,
        max_total_resolved_bytes: int = MAX_TOTAL_RESOLVED_BYTES,
    ):
        self.file_repo = file_repo
        self.json_codec = json_codec
        self.hash_service = hash_service
        self.token_counter = token_counter
        self.max_index_bytes = max_index_bytes
        self.max_sidecar_bytes = max_sidecar_bytes
        self.max_entries_per_chunk = max_entries_per_chunk
        self.max_alias_count = max_alias_count
        self.max_chunks = max_chunks
        self.max_value_length = max_value_length
        self.max_total_resolved_bytes = max_total_resolved_bytes

    def resolve(self, aliases: set[str], tknd_dir: str) -> AliasResolutionResult:
        start = time.perf_counter()
        requested = set(aliases)
        for alias in requested:
            if not ALIAS_RE.fullmatch(alias):
                raise SidecarValidationError(f"Malformed alias rejected: {alias}")

        if not requested:
            return AliasResolutionResult({}, set(), tuple(), 0, 0, 0)

        index_path = self.file_repo.join(tknd_dir, ALIAS_INDEX_FILENAME)
        if self.file_repo.exists(index_path):
            return self._resolve_with_index(requested, tknd_dir, index_path, start)

        return self._resolve_legacy_single_sidecar(requested, tknd_dir, start)

    def _read_limited_text(self, path: str, max_bytes: int) -> tuple[str, int]:
        if hasattr(self.file_repo, "read_bytes_limited"):
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
        if index_data.get("schema_version") != ALIAS_INDEX_SCHEMA_VERSION:
            raise SidecarValidationError(f"Unsupported alias index schema: {index_data.get('schema_version')}")
        if not _is_sha256(index_data.get("dictionary_id")):
            raise SidecarValidationError("Alias index dictionary_id must be a SHA-256 hex digest")
        if not _is_sha256(index_data.get("manifest_sha256")):
            raise SidecarValidationError("Alias index manifest_sha256 must be a SHA-256 hex digest")
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
        for chunk_name, metadata in chunks.items():
            safe_name = _safe_chunk_filename(chunk_name)
            if not CHUNK_FILENAME_RE.fullmatch(safe_name):
                raise SidecarValidationError(f"Invalid corpus chunk filename: {safe_name}")
            if not isinstance(metadata, dict):
                raise SidecarValidationError(f"Alias index chunk metadata must be an object: {chunk_name}")
            digest = metadata.get("sha256")
            if not _is_sha256(digest):
                raise SidecarValidationError(f"Invalid alias index chunk hash: {chunk_name}")
            entry_count = metadata.get("entry_count")
            if not isinstance(entry_count, int) or entry_count < 0 or entry_count > self.max_entries_per_chunk:
                raise SidecarValidationError(f"Invalid alias index chunk entry_count: {chunk_name}")
        ranges = index_data.get("ranges")
        if not isinstance(ranges, list):
            raise SidecarValidationError("Alias index ranges must be an array")
        if len(ranges) != chunk_count:
            raise SidecarValidationError("Alias index ranges do not match chunk_count")
        total_entries = 0
        seen_paths = set()
        previous_last = ""
        for item in ranges:
            if not isinstance(item, dict):
                raise SidecarValidationError("Alias index range must be an object")
            first_alias = item.get("first_alias")
            last_alias = item.get("last_alias")
            path = item.get("path")
            if not isinstance(first_alias, str) or not ALIAS_RE.fullmatch(first_alias):
                raise SidecarValidationError(f"Malformed first_alias in index: {first_alias}")
            if not isinstance(last_alias, str) or not ALIAS_RE.fullmatch(last_alias):
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
            total_entries += chunks[safe_path]["entry_count"]
        if seen_paths != set(chunks):
            raise SidecarValidationError("Alias index ranges do not cover all chunks")
        if total_entries != alias_count:
            raise SidecarValidationError("Alias index alias_count does not match chunk entry counts")
        expected_hash = index_data.get("index_sha256")
        if not _is_sha256(expected_hash):
            raise SidecarValidationError("Alias index hash is missing or malformed")
        actual_hash = self.hash_service.sha256(
            self.json_codec.canonical_encode(_canonical_index_payload(index_data)).encode("utf-8")
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
        index_text, index_bytes = self._read_limited_text(index_path, self.max_index_bytes)
        parse_start = time.perf_counter()
        index_data = self.json_codec.decode(index_text)
        self._validate_index(index_data)
        index_parse_ms = (time.perf_counter() - parse_start) * 1000.0

        ranges: list[dict[str, str]] = index_data["ranges"]
        chunk_hashes: dict[str, dict[str, str]] = index_data.get("chunks", {})
        alias_map: dict[str, str] = {}
        for alias in sorted(requested):
            for item in ranges:
                if item["first_alias"] <= alias <= item["last_alias"]:
                    alias_map[alias] = _safe_chunk_filename(item["path"])
                    break
        chunk_names = sorted(set(alias_map.values()))

        resolved: dict[str, str] = {}
        unresolved = {a for a in requested if a not in alias_map}
        chunks_loaded: list[str] = []
        entries_loaded = 0
        bytes_loaded = index_bytes
        tokens_loaded = self._count_tokens(index_text)
        sidecar_parse_ms = 0.0
        seen_alias_locations: dict[str, str] = {}

        for chunk_name in chunk_names:
            chunk_path = self.file_repo.join(tknd_dir, chunk_name)
            if not self.file_repo.exists(chunk_path):
                raise SidecarValidationError(f"Alias sidecar chunk missing: {chunk_name}")
            chunk_text, chunk_bytes = self._read_limited_text(chunk_path, self.max_sidecar_bytes)
            expected_sha = chunk_hashes.get(chunk_name, {}).get("sha256")
            if expected_sha and self.hash_service.sha256(chunk_text.encode("utf-8")) != expected_sha:
                raise SidecarValidationError(f"Alias sidecar chunk hash mismatch: {chunk_name}")

            parse_start = time.perf_counter()
            sidecar_data = self.json_codec.decode(chunk_text)
            validate_sidecar_schema(sidecar_data)
            if sidecar_data.get("dictionary_id") != index_data["dictionary_id"]:
                raise SidecarValidationError(f"Alias sidecar dictionary_id mismatch: {chunk_name}")
            if sidecar_data.get("manifest_sha256") != index_data["manifest_sha256"]:
                raise SidecarValidationError(f"Alias sidecar manifest_sha256 mismatch: {chunk_name}")
            if sidecar_data.get("chunk_count") != index_data["chunk_count"]:
                raise SidecarValidationError(f"Alias sidecar chunk_count mismatch: {chunk_name}")
            entries = sidecar_data["entries"]
            if len(entries) > self.max_entries_per_chunk:
                raise SidecarValidationError(f"Alias sidecar chunk has too many entries: {chunk_name}")
            if len(entries) != chunk_hashes[chunk_name]["entry_count"]:
                raise SidecarValidationError(f"Alias sidecar entry_count mismatch: {chunk_name}")
            entries_sha = self.hash_service.sha256(self.json_codec.canonical_encode(entries).encode("utf-8"))
            if sidecar_data.get("entries_sha256") != entries_sha:
                raise SidecarValidationError(f"Alias sidecar entries_sha256 mismatch: {chunk_name}")
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
        )

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
            return AliasResolutionResult({}, set(requested), tuple(), 0, 0, 0)
        if len(chunk_names) > 1:
            raise SidecarValidationError(
                f"Alias index '{ALIAS_INDEX_FILENAME}' is required for multi-chunk lookup"
            )

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
        )
