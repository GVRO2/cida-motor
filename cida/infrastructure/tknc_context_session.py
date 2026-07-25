import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cida.application.selective_alias_resolution import (
    ALIAS_INDEX_FILENAME,
    AliasResolutionResult,
    SelectiveAliasResolver,
    _safe_chunk_filename,
)
from cida.domain.errors import SidecarValidationError
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
TERM_EXPANSIONS = {
    "componente": ("main", "processarEComparar"),
    "inicia": ("main", "processarEComparar"),
    "principal": ("main", "motor_v3", "cli"),
    "processamento": ("workflow",),
    "auxiliares": ("write_corpus_sidecars", "tknd", "alias-index", "cidatkn"),
    "aliases": ("alias", "entries", "reconstruct_content"),
    "original": ("reconstruct_content", "entries", "replace"),
    "workers": ("ResourceProfiles", "resolveEffectiveWorkers"),
    "efetiva": ("resolveEffectiveWorkers", "worker"),
    "tokens": ("tokenizer", "count_tokens"),
    "contar": ("count_tokens", "tokenizer"),
    "vocabulario": ("lexicon",),
    "comprimido": ("lexicon",),
    "referencia": ("lexicon",),
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


def _parts(path: Path) -> tuple[str, ...]:
    return tuple(part.lower() for part in path.parts)


def is_lookup_artifact(path: Path) -> bool:
    parts = _parts(path)
    return "tknd" in parts or path.name == ALIAS_INDEX_FILENAME or path.suffix == ".cidatkn"


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
    if path.suffix == ".cidatkn":
        return "sidecar"
    if path.name == "tknc-manifest.json":
        return "manifest"
    if path.name.startswith("report"):
        return "report"
    if is_content_artifact(path):
        return "content"
    return "other"


def _looks_like_generated_alias(alias: str) -> bool:
    return (
        (len(alias) == 2 and alias.isalpha() and (alias.isupper() or alias.islower()))
        or (len(alias) == 3 and alias.isalpha() and alias.isupper())
    )


class ContextFilesystem(PhysicalFilesystem):
    def __init__(self) -> None:
        super().__init__()
        self._started = time.perf_counter()
        self.reads: list[ContextReadEvent] = []
        self._cache: dict[str, bytes] = {}

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
        cache_hit = path_key in self._cache
        if cache_hit:
            data = self._cache[path_key]
            if len(data) > max_bytes:
                raise SidecarValidationError(f"Context artifact exceeds requested size limit: {filepath}")
        else:
            data = super().read_bytes_limited(filepath, max_bytes)
            self._cache[path_key] = data
        self.reads.append(
            ContextReadEvent(
                path=filepath,
                artifact_type=artifact_type(Path(filepath)),
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


def question_terms(question: str) -> tuple[str, ...]:
    raw = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", question)
    terms: list[str] = []
    for term in raw:
        if term.lower() not in STOPWORDS:
            terms.append(term)
        terms.extend(TERM_EXPANSIONS.get(term.lower(), ()))
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
    scored: list[tuple[int, str, str]] = []
    files = fs.list_context_files(root)
    bytes_read = 0
    alias_re = re.compile(r"\b[A-Za-z]{2,3}\b")
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
        if score:
            scored.append((score, rel, text))
    scored.sort(key=lambda item: (-item[0], item[1]))
    selected_items = scored[:limit]
    alias_candidates: set[str] = set()
    for _, _, text in selected_items:
        alias_candidates.update(alias_re.findall(text))
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
    )


@dataclass
class TkncContextSession:
    root: Path
    fs: ContextFilesystem
    json_codec: Any
    hash_service: Any
    token_counter: Any
    max_memory_bytes: int = 8_000_000
    index_data: dict[str, Any] | None = None
    index_text: str = ""
    manifest_data: dict[str, Any] | None = None
    manifest_text: str = ""
    resolved_aliases: dict[str, str] = field(default_factory=dict)
    cache_hits: int = 0
    cache_misses: int = 0

    def close(self) -> None:
        self.index_data = None
        self.manifest_data = None
        self.resolved_aliases.clear()

    @property
    def tknd_dir(self) -> Path:
        return self.root / "tknd"

    def search(self, question: str, *, query_id: str, limit: int = 4) -> ContextSearchResult:
        return search_context(self.root, question, self.fs, self.token_counter, query_id=query_id, limit=limit)

    def _load_index(self, *, query_id: str) -> dict[str, Any]:
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
        if manifest_hash != index_data.get("manifest_sha256"):
            raise SidecarValidationError("Corpus manifest is not bound to alias index")
        self.manifest_data = manifest
        return manifest

    def aliases_in_index(self, aliases: set[str], *, query_id: str) -> set[str]:
        index = self._load_index(query_id=query_id)
        ranges = index.get("ranges", [])
        found: set[str] = set()
        for alias in aliases:
            if not _looks_like_generated_alias(alias):
                continue
            for item in ranges:
                if item["first_alias"] <= alias <= item["last_alias"]:
                    found.add(alias)
                    break
        return found

    def required_chunks(self, aliases: set[str], *, query_id: str) -> tuple[str, ...]:
        index = self._load_index(query_id=query_id)
        chunks: set[str] = set()
        for alias in aliases:
            if not _looks_like_generated_alias(alias):
                continue
            for item in index.get("ranges", []):
                if item["first_alias"] <= alias <= item["last_alias"]:
                    chunks.add(_safe_chunk_filename(item["path"]))
                    break
        return tuple(sorted(chunks))

    def resolve(self, aliases: set[str], *, query_id: str) -> AliasResolutionResult:
        index = self._load_index(query_id=query_id)
        self._validate_manifest(index, query_id=query_id)
        cached = {alias: self.resolved_aliases[alias] for alias in aliases if alias in self.resolved_aliases}
        missing = set(aliases) - set(cached)
        if not missing:
            self.cache_hits += len(cached)
            return AliasResolutionResult(cached, set(), tuple(), 0, 0, self.token_counter.count(str(cached)))

        self.cache_misses += len(missing)
        resolver = SelectiveAliasResolver(self.fs, self.json_codec, self.hash_service, self.token_counter)
        result = resolver.resolve(missing, str(self.tknd_dir))
        self.resolved_aliases.update(result.resolved)
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
        )
