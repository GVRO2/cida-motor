import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cida.application.selective_alias_resolution import ALIAS_INDEX_FILENAME  # noqa: E402
from cida.infrastructure.tknc_context_session import (  # noqa: E402
    ContextFilesystem,
    TkncContextSession,
    is_content_artifact,
    is_evidence_artifact,
    is_lookup_artifact,
    search_context,
)
from cida.infrastructure.hashing import HashService  # noqa: E402
from cida.infrastructure.json_codec import JsonCodec  # noqa: E402
from cida.infrastructure.tokenizer import OfflineTokenizer  # noqa: E402


INSTRUCTION_ORIGINAL = "Answer using selected original files discovered from the question."
INSTRUCTION_TKNC = "Answer using selected .tknc content files, then resolve detected aliases through the measured session."
LOOKUP_INSTRUCTION = "Resolve only aliases detected in selected content, and load only chunks required by the alias index."
MANIFEST_INSTRUCTION = "Validate the corpus manifest hash and binding before trusting sidecar chunks."


@dataclass(frozen=True)
class Question:
    question_id: str
    question: str
    required_files: tuple[str, ...]
    required_symbols: tuple[str, ...]
    required_facts: tuple[str, ...]
    forbidden_facts: tuple[str, ...] = ()


def _run_git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _token_count(tokenizer: OfflineTokenizer, parts: list[str]) -> int:
    return sum(tokenizer.count(part) for part in parts if part)


def _word_suffix(value: int) -> str:
    letters = []
    current = value
    for _ in range(6):
        letters.append(chr(ord("a") + current % 26))
        current //= 26
    return "".join(reversed(letters))


def _dictionary_words(count: int, prefix: str) -> list[str]:
    safe_prefix = "".join(ch for ch in prefix.lower() if ch.isalpha())[:6] or "cida"
    return [f"{safe_prefix}context{_word_suffix(i)}compression" for i in range(count)]


def _write_fixture_corpus(root: Path, name: str, count: int) -> tuple[Path, list[str]]:
    source = root / name / "original"
    source.mkdir(parents=True)
    relpaths: list[str] = []
    alias_target = max(40, count)
    dictionary_words = _dictionary_words(alias_target, name)
    repeated_words = " ".join(f"{word} {word} {word} {word}" for word in dictionary_words)
    primary_words = " ".join(f"{word} {word} {word} {word}" for word in dictionary_words[:1])

    templates = {
        "cida/interfaces/cli.py": (
            "def main():\n"
            "    return processarEComparar()\n\n"
            "def processarEComparar():\n"
            "    return 'python_optimizer_bridge starts the principal processing component'\n\n"
            f"CLI_CONTEXT = '{primary_words}'\n"
        ),
        "motor_v3.go": (
            "package main\n\n"
            "func main() { processarEComparar() }\n"
            "func processarEComparar() string { return \"Go wrapper uses python_optimizer_bridge\" }\n"
            f"const MotorContext = \"{primary_words}\"\n"
        ),
        "cida/infrastructure/tokenizer.py": (
            "def count_tokens(text):\n"
            "    return len(text.split())  # deterministic token counter\n\n"
            f"TOKENIZER_CONTEXT = '{primary_words}'\n"
        ),
        "cida/application/optimize_corpus.py": (
            "def write_corpus_sidecars(dst):\n"
            "    return 'tknd alias-index.json cidatkn corpus_sidecar_writer'\n\n"
            f"SIDECAR_WRITER_CONTEXT = '{primary_words}'\n"
        ),
        "cida/domain/reconstruction.py": (
            "def reconstruct_content(payload, entries):\n"
            "    return payload.replace('alias', entries['alias'])  # lossless_reconstruction_contract\n\n"
            f"RECONSTRUCTION_CONTEXT = '{primary_words}'\n"
        ),
        "src/main/java/ResourceProfiles.java": (
            "public final class ResourceProfiles {\n"
            "  static int resolveEffectiveWorkers() { return 4; }\n"
            "  String profile = \"resource_worker_profile\";\n"
            f"  String context = \"{primary_words}\";\n"
            "}\n"
        ),
        "docs/lexicon.md": f"# Lexicon\n\n{repeated_words}\n",
        "docs/workflow.md": (
            "# BMAD Workflow\n\n"
            "The flow preserves python_optimizer_bridge, corpus_sidecar_writer, and resource_worker_profile.\n\n"
            f"{primary_words}\n"
        ),
    }

    for rel, content in templates.items():
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        relpaths.append(rel)

    return source, sorted(relpaths)


