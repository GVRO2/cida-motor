import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable

from cida.domain.errors import SidecarValidationError


SEARCH_INDEX_FILENAME = "content-search-index.json"
SEARCH_INDEX_FORMAT = "cida-content-search-index"
SEARCH_SEGMENT_FORMAT = "cida-content-search-segment"
SEARCH_INDEX_SCHEMA_VERSION = 1
SEARCH_SEGMENT_SCHEMA_VERSION = 1
MAX_POSTINGS_PER_TERM = 2_000
MAX_TERMS_PER_FILE = 2_000
SINGLE_SEGMENT_FILE_LIMIT = 16
SINGLE_SEGMENT_ID = "all"
TERM_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")
CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
STEM_SUFFIXES = (
    "acoes",
    "acao",
    "oes",
    "ing",
    "tion",
    "sion",
    "ment",
    "ments",
    "adores",
    "ador",
    "antes",
    "ante",
    "arios",
    "ario",
    "ivas",
    "ivos",
    "iva",
    "ivo",
    "es",
    "s",
)
ALPHA_SEGMENT_BUCKETS = (
    ("a", "f", "a-f"),
    ("g", "l", "g-l"),
    ("m", "r", "m-r"),
    ("s", "z", "s-z"),
)
VALID_SEARCH_SEGMENTS = {SINGLE_SEGMENT_ID, "_", "0-9", *(bucket for _, _, bucket in ALPHA_SEGMENT_BUCKETS)}


@dataclass(frozen=True)
class ContentSearchIndexArtifacts:
    root: dict[str, Any]
    segments: dict[str, dict[str, Any]]


def normalize_terms(text: str) -> tuple[str, ...]:
    expanded_text = _identifier_words(text)
    terms = []
    for raw in TERM_RE.findall(expanded_text):
        term = raw.lower()
        if len(term) > 64:
            term = term[:64]
        terms.append(term)
        stem = _simple_stem(term)
        if stem != term:
            terms.append(stem)
    return tuple(dict.fromkeys(terms))


