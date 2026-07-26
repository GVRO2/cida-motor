import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from cida.application.content_search_index import (
    SEARCH_INDEX_FILENAME,
    SINGLE_SEGMENT_ID,
    normalize_terms,
    segment_id_for_term,
    validate_content_search_index,
    validate_content_search_segment,
)
from cida.application.selective_alias_resolution import (
    ALIAS_INDEX_FILENAME,
    AliasResolutionResult,
    AliasDetector,
    EXACT_MEMBERSHIP,
    SelectiveAliasResolver,
)
from cida.domain.alias_codec import DEFAULT_ALIAS_CODEC
from cida.domain.errors import SidecarValidationError
from cida.infrastructure.byte_bounded_cache import ByteBoundedLRUCache
from cida.infrastructure.bundle_runtime_verifier import BundleRuntimeVerifier, find_bundle_root
from cida.infrastructure.managed_memory import ManagedMemoryAccounting
from cida.infrastructure.filesystem import PhysicalFilesystem


CONTENT_SUFFIXES = {".py", ".go", ".java", ".md", ".txt", ".yaml", ".yml", ".js", ".ts", ".tknc"}
STOPWORDS = {
    "the",
    "and",
    "with",
    "using",
    "qual",
    "como",
    "onde",
    "para",
    "por",
    "que",
    "dos",
    "das",
    "main",
}


@dataclass(frozen=True)
class ContextReadEvent:
    path: str
    artifact_type: str
    operation: str
    bytes_requested: int
    bytes_read: int
    reason: str
    query_id: str
    cache_hit: bool
    relative_timestamp_ms: float


@dataclass(frozen=True)
class ContextSearchResult:
    files: tuple[str, ...]
    files_available: int
    files_scanned: int
    files_selected: int
    terms: tuple[str, ...]
    alias_candidates: tuple[str, ...]
    search_bytes_read: int
    search_tokens: int
    search_duration_ms: float
    search_mode: str = "FULL_SCAN_FALLBACK"
    search_index_segments_loaded: int = 0
    search_index_bytes_read: int = 0
    content_files_opened: int = 0
    candidate_files: tuple[str, ...] = ()


def _parts(path: Path) -> tuple[str, ...]:
    return tuple(part.lower() for part in path.parts)


def is_lookup_artifact(path: Path) -> bool:
    parts = _parts(path)
    return (
        "tknd" in parts
        or "search-index" in parts
        or path.name in {ALIAS_INDEX_FILENAME, SEARCH_INDEX_FILENAME}
        or path.suffix == ".cidatkn"
    )


def is_evidence_artifact(path: Path) -> bool:
    name = path.name.lower()
    return (
        name == "tknc-manifest.json"
        or name.startswith("report")
        or name in {"read-events.json", "harness-events.json"}
    )


def is_content_artifact(path: Path) -> bool:
    return path.suffix in CONTENT_SUFFIXES and not is_lookup_artifact(path) and not is_evidence_artifact(path)


def artifact_type(path: Path) -> str:
    if path.name == ALIAS_INDEX_FILENAME:
        return "alias_index"
    if "segments" in _parts(path) and path.suffix == ".json":
        return "alias_segment"
    if path.suffix == ".cidatkn":
        return "sidecar"
    if path.name == "tknc-manifest.json":
        return "manifest"
    if path.name == "bundle-manifest.json":
        return "bundle_manifest"
    if path.name == SEARCH_INDEX_FILENAME:
        return "search_index"
    if "search-index" in _parts(path):
        return "search_segment"
    if path.name.startswith("report"):
        return "report"
    if is_content_artifact(path):
        return "content"
    return "other"