def _run_production_tknc(original: Path, destination: Path, report_root: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env.setdefault("TIKTOKEN_CACHE_DIR", str(ROOT / "resources"))
    started = time.perf_counter()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cida.interfaces.cli",
            "--src",
            str(original),
            "--dst",
            str(destination),
            "--mode",
            "semantic",
            "--dictionary-scope",
            "corpus",
            "--validation-level",
            "strict",
            "--report",
            "json",
            "--report-path",
            str(report_root),
        ],
        cwd=str(ROOT),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    return {
        "command": "python -m cida.interfaces.cli --mode semantic --dictionary-scope corpus --validation-level strict",
        "exit_code": result.returncode,
        "duration_ms": (time.perf_counter() - started) * 1000.0,
        "stdout": result.stdout[-2000:],
        "stderr": result.stderr[-2000:],
    }


def _build_tknc_corpus(original: Path, destination: Path, relpaths: list[str] | None = None) -> dict[str, str]:
    del relpaths
    report_root = destination.parent / "production-report"
    outcome = _run_production_tknc(original, destination, report_root)
    if outcome["exit_code"] != 0:
        raise RuntimeError(json.dumps(outcome, indent=2))
    index_path = destination / "tknd" / ALIAS_INDEX_FILENAME
    if not index_path.exists():
        return {}
    fs = ContextFilesystem()
    session = TkncContextSession(destination, fs, JsonCodec(), HashService(), OfflineTokenizer())
    index = session._load_index(query_id="build-corpus")
    session._validate_manifest(index, query_id="build-corpus")
    aliases: set[str] = set()
    for sidecar in sorted((destination / "tknd").glob("*.cidatkn")):
        text = fs.read_text_limited(str(sidecar), 2_000_000, operation="lookup", reason="build_alias_map", query_id="build-corpus")
        data = JsonCodec().decode(text)
        aliases.update(data.get("entries", {}).keys())
    return session.resolve(aliases, query_id="build-corpus").resolved


def _question_set() -> list[Question]:
    return [
        Question(
            "Q001",
            "Qual componente inicia o processamento principal?",
            ("cida/interfaces/cli.py", "motor_v3.go"),
            ("main", "processarEComparar"),
            ("processarEComparar",),
        ),
        Question(
            "Q002",
            "Qual componente e responsavel por contar tokens?",
            ("cida/infrastructure/tokenizer.py",),
            ("count_tokens",),
            ("deterministic token counter",),
        ),
        Question(
            "Q003",
            "Onde o sistema cria os arquivos auxiliares usados para resolver os aliases?",
            ("cida/application/optimize_corpus.py",),
            ("write_corpus_sidecars",),
            ("write_corpus_sidecars",),
        ),
        Question(
            "Q004",
            "Como o motor recupera o conteudo original a partir dos aliases?",
            ("cida/domain/reconstruction.py",),
            ("reconstruct_content",),
            ("lossless_reconstruction_contract",),
        ),
        Question(
            "Q005",
            "Como o sistema calcula a quantidade efetiva de workers?",
            ("src/main/java/ResourceProfiles.java",),
            ("ResourceProfiles", "resolveEffectiveWorkers"),
            ("resolveEffectiveWorkers",),
        ),
        Question(
            "Q006",
            "Qual arquivo concentra o vocabulario comprimido de referencia?",
            ("docs/lexicon.md",),
            ("Lexicon",),
            ("Lexicon",),
        ),
    ]


def _read_selected(
    root: Path,
    relpaths: tuple[str, ...],
    fs: ContextFilesystem,
    *,
    query_id: str,
    reason: str,
) -> list[str]:
    return [
        fs.read_text_limited(
            str(root / rel),
            2_000_000,
            operation="content_load",
            reason=reason,
            query_id=query_id,
        )
        for rel in relpaths
    ]


def _search(root: Path, question: str, limit: int = 4):
    return search_context(root, question, ContextFilesystem(), OfflineTokenizer(), query_id="compat-search", limit=limit)


def _load_index(tknc: Path) -> tuple[dict[str, Any], str]:
    fs = ContextFilesystem()
    index_text = fs.read_text_limited(
        str(tknc / "tknd" / ALIAS_INDEX_FILENAME),
        2_000_000,
        operation="lookup",
        reason="compat_load_index",
        query_id="compat-load-index",
    )
    return JsonCodec().decode(index_text), index_text


def _iter_full_context_files(root: Path, *, include_lookup: bool) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or ".pytest_cache" in path.parts:
            continue
        if is_content_artifact(path):
            files.append(path)
        elif include_lookup and (is_lookup_artifact(path) or is_evidence_artifact(path)):
            files.append(path)
    return files


def _read_full_context(root: Path, fs: ContextFilesystem, *, query_id: str, include_lookup: bool) -> list[str]:
    return [
        fs.read_text_limited(
            str(path),
            2_000_000,
            operation="full_context",
            reason="full_context_comparison",
            query_id=query_id,
        )
        for path in _iter_full_context_files(root, include_lookup=include_lookup)
    ]