def _identifier_words(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    split = CAMEL_BOUNDARY_RE.sub(" ", ascii_text)
    return split.replace("_", " ").replace("-", " ")


def _simple_stem(term: str) -> str:
    for suffix in STEM_SUFFIXES:
        if term.endswith(suffix) and len(term) - len(suffix) >= 4:
            return term[: -len(suffix)]
    return term


def segment_id_for_term(term: str) -> str:
    normalized = term.lower()
    if not normalized:
        return "_"
    first = normalized[0]
    if first.isascii() and first.isdigit():
        return "0-9"
    if first.isascii() and first.isalpha():
        for start, end, bucket in ALPHA_SEGMENT_BUCKETS:
            if start <= first <= end:
                return bucket
    return "_"


def build_content_search_index_artifacts(
    files: Iterable[tuple[str, str]],
    *,
    corpus_id: str,
    hash_service: Any,
    json_codec: Any,
) -> ContentSearchIndexArtifacts:
    postings: dict[str, set[str]] = {}
    term_cache: dict[str, tuple[str, ...]] = {}
    file_count = 0
    for rel_path, text in sorted(files):
        safe_path = _safe_content_path(rel_path)
        file_count += 1
        path_terms = normalize_terms(safe_path.replace("/", " "))
        content_terms = term_cache.get(text)
        if content_terms is None:
            content_terms = normalize_terms(text)
            term_cache[text] = content_terms
        for term in tuple(dict.fromkeys((*path_terms, *content_terms)))[:MAX_TERMS_PER_FILE]:
            postings.setdefault(term, set()).add(safe_path)

    single_segment = 0 < file_count <= SINGLE_SEGMENT_FILE_LIMIT
    terms_by_segment: dict[str, dict[str, list[str]]] = {}
    for term, paths in sorted(postings.items()):
        segment_id = SINGLE_SEGMENT_ID if single_segment else segment_id_for_term(term)
        terms_by_segment.setdefault(segment_id, {})[term] = sorted(paths)[:MAX_POSTINGS_PER_TERM]

    segments: dict[str, dict[str, Any]] = {}
    segment_metadata: dict[str, dict[str, Any]] = {}
    for segment_id, terms in sorted(terms_by_segment.items()):
        segment_data: dict[str, Any] = {
            "format": SEARCH_SEGMENT_FORMAT,
            "schema_version": SEARCH_SEGMENT_SCHEMA_VERSION,
            "corpus_id": corpus_id,
            "segment_id": segment_id,
            "terms": terms,
        }
        segment_data["segment_sha256"] = hash_service.sha256(
            json_codec.canonical_encode(_canonical_segment_payload(segment_data)).encode("utf-8")
        )
        path = f"search-index/segment-{segment_id}.json"
        segments[path] = segment_data
        segment_metadata[segment_id] = {
            "path": path,
            "sha256": segment_data["segment_sha256"],
            "term_count": len(terms),
        }

    root: dict[str, Any] = {
        "format": SEARCH_INDEX_FORMAT,
        "schema_version": SEARCH_INDEX_SCHEMA_VERSION,
        "corpus_id": corpus_id,
        "segment_count": len(segment_metadata),
        "file_count": file_count,
        "segmentation": "single" if single_segment else "bucket",
        "segments": segment_metadata,
    }
    root["index_sha256"] = hash_service.sha256(json_codec.canonical_encode(_canonical_root_payload(root)).encode("utf-8"))
    return ContentSearchIndexArtifacts(root=root, segments=segments)


def validate_content_search_index(root: dict[str, Any], *, hash_service: Any, json_codec: Any, corpus_id: str | None = None) -> None:
    if not isinstance(root, dict):
        raise SidecarValidationError("Content search index must be a JSON object")
    if root.get("format") != SEARCH_INDEX_FORMAT:
        raise SidecarValidationError(f"Unsupported content search index format: {root.get('format')}")
    if root.get("schema_version") != SEARCH_INDEX_SCHEMA_VERSION:
        raise SidecarValidationError(f"Unsupported content search index schema: {root.get('schema_version')}")
    if corpus_id is not None and root.get("corpus_id") != corpus_id:
        raise SidecarValidationError("Content search index corpus_id mismatch")
    segments = root.get("segments")
    if not isinstance(segments, dict):
        raise SidecarValidationError("Content search index segments must be an object")
    if root.get("segment_count") != len(segments):
        raise SidecarValidationError("Content search index segment_count mismatch")
    if root.get("segmentation", "bucket") not in {"single", "bucket"}:
        raise SidecarValidationError(f"Unsupported content search index segmentation: {root.get('segmentation')}")
    for segment_id, metadata in segments.items():
        if segment_id not in VALID_SEARCH_SEGMENTS:
            raise SidecarValidationError(f"Invalid content search segment id: {segment_id}")
        if not isinstance(metadata, dict):
            raise SidecarValidationError(f"Content search segment metadata must be an object: {segment_id}")
        segment_path = metadata.get("path")
        if not isinstance(segment_path, str):
            raise SidecarValidationError(f"Invalid content search segment path: {segment_id}")
        _safe_segment_path(segment_path)
        if not _is_sha256(metadata.get("sha256")):
            raise SidecarValidationError(f"Invalid content search segment hash: {segment_id}")
        if not isinstance(metadata.get("term_count"), int) or metadata["term_count"] < 0:
            raise SidecarValidationError(f"Invalid content search segment term_count: {segment_id}")
    expected = root.get("index_sha256")
    if not _is_sha256(expected):
        raise SidecarValidationError("Content search index hash is missing or malformed")
    actual = hash_service.sha256(json_codec.canonical_encode(_canonical_root_payload(root)).encode("utf-8"))
    if actual != expected:
        raise SidecarValidationError("Content search index hash mismatch")


def validate_content_search_segment(
    segment: dict[str, Any],
    *,
    segment_id: str,
    expected_sha256: str,
    corpus_id: str,
    hash_service: Any,
    json_codec: Any,
) -> None:
    if not isinstance(segment, dict):
        raise SidecarValidationError("Content search segment must be a JSON object")
    if segment.get("format") != SEARCH_SEGMENT_FORMAT:
        raise SidecarValidationError(f"Unsupported content search segment format: {segment.get('format')}")
    if segment.get("schema_version") != SEARCH_SEGMENT_SCHEMA_VERSION:
        raise SidecarValidationError(f"Unsupported content search segment schema: {segment.get('schema_version')}")
    if segment.get("corpus_id") != corpus_id:
        raise SidecarValidationError(f"Content search segment corpus_id mismatch: {segment_id}")
    if segment.get("segment_id") != segment_id:
        raise SidecarValidationError(f"Content search segment id mismatch: {segment_id}")
    terms = segment.get("terms")
    if not isinstance(terms, dict):
        raise SidecarValidationError("Content search segment terms must be an object")
    for term, paths in terms.items():
        if segment_id != SINGLE_SEGMENT_ID and segment_id_for_term(term) != segment_id:
            raise SidecarValidationError(f"Content search term stored in wrong segment: {term}")
        if not isinstance(paths, list):
            raise SidecarValidationError(f"Content search postings must be a list: {term}")
        for path in paths:
            _safe_content_path(path)
    actual = hash_service.sha256(json_codec.canonical_encode(_canonical_segment_payload(segment)).encode("utf-8"))
    if segment.get("segment_sha256") != expected_sha256 or actual != expected_sha256:
        raise SidecarValidationError(f"Content search segment hash mismatch: {segment_id}")


def _canonical_root_payload(root: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": root.get("format"),
        "schema_version": root.get("schema_version"),
        "corpus_id": root.get("corpus_id"),
        "segment_count": root.get("segment_count"),
        "file_count": root.get("file_count"),
        "segmentation": root.get("segmentation", "bucket"),
        "segments": root.get("segments", {}),
    }


def _canonical_segment_payload(segment: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": segment.get("format"),
        "schema_version": segment.get("schema_version"),
        "corpus_id": segment.get("corpus_id"),
        "segment_id": segment.get("segment_id"),
        "terms": segment.get("terms", {}),
    }


def _safe_content_path(path: str) -> str:
    if not isinstance(path, str) or not path:
        raise SidecarValidationError(f"Invalid content search path: {path}")
    normalized = path.replace("\\", "/")
    parsed = PurePosixPath(normalized)
    if parsed.is_absolute() or ".." in parsed.parts:
        raise SidecarValidationError(f"Unsafe content search path: {path}")
    return normalized


def _safe_segment_path(path: str) -> str:
    if not isinstance(path, str) or not path.startswith("search-index/segment-") or not path.endswith(".json"):
        raise SidecarValidationError(f"Invalid content search segment path: {path}")
    parsed = PurePosixPath(path.replace("\\", "/"))
    if parsed.is_absolute() or ".." in parsed.parts:
        raise SidecarValidationError(f"Unsafe content search segment path: {path}")
    return path.replace("\\", "/")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None
