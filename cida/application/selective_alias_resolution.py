import re
import time
from dataclasses import dataclass
from typing import Any

from cida.domain.errors import SidecarValidationError
from cida.domain.sidecar import validate_sidecar_schema


ALIAS_INDEX_FILENAME = "alias-index.json"
ALIAS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")


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


def _safe_chunk_filename(filename: str) -> str:
    if not isinstance(filename, str) or not filename.endswith(".cidatkn"):
        raise SidecarValidationError(f"Invalid sidecar chunk filename: {filename}")
    normalized = filename.replace("\\", "/")
    if "/" in normalized or normalized in (".cidatkn", ALIAS_INDEX_FILENAME):
        raise SidecarValidationError(f"Unsafe sidecar chunk filename: {filename}")
    return normalized


def _canonical_index_payload(index_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": index_data.get("schema_version"),
        "dictionary_id": index_data.get("dictionary_id"),
        "aliases": index_data.get("aliases"),
        "chunks": index_data.get("chunks", {}),
    }


def build_alias_index(
    alias_to_chunk: dict[str, str],
    dictionary_id: str,
    chunk_hashes: dict[str, str],
    hash_service: Any,
    json_codec: Any,
) -> dict[str, Any]:
    aliases: dict[str, str] = {}
    for alias, chunk_name in sorted(alias_to_chunk.items()):
        if not ALIAS_RE.fullmatch(alias):
            raise SidecarValidationError(f"Malformed alias rejected: {alias}")
        aliases[alias] = _safe_chunk_filename(chunk_name)

    chunks = {}
    for chunk_name, digest in sorted(chunk_hashes.items()):
        safe_name = _safe_chunk_filename(chunk_name)
        if not isinstance(digest, str) or len(digest) != 64:
            raise SidecarValidationError(f"Invalid sidecar chunk hash for {safe_name}")
        chunks[safe_name] = {"sha256": digest}

    index_data: dict[str, Any] = {
        "schema_version": 1,
        "dictionary_id": dictionary_id,
        "aliases": aliases,
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
    ):
        self.file_repo = file_repo
        self.json_codec = json_codec
        self.hash_service = hash_service
        self.token_counter = token_counter
        self.max_index_bytes = max_index_bytes
        self.max_sidecar_bytes = max_sidecar_bytes
        self.max_entries_per_chunk = max_entries_per_chunk

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
        if index_data.get("schema_version") != 1:
            raise SidecarValidationError(f"Unsupported alias index schema: {index_data.get('schema_version')}")
        if not isinstance(index_data.get("dictionary_id"), str) or not index_data["dictionary_id"]:
            raise SidecarValidationError("Alias index dictionary_id must be a non-empty string")
        aliases = index_data.get("aliases")
        if not isinstance(aliases, dict):
            raise SidecarValidationError("Alias index aliases must be an object")
        chunks = index_data.get("chunks", {})
        if not isinstance(chunks, dict):
            raise SidecarValidationError("Alias index chunks must be an object")
        for alias, chunk_name in aliases.items():
            if not ALIAS_RE.fullmatch(alias):
                raise SidecarValidationError(f"Malformed alias in index: {alias}")
            _safe_chunk_filename(chunk_name)
        for chunk_name, metadata in chunks.items():
            _safe_chunk_filename(chunk_name)
            if not isinstance(metadata, dict):
                raise SidecarValidationError(f"Alias index chunk metadata must be an object: {chunk_name}")
            digest = metadata.get("sha256")
            if not isinstance(digest, str) or len(digest) != 64:
                raise SidecarValidationError(f"Invalid alias index chunk hash: {chunk_name}")
        expected_hash = index_data.get("index_sha256")
        if not isinstance(expected_hash, str) or len(expected_hash) != 64:
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

        alias_map: dict[str, str] = index_data["aliases"]
        chunk_hashes: dict[str, dict[str, str]] = index_data.get("chunks", {})
        chunk_names = sorted({_safe_chunk_filename(alias_map[a]) for a in requested if a in alias_map})

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
            entries = sidecar_data["entries"]
            if len(entries) > self.max_entries_per_chunk:
                raise SidecarValidationError(f"Alias sidecar chunk has too many entries: {chunk_name}")
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
                    resolved[alias] = entries[alias]
                else:
                    unresolved.add(alias)

        unresolved -= set(resolved)
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