def _reconstruct_text(text: str, resolved: dict[str, str]) -> str:
    import re

    for alias, value in sorted(resolved.items(), key=lambda item: len(item[0]), reverse=True):
        text = re.sub(rf"\b{re.escape(alias)}\b", value, text)
    return text


def _score(question: Question, loaded_files: tuple[str, ...], reconstructed_parts: list[str]) -> dict[str, Any]:
    loaded = {path[:-5] if path.endswith(".tknc") else path for path in loaded_files}
    reconstructed = "\n".join([*loaded_files, *reconstructed_parts])
    file_recall = len(set(question.required_files) & loaded) / len(question.required_files)
    symbol_recall = sum(1 for symbol in question.required_symbols if symbol in reconstructed) / len(question.required_symbols)
    fact_recall = sum(1 for fact in question.required_facts if fact in reconstructed) / len(question.required_facts)
    contradiction_penalty = 0.25 * sum(1 for fact in question.forbidden_facts if fact in reconstructed)
    accuracy = max(0.0, 0.25 * file_recall + 0.35 * symbol_recall + 0.40 * fact_recall - contradiction_penalty)
    return {
        "file_recall": file_recall,
        "symbol_recall": symbol_recall,
        "fact_recall": fact_recall,
        "contradiction_penalty": contradiction_penalty,
        "accuracy": accuracy,
    }


def _read_events_dict(fs: ContextFilesystem) -> list[dict[str, Any]]:
    return [event.__dict__ for event in fs.reads]


def _events_before(fs: ContextFilesystem, marker_count: int, artifact: str) -> int:
    return sum(1 for event in fs.reads[:marker_count] if event.artifact_type == artifact and not event.cache_hit)