class ContextFilesystem(PhysicalFilesystem):
    def __init__(self, max_cache_bytes: int = 8_000_000, max_cache_items: int = 512) -> None:
        super().__init__()
        self._started = time.perf_counter()
        self.reads: list[ContextReadEvent] = []
        self._cache = ByteBoundedLRUCache(max_bytes=max_cache_bytes, max_items=max_cache_items)
        self._bundle_verifiers: dict[Path, BundleRuntimeVerifier] = {}

    def read_bytes_limited(
        self,
        filepath: str,
        max_bytes: int,
        *,
        operation: str = "read",
        reason: str = "",
        query_id: str = "",
    ) -> bytes:
        path_key = str(Path(filepath).resolve())
        current_artifact_type = artifact_type(Path(filepath))
        cached = self._cache.get(path_key)
        if cached is not None:
            cache_hit = True
            data = cached
            if len(data) > max_bytes:
                raise SidecarValidationError(f"Context artifact exceeds requested size limit: {filepath}")
        else:
            cache_hit = False
            data = self._read_verified_or_physical(Path(filepath), current_artifact_type, max_bytes)
            self._cache.put(
                path_key,
                data,
                artifact_type=current_artifact_type,
                pinned=current_artifact_type in {"alias_index", "manifest", "bundle_manifest", "search_index"},
            )
        self.reads.append(
            ContextReadEvent(
                path=filepath,
                artifact_type=current_artifact_type,
                operation=operation,
                bytes_requested=max_bytes,
                bytes_read=len(data),
                reason=reason,
                query_id=query_id,
                cache_hit=cache_hit,
                relative_timestamp_ms=(time.perf_counter() - self._started) * 1000.0,
            )
        )
        return data

    def _read_verified_or_physical(self, path: Path, current_artifact_type: str, max_bytes: int) -> bytes:
        bundle_root = find_bundle_root(path)
        if bundle_root is None:
            return super().read_bytes_limited(str(path), max_bytes)
        verifier = self._bundle_verifiers.get(bundle_root)
        if verifier is None:
            verifier = BundleRuntimeVerifier(bundle_root)
            self._bundle_verifiers[bundle_root] = verifier
        expected_type = _bundle_artifact_type(current_artifact_type)
        try:
            data = verifier.read_verified_bytes(path, expected_type=expected_type)
        except ValueError as exc:
            raise SidecarValidationError(str(exc)) from exc
        if len(data) > max_bytes:
            raise SidecarValidationError(f"Context artifact exceeds requested size limit: {path}")
        return data

    def read_text_limited(
        self,
        filepath: str,
        max_bytes: int,
        *,
        operation: str = "read",
        reason: str = "",
        query_id: str = "",
    ) -> str:
        raw = self.read_bytes_limited(
            filepath,
            max_bytes,
            operation=operation,
            reason=reason,
            query_id=query_id,
        )
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SidecarValidationError(f"Invalid UTF-8 context artifact: {filepath}") from exc

    def list_context_files(self, root: Path) -> list[Path]:
        return [path for path in sorted(root.rglob("*")) if is_content_artifact(path)]

    def physical_read_count(self, artifact: str | None = None) -> int:
        return sum(1 for event in self.reads if not event.cache_hit and (artifact is None or event.artifact_type == artifact))

    def cached_texts(self, artifact_types: set[str]) -> list[str]:
        values = []
        for key, entry in self._cache._items.items():
            if artifact_type(Path(key)) in artifact_types:
                values.append(entry.data.decode("utf-8"))
        return values

    def clear_cache(self, *, reset_stats: bool = False) -> None:
        self._cache.clear(reset_stats=reset_stats)

    def cache_metrics(self) -> dict[str, object]:
        return self._cache.metrics()


def question_terms(question: str) -> tuple[str, ...]:
    terms: list[str] = []
    for term in normalize_terms(question):
        if term not in STOPWORDS:
            terms.append(term)
    return tuple(dict.fromkeys(terms))