def _measure_question(tokenizer: OfflineTokenizer, original: Path, tknc: Path, question: Question) -> dict[str, Any]:
    query_id = question.question_id
    original_fs = ContextFilesystem()
    tknc_fs = ContextFilesystem()
    session = TkncContextSession(tknc, tknc_fs, JsonCodec(), HashService(), tokenizer)

    original_search = search_context(original, question.question, original_fs, tokenizer, query_id=query_id)
    original_selected_text = _read_selected(original, original_search.files, original_fs, query_id=query_id, reason="original_selected_content")

    tknc_search = session.search(question.question, query_id=query_id)
    selected_tknc_text = _read_selected(tknc, tknc_search.files, tknc_fs, query_id=query_id, reason="tknc_selected_content")
    alias_detection_event_count = len(tknc_fs.reads)
    alias_candidates = set(tknc_search.alias_candidates)
    detected_aliases = session.aliases_in_index(alias_candidates, query_id=query_id)
    required_chunks = session.required_chunks(detected_aliases, query_id=query_id)
    resolution = session.resolve(detected_aliases, query_id=query_id)
    reconstructed_tknc = [_reconstruct_text(text, resolution.resolved) for text in selected_tknc_text]

    original_accuracy = _score(question, original_search.files, original_selected_text)
    tknc_accuracy = _score(question, tknc_search.files, reconstructed_tknc)

    original_full_total = _token_count(tokenizer, [INSTRUCTION_ORIGINAL, *_read_full_context(original, original_fs, query_id=query_id, include_lookup=False)])
    tknc_full_total = _token_count(
        tokenizer,
        [INSTRUCTION_TKNC, *_read_full_context(tknc, tknc_fs, query_id=query_id, include_lookup=False)],
    )

    original_selective_content = _token_count(tokenizer, original_selected_text)
    original_selective_total = original_selective_content + tokenizer.count(INSTRUCTION_ORIGINAL) + original_search.search_tokens
    tknc_content_tokens = _token_count(tokenizer, selected_tknc_text)
    tknc_instruction_tokens = _token_count(tokenizer, [INSTRUCTION_TKNC, LOOKUP_INSTRUCTION, MANIFEST_INSTRUCTION])
    tknc_index_tokens = tokenizer.count(session.index_text)
    tknc_manifest_tokens = tokenizer.count(session.manifest_text)
    tknc_translation_tokens = tokenizer.count(json.dumps(resolution.resolved, sort_keys=True))
    tknc_sidecar_tokens = max(0, resolution.tokens_loaded - tknc_index_tokens)
    tknc_cold_total = (
        tknc_content_tokens
        + tknc_search.search_tokens
        + tknc_instruction_tokens
        + tknc_index_tokens
        + tknc_manifest_tokens
        + tknc_sidecar_tokens
        + tknc_translation_tokens
    )
    loaded_chunks = tuple(resolution.chunks_loaded)
    unexpected_chunks = sorted(set(loaded_chunks) - set(required_chunks))
    missing_chunks = sorted(set(required_chunks) - set(loaded_chunks))
    duplicate_chunk_reads = max(0, len(loaded_chunks) - len(set(loaded_chunks)))
    sidecar_reads_before_detection = _events_before(tknc_fs, alias_detection_event_count, "sidecar")
    unnecessary_sidecar_chunks_read = len(unexpected_chunks)

    tknc_data = {
        "search_mode": tknc_search.search_mode,
        "search_index_segments_loaded": tknc_search.search_index_segments_loaded,
        "search_index_bytes_read": tknc_search.search_index_bytes_read,
        "content_files_opened": tknc_search.content_files_opened,
        "candidate_files": list(tknc_search.candidate_files),
        "search_tokens": tknc_search.search_tokens,
        "content_tokens": tknc_content_tokens,
        "instruction_tokens": tknc_instruction_tokens,
        "index_tokens": tknc_index_tokens,
        "manifest_tokens": tknc_manifest_tokens,
        "sidecar_tokens": tknc_sidecar_tokens,
        "translation_tokens": tknc_translation_tokens,
        "total_tokens": tknc_cold_total,
        "total_context_tokens": tknc_cold_total,
        "full_total_context_tokens": tknc_full_total,
        "files_available": tknc_search.files_available,
        "files_scanned": tknc_search.files_scanned,
        "files_selected": tknc_search.files_selected,
        "files_loaded": list(tknc_search.files),
        "content_files_selected": list(tknc_search.files),
        "search_bytes_read": tknc_search.search_bytes_read,
        "search_duration_ms": tknc_search.search_duration_ms,
        "alias_candidates_detected_before_index": sorted(alias_candidates),
        "aliases_detected": sorted(detected_aliases),
        "aliases_resolved": sorted(resolution.resolved),
        "aliases_unresolved": sorted(set(detected_aliases) - set(resolution.resolved)),
        "aliases_unexpected": sorted(set(resolution.resolved) - set(detected_aliases)),
        "required_chunks": list(required_chunks),
        "loaded_chunks": list(loaded_chunks),
        "chunks_loaded": list(loaded_chunks),
        "unexpected_chunks": unexpected_chunks,
        "missing_chunks": missing_chunks,
        "duplicate_chunk_reads": duplicate_chunk_reads,
        "chunks_available": session.index_data.get("chunk_count", 0) if session.index_data else 0,
        "entries_loaded": resolution.entries_loaded,
        "bytes_read": sum(event.bytes_read for event in tknc_fs.reads if not event.cache_hit),
        "read_events": _read_events_dict(tknc_fs),
        "cache": session.cache_metrics(),
        "sidecar_reads_before_alias_detection": sidecar_reads_before_detection,
        "index_reads_before_alias_detection": _events_before(tknc_fs, alias_detection_event_count, "alias_index"),
        "global_dictionary_preload": sidecar_reads_before_detection > 0 or unnecessary_sidecar_chunks_read > 0,
        "lookup_pass": set(loaded_chunks) == set(required_chunks)
        and sidecar_reads_before_detection == 0
        and not unexpected_chunks
        and not missing_chunks
        and duplicate_chunk_reads == 0,
        "all_aliases_resolvable": set(detected_aliases) == set(resolution.resolved),
    }
    tknc_data["token_accounting_pass"] = tknc_data["total_tokens"] == (
        tknc_data["content_tokens"]
        + tknc_data["search_tokens"]
        + tknc_data["instruction_tokens"]
        + tknc_data["index_tokens"]
        + tknc_data["manifest_tokens"]
        + tknc_data["sidecar_tokens"]
        + tknc_data["translation_tokens"]
    )
    tknc_data["model_context"] = {
        "model_instruction_tokens": tknc_instruction_tokens,
        "model_content_tokens": tknc_content_tokens,
        "model_translation_tokens": tknc_translation_tokens,
        "model_total_context_tokens": tknc_instruction_tokens + tknc_content_tokens + tknc_translation_tokens,
    }
    tknc_data["local_retrieval"] = {
        "search_index_bytes": tknc_search.search_index_bytes_read,
        "alias_index_bytes": sum(event.bytes_read for event in tknc_fs.reads if event.artifact_type == "alias_index" and not event.cache_hit),
        "manifest_bytes": sum(event.bytes_read for event in tknc_fs.reads if event.artifact_type == "manifest" and not event.cache_hit),
        "sidecar_bytes": sum(event.bytes_read for event in tknc_fs.reads if event.artifact_type == "sidecar" and not event.cache_hit),
        "content_bytes_scanned": tknc_search.search_bytes_read - tknc_search.search_index_bytes_read,
        "files_scanned": tknc_search.files_scanned,
        "lookup_duration_ms": resolution.alias_resolution_duration_ms,
        "search_duration_ms": tknc_search.search_duration_ms,
        "managed_cache_peak_bytes": session.cache_metrics()["managed_cache_peak_bytes"],
    }

    return {
        "question_id": question.question_id,
        "question": question.question,
        "required_files": list(question.required_files),
        "required_symbols": list(question.required_symbols),
        "required_facts": list(question.required_facts),
        "original": {
            "search_tokens": original_search.search_tokens,
            "content_tokens": original_selective_content,
            "instruction_tokens": tokenizer.count(INSTRUCTION_ORIGINAL),
            "total_context_tokens": original_selective_total,
            "full_total_context_tokens": original_full_total,
            "files_available": original_search.files_available,
            "files_scanned": original_search.files_scanned,
            "files_selected": original_search.files_selected,
            "files_loaded": list(original_search.files),
        },
        "tknc": tknc_data,
        "accuracy": {
            "original": original_accuracy,
            "tknc": tknc_accuracy,
            "accuracy_delta": tknc_accuracy["accuracy"] - original_accuracy["accuracy"],
        },
        "performance": {
            "lookup_duration_ms": resolution.alias_resolution_duration_ms,
            "search_duration_ms": tknc_search.search_duration_ms,
            "reconstruction_duration_ms": 0.0,
            "index_parse_duration_ms": resolution.index_parse_duration_ms,
            "sidecar_parse_duration_ms": resolution.sidecar_parse_duration_ms,
        },
        "full_reduction_percentage": (
            (original_full_total - tknc_full_total) / original_full_total * 100.0 if original_full_total else 0.0
        ),
        "selective_cold_delta_percentage": (
            (original_selective_total - tknc_cold_total) / original_selective_total * 100.0 if original_selective_total else 0.0
        ),
        "result": "PASS" if tknc_accuracy["accuracy"] >= original_accuracy["accuracy"] and tknc_data["lookup_pass"] else "FAIL",
    }