def search_context(
    root: Path,
    question: str,
    fs: ContextFilesystem,
    token_counter: Any,
    *,
    query_id: str,
    limit: int = 4,
    max_file_bytes: int = 2_000_000,
) -> ContextSearchResult:
    started = time.perf_counter()
    terms = question_terms(question)
    index_path = root / "tknd" / SEARCH_INDEX_FILENAME
    if index_path.exists():
        return _search_context_indexed(
            root,
            index_path,
            terms,
            fs,
            token_counter,
            query_id=query_id,
            limit=limit,
            max_file_bytes=max_file_bytes,
            started=started,
        )
    return _search_context_full_scan(
        root,
        terms,
        fs,
        token_counter,
        query_id=query_id,
        limit=limit,
        max_file_bytes=max_file_bytes,
        started=started,
    )


def _score_text(rel: str, text: str, terms: tuple[str, ...]) -> int:
    rel_lower = rel.lower()
    text_lower = text.lower()
    score = 0
    for term in terms:
        term_lower = term.lower()
        if term_lower in rel_lower:
            score += 12
        if re.search(rf"\b{re.escape(term_lower)}\b", text_lower):
            score += 8
        score += min(text_lower.count(term_lower), 3)
    return score


def _search_context_indexed(
    root: Path,
    index_path: Path,
    terms: tuple[str, ...],
    fs: ContextFilesystem,
    token_counter: Any,
    *,
    query_id: str,
    limit: int,
    max_file_bytes: int,
    started: float,
) -> ContextSearchResult:
    root_text = fs.read_text_limited(
        str(index_path),
        1_000_000,
        operation="search_index",
        reason="load_content_search_index",
        query_id=query_id,
    )
    index_bytes = len(root_text.encode("utf-8"))
    from cida.infrastructure.hashing import HashService
    from cida.infrastructure.json_codec import JsonCodec

    json_codec = JsonCodec()
    hash_service = HashService()
    index_data = json_codec.decode(root_text)
    validate_content_search_index(index_data, hash_service=hash_service, json_codec=json_codec)
    query_terms = tuple(dict.fromkeys(term for item in terms for term in normalize_terms(item)))
    if index_data.get("segmentation") == "single":
        segment_ids = [SINGLE_SEGMENT_ID]
    else:
        segment_ids = sorted({segment_id_for_term(term) for term in query_terms})
    candidates: set[str] = set()
    candidate_term_hits: dict[str, int] = {}
    segments_loaded = 0
    for segment_id in segment_ids:
        metadata = index_data["segments"].get(segment_id)
        if metadata is None:
            continue
        segment_rel = metadata["path"].replace("\\", "/")
        segment_path = root / "tknd" / Path(*segment_rel.split("/"))
        segment_text = fs.read_text_limited(
            str(segment_path),
            1_000_000,
            operation="search_index",
            reason="load_content_search_segment",
            query_id=query_id,
        )
        index_bytes += len(segment_text.encode("utf-8"))
        segment = json_codec.decode(segment_text)
        validate_content_search_segment(
            segment,
            segment_id=segment_id,
            expected_sha256=metadata["sha256"],
            corpus_id=index_data["corpus_id"],
            hash_service=hash_service,
            json_codec=json_codec,
        )
        segments_loaded += 1
        for term in query_terms:
            for rel in segment["terms"].get(term, []):
                candidates.add(rel)
                candidate_term_hits[rel] = candidate_term_hits.get(rel, 0) + 1
    scored: list[tuple[int, str, str]] = []
    content_bytes = 0
    opened = 0
    for rel in sorted(candidates):
        path = root / Path(*rel.split("/"))
        if not path.exists() or not is_content_artifact(path):
            raise SidecarValidationError(f"Content search index points to invalid file: {rel}")
        text = fs.read_text_limited(
            str(path),
            max_file_bytes,
            operation="search",
            reason="indexed_content_candidate",
            query_id=query_id,
        )
        opened += 1
        content_bytes += len(text.encode("utf-8"))
        score = _score_text(rel, text, terms) + candidate_term_hits.get(rel, 0) * 10
        if score:
            scored.append((score, rel, text))
    scored.sort(key=lambda item: (-item[0], item[1]))
    selected_items = scored[:limit]
    alias_detector = AliasDetector()
    alias_candidates: set[str] = set()
    for _, _, text in selected_items:
        alias_candidates.update(alias_detector.candidates(text))
    selected = tuple(rel for _, rel, _ in selected_items)
    return ContextSearchResult(
        files=selected,
        files_available=index_data.get("file_count", 0),
        files_scanned=len(candidates),
        files_selected=len(selected),
        terms=terms,
        alias_candidates=tuple(sorted(alias_candidates)),
        search_bytes_read=index_bytes + content_bytes,
        search_tokens=token_counter.count(" ".join(terms)) if token_counter is not None else 0,
        search_duration_ms=(time.perf_counter() - started) * 1000.0,
        search_mode="INDEXED",
        search_index_segments_loaded=segments_loaded,
        search_index_bytes_read=index_bytes,
        content_files_opened=opened,
        candidate_files=tuple(sorted(candidates)),
    )