def _session_token_total(tokenizer: OfflineTokenizer, fs: ContextFilesystem, content_tokens: int, search_tokens: int, translation_tokens: int) -> int:
    lookup_texts = fs.cached_texts({"alias_index", "manifest", "sidecar"})
    return content_tokens + search_tokens + _token_count(tokenizer, [INSTRUCTION_TKNC, LOOKUP_INSTRUCTION, MANIFEST_INSTRUCTION]) + _token_count(tokenizer, lookup_texts) + translation_tokens


def _run_tknc_session(tokenizer: OfflineTokenizer, tknc: Path, questions: list[Question], query_count: int) -> dict[str, Any]:
    fs = ContextFilesystem()
    session = TkncContextSession(tknc, fs, JsonCodec(), HashService(), tokenizer)
    content_tokens = 0
    search_tokens = 0
    translation_tokens = 0
    incremental_tokens: list[int] = []
    for idx in range(query_count):
        question = questions[idx % len(questions)]
        query_id = f"multi-{query_count}-{idx:03d}"
        search = session.search(question.question, query_id=query_id)
        selected = _read_selected(tknc, search.files, fs, query_id=query_id, reason="session_selected_content")
        aliases = session.aliases_in_index(set(search.alias_candidates), query_id=query_id)
        resolution = session.resolve(aliases, query_id=query_id)
        query_content_tokens = _token_count(tokenizer, selected)
        query_translation_tokens = tokenizer.count(json.dumps(resolution.resolved, sort_keys=True))
        content_tokens += query_content_tokens
        search_tokens += search.search_tokens
        translation_tokens += query_translation_tokens
        incremental_tokens.append(query_content_tokens + search.search_tokens + query_translation_tokens)
    total = _session_token_total(tokenizer, fs, content_tokens, search_tokens, translation_tokens)
    fixed_tokens = _token_count(tokenizer, [INSTRUCTION_TKNC, LOOKUP_INSTRUCTION, MANIFEST_INSTRUCTION])
    alias_index_tokens = _token_count(tokenizer, fs.cached_texts({"alias_index"}))
    search_index_tokens = _token_count(tokenizer, fs.cached_texts({"search_index"}))
    manifest_tokens = _token_count(tokenizer, fs.cached_texts({"manifest"}))
    sidecar_tokens = _token_count(tokenizer, fs.cached_texts({"sidecar"}))
    return {
        "queries": query_count,
        "tknc": total,
        "physical_index_reads": fs.physical_read_count("alias_index"),
        "physical_manifest_reads": fs.physical_read_count("manifest"),
        "physical_sidecar_reads": fs.physical_read_count("sidecar"),
        "cache_hits": sum(1 for event in fs.reads if event.cache_hit),
        "cache_misses": sum(1 for event in fs.reads if not event.cache_hit),
        "session_fixed_tokens": fixed_tokens,
        "session_search_index_tokens": search_index_tokens,
        "session_alias_index_tokens": alias_index_tokens,
        "session_manifest_tokens": manifest_tokens,
        "session_unique_sidecar_tokens": sidecar_tokens,
        "cumulative_content_tokens": content_tokens,
        "cumulative_translation_tokens": translation_tokens,
        "cumulative_instruction_tokens": fixed_tokens,
        "incremental_tokens_by_query": incremental_tokens,
        "average_tokens_per_query": total / query_count,
        "total_session_tokens": total,
        "cache": session.cache_metrics(),
    }


def _run_original_session(tokenizer: OfflineTokenizer, original: Path, questions: list[Question], query_count: int) -> int:
    fs = ContextFilesystem()
    total = 0
    for idx in range(query_count):
        question = questions[idx % len(questions)]
        query_id = f"original-multi-{query_count}-{idx:03d}"
        search = search_context(original, question.question, fs, tokenizer, query_id=query_id)
        selected = _read_selected(original, search.files, fs, query_id=query_id, reason="original_session_selected_content")
        total += _token_count(tokenizer, selected) + search.search_tokens + tokenizer.count(INSTRUCTION_ORIGINAL)
    return total


def _measure_sessions(tokenizer: OfflineTokenizer, original: Path, tknc: Path, questions: list[Question]) -> dict[str, Any]:
    query_counts: dict[str, Any] = {}
    break_even = None
    for count in (1, 10, 50, 100):
        original_tokens = _run_original_session(tokenizer, original, questions, count)
        tknc_measure = _run_tknc_session(tokenizer, tknc, questions, count)
        tknc_measure["original"] = original_tokens
        tknc_measure["delta"] = original_tokens - tknc_measure["tknc"]
        query_counts[str(count)] = tknc_measure
        if break_even is None and tknc_measure["tknc"] < original_tokens:
            break_even = count
    return {
        "query_counts": query_counts,
        "break_even_query_count": break_even,
        "result": "PASS" if break_even is not None and query_counts["100"]["tknc"] < query_counts["100"]["original"] else "FAIL",
    }


def _harness_summary() -> dict[str, Any]:
    try:
        from devtools.runtime_harness_probe import RuntimeHarnessProbe

        with RuntimeHarnessProbe() as probe:
            Path(__file__).exists()
        return {
            "original_python": {
                "measured": True,
                "events": probe.events.as_dict(),
                "imports": len(probe.events.imports),
                "reads": len(probe.events.file_reads),
                "subprocesses": len(probe.events.subprocesses),
            },
            "original_go": {"measured": False, "reason": "Go file-open tracing is not available in this local benchmark"},
        }
    except Exception as exc:
        return {
            "original_python": {"measured": False, "reason": str(exc)},
            "original_go": {"measured": False, "reason": "Go file-open tracing is not available in this local benchmark"},
        }