def _search_context_full_scan(
    root: Path,
    terms: tuple[str, ...],
    fs: ContextFilesystem,
    token_counter: Any,
    *,
    query_id: str,
    limit: int,
    max_file_bytes: int,
    started: float,
) -> ContextSearchResult:
    scored: list[tuple[int, str, str]] = []
    files = fs.list_context_files(root)
    bytes_read = 0
    alias_detector = AliasDetector()
    for path in files:
        rel = path.relative_to(root).as_posix()
        text = fs.read_text_limited(
            str(path),
            max_file_bytes,
            operation="search",
            reason="content_candidate_scan",
            query_id=query_id,
        )
        bytes_read += len(text.encode("utf-8"))
        score = _score_text(rel, text, terms)
        if score:
            scored.append((score, rel, text))
    scored.sort(key=lambda item: (-item[0], item[1]))
    selected_items = scored[:limit]
    alias_candidates: set[str] = set()
    for _, _, text in selected_items:
        alias_candidates.update(alias_detector.candidates(text))
    selected = tuple(rel for _, rel, _ in selected_items)
    return ContextSearchResult(
        files=selected,
        files_available=len(files),
        files_scanned=len(files),
        files_selected=len(selected),
        terms=terms,
        alias_candidates=tuple(sorted(alias_candidates)),
        search_bytes_read=bytes_read,
        search_tokens=token_counter.count(" ".join(terms)) if token_counter is not None else 0,
        search_duration_ms=(time.perf_counter() - started) * 1000.0,
        search_mode="FULL_SCAN_FALLBACK",
        content_files_opened=len(files),
        candidate_files=tuple(path.relative_to(root).as_posix() for path in files),
    )