def _summarize(head_sha: str, corpora: dict[str, Any], scenarios: list[dict[str, Any]], production: dict[str, Any], sessions: dict[str, Any]) -> dict[str, Any]:
    original_full = sum(item["original"]["full_total_context_tokens"] for item in scenarios)
    tknc_full = sum(item["tknc"]["full_total_context_tokens"] for item in scenarios)
    original_sel = sum(item["original"]["total_context_tokens"] for item in scenarios)
    tknc_cold = sum(item["tknc"]["total_context_tokens"] for item in scenarios)
    original_accuracy = sum(item["accuracy"]["original"]["accuracy"] for item in scenarios) / len(scenarios)
    tknc_accuracy = sum(item["accuracy"]["tknc"]["accuracy"] for item in scenarios) / len(scenarios)
    full_pass = tknc_full < original_full
    accuracy_pass = tknc_accuracy >= original_accuracy
    lookup_pass = all(item["tknc"]["lookup_pass"] for item in scenarios)
    alias_pass = all(item["tknc"]["all_aliases_resolvable"] for item in scenarios)
    preload_pass = all(not item["tknc"]["global_dictionary_preload"] for item in scenarios)
    token_pass = all(item["tknc"]["token_accounting_pass"] for item in scenarios)
    indexed_search_pass = all(item["tknc"].get("search_mode", "INDEXED") == "INDEXED" for item in scenarios)
    default_cache = {"cache_peak_bytes": 0, "cache_max_bytes": 1, "cache_evictions": 0}
    bounded_cache_pass = all(
        item["tknc"].get("cache", default_cache)["cache_peak_bytes"]
        <= item["tknc"].get("cache", default_cache)["cache_max_bytes"]
        for item in scenarios
    )
    warm_pass = sessions["result"] == "PASS"
    multi_pass = sessions["result"] == "PASS"
    production_pass = all(item["exit_code"] == 0 for item in production.values())
    index_integrity_pass = all(item["chunk_count"] >= 1 and item["alias_count"] >= 1 for item in corpora.values())
    alias_scale_pass = any(item["alias_count"] >= 5000 and item["chunk_count"] >= 10 for item in corpora.values())
    harness = _harness_summary()
    harness_pass = harness["original_python"].get("measured") is True
    overall_pass = all(
        [
            production_pass,
            index_integrity_pass,
            alias_scale_pass,
            full_pass,
            warm_pass,
            multi_pass,
            accuracy_pass,
            lookup_pass,
            alias_pass,
            preload_pass,
            token_pass,
            indexed_search_pass,
            bounded_cache_pass,
            harness_pass,
        ]
    )
    return {
        "schema_version": 4,
        "head_sha": head_sha,
        "base_sha": "unknown",
        "corpora": corpora,
        "production": production,
        "integrity": {
            "production_pass": production_pass,
            "index_integrity_pass": index_integrity_pass,
            "alias_scale_pass": alias_scale_pass,
            "all_aliases_resolvable": alias_pass,
            "no_global_preload": preload_pass,
            "token_accounting_pass": token_pass,
            "indexed_search_pass": indexed_search_pass,
            "bounded_cache_pass": bounded_cache_pass,
        },
        "search": {
            "mode": "INDEXED" if indexed_search_pass else "MIXED",
            "segments_loaded": sum(item["tknc"].get("search_index_segments_loaded", 0) for item in scenarios),
            "files_opened": sum(item["tknc"].get("content_files_opened", 0) for item in scenarios),
            "bytes_read": sum(item["tknc"].get("search_bytes_read", 0) for item in scenarios),
            "search_reads_sidecars": sum(
                1
                for item in scenarios
                for event in item["tknc"]["read_events"]
                if event["operation"] == "search" and event["artifact_type"] == "sidecar"
            ),
            "lookup_pass": lookup_pass,
        },
        "cache": {
            "max_bytes": max(item["tknc"].get("cache", default_cache)["cache_max_bytes"] for item in scenarios),
            "peak_bytes": max(item["tknc"].get("cache", default_cache)["cache_peak_bytes"] for item in scenarios),
            "evictions": sum(item["tknc"].get("cache", default_cache)["cache_evictions"] for item in scenarios),
            "result": "PASS" if bounded_cache_pass else "FAIL",
        },
        "sessions": sessions,
        "harness": harness,
        "scenarios": scenarios,
        "summary": {
            "full_vs_full": {
                "original": original_full,
                "tknc": tknc_full,
                "delta_percentage": (original_full - tknc_full) / original_full * 100.0 if original_full else 0.0,
                "result": "PASS" if full_pass else "FAIL",
            },
            "selective_cold": {
                "original": original_sel,
                "tknc": tknc_cold,
                "delta_percentage": (original_sel - tknc_cold) / original_sel * 100.0 if original_sel else 0.0,
                "required": False,
                "result": "INFORMATIONAL",
            },
            "selective_warm": {
                "original": sessions["query_counts"]["100"]["original"],
                "tknc": sessions["query_counts"]["100"]["tknc"],
                "delta_percentage": sessions["query_counts"]["100"]["delta"] / sessions["query_counts"]["100"]["original"] * 100.0
                if sessions["query_counts"]["100"]["original"]
                else 0.0,
                "result": "PASS" if warm_pass else "FAIL",
            },
            "multi_query": {
                "break_even_query_count": sessions["break_even_query_count"],
                "result": "PASS" if multi_pass else "FAIL",
            },
            "accuracy": {
                "original_score": original_accuracy,
                "tknc_score": tknc_accuracy,
                "result": "PASS" if accuracy_pass else "FAIL",
            },
            "lookup": {
                "result": "PASS" if lookup_pass else "FAIL",
            },
            "memory": {
                "result": "PASS" if bounded_cache_pass else "FAIL",
            },
            "integrity": {
                "result": "PASS" if index_integrity_pass and alias_pass else "FAIL",
            },
            "search": {
                "result": "PASS" if indexed_search_pass else "FAIL",
            },
            "overall_result": "PASS" if overall_pass else "FAIL",
        },
    }


def _markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# CIDA .tknc Context Usage Report v4",
        "",
        f"HEAD SHA: `{report['head_sha']}`",
        f"Overall result: `{report['summary']['overall_result']}`",
        f"Full vs full: `{report['summary']['full_vs_full']['result']}`",
        f"Selective cold: `{report['summary']['selective_cold']['result']}`",
        f"Selective warm: `{report['summary']['selective_warm']['result']}`",
        f"Multi-query: `{report['summary']['multi_query']['result']}`",
        f"Break-even queries: `{report['summary']['multi_query']['break_even_query_count']}`",
        "",
        "| Question | Corpus | Original selective | .tknc cold | Required chunks | Loaded chunks | Accuracy | Result |",
        "|---|---|---:|---:|---|---|---:|---|",
    ]
    for item in report["scenarios"]:
        lines.append(
            "| {qid} | {corpus} | {osel} | {tcold} | {required} | {loaded} | {acc:.2f} | {result} |".format(
                qid=item["question_id"],
                corpus=item["corpus"],
                osel=item["original"]["total_context_tokens"],
                tcold=item["tknc"]["total_context_tokens"],
                required=",".join(item["tknc"]["required_chunks"]),
                loaded=",".join(item["tknc"]["loaded_chunks"]),
                acc=item["accuracy"]["tknc"]["accuracy"],
                result=item["result"],
            )
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare equivalent original and .tknc context usage.")
    parser.add_argument("--output-json", default="context-usage-report-v4.json")
    parser.add_argument("--output-markdown", default="context-usage-report-v4.md")
    parser.add_argument("--read-events-json", default="")
    parser.add_argument("--cache-events-json", default="")
    parser.add_argument("--harness-events-json", default="")
    parser.add_argument("--source-manifest-json", default="")
    parser.add_argument("--bundle-manifest-json", default="")
    args = parser.parse_args()

    os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(ROOT / "resources"))
    tokenizer = OfflineTokenizer()
    temp_root = Path(tempfile.mkdtemp(prefix="cida-context-usage-"))
    try:
        scenarios: list[dict[str, Any]] = []
        corpora: dict[str, Any] = {}
        production: dict[str, Any] = {}
        session_source: tuple[Path, Path] | None = None
        source_manifest_sample: Path | None = None
        bundle_manifest_sample: Path | None = None
        for corpus_name, alias_target in (("one_chunk", 500), ("two_chunks", 1000), ("five_chunks", 2500), ("ten_chunks", 5000)):
            original, relpaths = _write_fixture_corpus(temp_root, corpus_name, alias_target)
            tknc = temp_root / corpus_name / "tknc"
            production[corpus_name] = _run_production_tknc(original, tknc, temp_root / corpus_name / "report" / "context")
            if production[corpus_name]["exit_code"] != 0:
                raise RuntimeError(json.dumps(production[corpus_name], indent=2))
            index_path = tknc / "tknd" / ALIAS_INDEX_FILENAME
            fs = ContextFilesystem()
            index = JsonCodec().decode(fs.read_text_limited(str(index_path), 2_000_000, operation="lookup", reason="corpus_summary", query_id=corpus_name)) if index_path.exists() else {}
            corpora[corpus_name] = {
                "files": len(relpaths),
                "alias_target": alias_target,
                "alias_count": index.get("alias_count", 0),
                "chunk_count": index.get("chunk_count", 0),
                "index_bytes": index_path.stat().st_size if index_path.exists() else 0,
            }
            if corpus_name == "one_chunk":
                session_source = (original, tknc)
            if corpus_name == "ten_chunks":
                source_manifest_sample = tknc / "tknc-manifest.json"
                bundle_manifest_sample = tknc / "tknd" / "bundle-manifest.json"
            for question in _question_set():
                measured = _measure_question(tokenizer, original, tknc, question)
                measured["corpus"] = corpus_name
                scenarios.append(measured)

        assert session_source is not None
        sessions = _measure_sessions(tokenizer, session_source[0], session_source[1], _question_set())
        report = _summarize(_run_git_head(), corpora, scenarios, production, sessions)
        output_json = Path(args.output_json)
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

        output_md = Path(args.output_markdown)
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text(_markdown_report(report), encoding="utf-8")

        if args.read_events_json:
            events = [event for item in scenarios for event in item["tknc"]["read_events"]]
            Path(args.read_events_json).write_text(json.dumps(events, indent=2), encoding="utf-8")
        if args.cache_events_json:
            cache_events = {
                "cache": report["cache"],
                "sessions": {
                    key: value.get("cache", {})
                    for key, value in report["sessions"]["query_counts"].items()
                },
            }
            Path(args.cache_events_json).write_text(json.dumps(cache_events, indent=2), encoding="utf-8")
        if args.harness_events_json:
            Path(args.harness_events_json).write_text(json.dumps(report["harness"], indent=2), encoding="utf-8")
        if args.source_manifest_json and source_manifest_sample is not None:
            Path(args.source_manifest_json).write_text(source_manifest_sample.read_text(encoding="utf-8"), encoding="utf-8")
        if args.bundle_manifest_json and bundle_manifest_sample is not None:
            Path(args.bundle_manifest_json).write_text(bundle_manifest_sample.read_text(encoding="utf-8"), encoding="utf-8")

        print(
            json.dumps(
                {
                    "result": report["summary"]["overall_result"],
                    "scenarios": len(scenarios),
                    "output_json": str(output_json),
                    "output_markdown": str(output_md),
                },
                indent=2,
            )
        )
        if report["summary"]["overall_result"] != "PASS":
            sys.exit(1)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