@dataclass
class TkncContextSession:
    root: Path
    fs: ContextFilesystem
    json_codec: Any
    hash_service: Any
    token_counter: Any
    max_memory_bytes: int = 8_000_000
    max_resolved_aliases: int = 2_000
    max_resolved_alias_bytes: int = 2_000_000
    index_data: dict[str, Any] | None = None
    index_text: str = ""
    manifest_data: dict[str, Any] | None = None
    manifest_text: str = ""
    bundle_manifest_data: dict[str, Any] | None = None
    resolved_aliases: OrderedDict[str, str] = field(default_factory=OrderedDict)
    resolved_alias_bytes: int = 0
    resolved_alias_evictions: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    closed: bool = False
    managed_total_peak_bytes: int = 0

    def close(self) -> None:
        self.index_data = None
        self.index_text = ""
        self.manifest_data = None
        self.manifest_text = ""
        self.bundle_manifest_data = None
        self.resolved_aliases.clear()
        self.resolved_alias_bytes = 0
        self.fs.clear_cache(reset_stats=True)
        self.cache_hits = 0
        self.cache_misses = 0
        self.managed_total_peak_bytes = 0
        self.closed = True

    @property
    def tknd_dir(self) -> Path:
        return self.root / "tknd"

    def search(self, question: str, *, query_id: str, limit: int = 4) -> ContextSearchResult:
        self._ensure_open()
        return search_context(self.root, question, self.fs, self.token_counter, query_id=query_id, limit=limit)

    def _load_index(self, *, query_id: str) -> dict[str, Any]:
        self._ensure_open()
        if self.index_data is not None:
            self.cache_hits += 1
            return self.index_data
        self.cache_misses += 1
        index_path = self.tknd_dir / ALIAS_INDEX_FILENAME
        self.index_text = self.fs.read_text_limited(
            str(index_path),
            2_000_000,
            operation="lookup",
            reason="load_alias_index",
            query_id=query_id,
        )
        index = self.json_codec.decode(self.index_text)
        SelectiveAliasResolver(self.fs, self.json_codec, self.hash_service, self.token_counter)._validate_index(index)
        self.index_data = index
        return index

    def _validate_manifest(self, index_data: dict[str, Any], *, query_id: str) -> dict[str, Any]:
        self._ensure_open()
        if self.manifest_data is not None:
            self.cache_hits += 1
            return self.manifest_data
        self.cache_misses += 1
        manifest_path = self.root / "tknc-manifest.json"
        if not manifest_path.exists():
            raise SidecarValidationError("Corpus manifest is missing")
        self.manifest_text = self.fs.read_text_limited(
            str(manifest_path),
            1_000_000,
            operation="lookup",
            reason="validate_manifest_binding",
            query_id=query_id,
        )
        manifest = self.json_codec.decode(self.manifest_text)
        if not isinstance(manifest, dict):
            raise SidecarValidationError("Corpus manifest must be a JSON object")
        manifest_hash = manifest.get("manifest_sha256")
        if not isinstance(manifest_hash, str):
            raise SidecarValidationError("Corpus manifest hash is missing")
        canonical_payload = dict(manifest)
        canonical_payload.pop("manifest_sha256", None)
        actual = self.hash_service.sha256(self.json_codec.canonical_encode(canonical_payload).encode("utf-8"))
        if actual != manifest_hash:
            raise SidecarValidationError("Corpus manifest hash mismatch")
        expected_manifest_hash = index_data.get("source_manifest_sha256") or index_data.get("manifest_sha256")
        if manifest_hash != expected_manifest_hash:
            raise SidecarValidationError("Corpus manifest is not bound to alias index")
        self.manifest_data = manifest
        return manifest

    def aliases_in_index(self, aliases: set[str], *, query_id: str) -> set[str]:
        self._ensure_open()
        structural = {alias for alias in aliases if DEFAULT_ALIAS_CODEC.is_structurally_valid(alias)}
        resolver = SelectiveAliasResolver(self.fs, self.json_codec, self.hash_service, self.token_counter)
        return set(resolver.locate_aliases(structural, str(self.tknd_dir)))

    def required_chunks(self, aliases: set[str], *, query_id: str) -> tuple[str, ...]:
        self._ensure_open()
        structural = {alias for alias in aliases if DEFAULT_ALIAS_CODEC.is_structurally_valid(alias)}
        resolver = SelectiveAliasResolver(self.fs, self.json_codec, self.hash_service, self.token_counter)
        return tuple(sorted(set(resolver.locate_aliases(structural, str(self.tknd_dir)).values())))

    def resolve(self, aliases: set[str], *, query_id: str) -> AliasResolutionResult:
        self._ensure_open()
        index = self._load_index(query_id=query_id)
        self._validate_manifest(index, query_id=query_id)
        cached = {}
        for alias in aliases:
            if alias in self.resolved_aliases:
                cached[alias] = self.resolved_aliases[alias]
                self.resolved_aliases.move_to_end(alias)
        missing = set(aliases) - set(cached)
        if not missing:
            self.cache_hits += len(cached)
            return AliasResolutionResult(cached, set(), tuple(), 0, 0, self.token_counter.count(str(cached)))

        self.cache_misses += len(missing)
        resolver = SelectiveAliasResolver(self.fs, self.json_codec, self.hash_service, self.token_counter)
        result = resolver.resolve(missing, str(self.tknd_dir))
        self._store_resolved_aliases(result.resolved)
        combined = dict(cached)
        combined.update(result.resolved)
        return AliasResolutionResult(
            resolved=combined,
            unresolved=set(aliases) - set(combined),
            chunks_loaded=result.chunks_loaded,
            entries_loaded=result.entries_loaded,
            bytes_loaded=result.bytes_loaded,
            tokens_loaded=result.tokens_loaded,
            index_parse_duration_ms=result.index_parse_duration_ms,
            sidecar_parse_duration_ms=result.sidecar_parse_duration_ms,
            alias_resolution_duration_ms=result.alias_resolution_duration_ms,
            segments_loaded=result.segments_loaded,
            segment_parse_duration_ms=result.segment_parse_duration_ms,
            membership_mode=result.membership_mode,
        )

    def cache_metrics(self) -> dict[str, object]:
        metrics = self.fs.cache_metrics()
        accounting = ManagedMemoryAccounting.from_session(
            raw_cache_bytes=cast(int, metrics["cache_current_bytes"]),
            decoded_index_objects=[self.index_data, self.manifest_data, self.bundle_manifest_data],
            decoded_segment_objects=[],
            resolved_aliases=dict(self.resolved_aliases),
            event_buffer=list(self.fs.reads),
            other_objects=[],
            previous_peak=self.managed_total_peak_bytes,
            max_bytes=self.max_memory_bytes,
        )
        self.managed_total_peak_bytes = accounting.managed_total_peak_bytes
        metrics.update(
            {
                "resolved_alias_current_bytes": self.resolved_alias_bytes,
                "resolved_alias_max_bytes": self.max_resolved_alias_bytes,
                "resolved_alias_count": len(self.resolved_aliases),
                "resolved_alias_max_count": self.max_resolved_aliases,
                "resolved_alias_evictions": self.resolved_alias_evictions,
                "managed_cache_peak_bytes": metrics["cache_peak_bytes"],
                "membership_mode": self.index_data.get("membership", EXACT_MEMBERSHIP) if self.index_data else None,
                **accounting.as_dict(),
            }
        )
        return metrics

    def _store_resolved_aliases(self, values: dict[str, str]) -> None:
        for alias, value in sorted(values.items()):
            if alias in self.resolved_aliases:
                self.resolved_alias_bytes -= self._resolved_entry_bytes(alias, self.resolved_aliases[alias])
                self.resolved_aliases.pop(alias)
            entry_bytes = self._resolved_entry_bytes(alias, value)
            if entry_bytes > self.max_resolved_alias_bytes:
                continue
            self.resolved_aliases[alias] = value
            self.resolved_alias_bytes += entry_bytes
            self._evict_resolved_aliases()

    def _evict_resolved_aliases(self) -> None:
        while (
            len(self.resolved_aliases) > self.max_resolved_aliases
            or self.resolved_alias_bytes > self.max_resolved_alias_bytes
            or self.resolved_alias_bytes > self.max_memory_bytes
        ):
            alias, value = self.resolved_aliases.popitem(last=False)
            self.resolved_alias_bytes -= self._resolved_entry_bytes(alias, value)
            self.resolved_alias_evictions += 1

    @staticmethod
    def _resolved_entry_bytes(alias: str, value: str) -> int:
        return len(alias.encode("utf-8")) + len(value.encode("utf-8"))

    def _ensure_open(self) -> None:
        if self.closed:
            raise SidecarValidationError("TkncContextSession is closed")


def _bundle_artifact_type(context_artifact_type: str) -> str | None:
    return {
        "alias_index": "alias_index",
        "alias_segment": "alias_segment",
        "sidecar": "alias_chunk",
        "manifest": "source_manifest",
        "bundle_manifest": None,
        "search_index": "content_search_index",
        "search_segment": "content_search_segment",
        "content": "content_output",
    }.get(context_artifact_type)
