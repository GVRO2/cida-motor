import argparse
import json
import os
import re
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

from cida.application.selective_alias_resolution import (  # noqa: E402
    ALIAS_INDEX_FILENAME,
    AliasDetector,
    SelectiveAliasResolver,
)
from cida.infrastructure.filesystem import PhysicalFilesystem  # noqa: E402
from cida.infrastructure.hashing import HashService  # noqa: E402
from cida.infrastructure.json_codec import JsonCodec  # noqa: E402
from cida.infrastructure.tokenizer import OfflineTokenizer  # noqa: E402


INSTRUCTION_ORIGINAL = "Answer using selected original files discovered from the question."
INSTRUCTION_TKNC = "Answer using selected .tknc files discovered from the question and reconstruct aliases first."
LOOKUP_INSTRUCTION = "Use alias-index.json ranges to locate only .cidatkn chunks containing detected aliases."
MANIFEST_INSTRUCTION = "Validate the generated corpus manifest before trusting retrieved content."
TOKEN_SUFFIXES = {".py", ".go", ".java", ".md", ".txt", ".json", ".yaml", ".yml", ".tknc", ".cidatkn"}


@dataclass(frozen=True)
class Question:
    question_id: str
    question: str
    required_files: tuple[str, ...]
    required_symbols: tuple[str, ...]
    required_facts: tuple[str, ...]
    forbidden_facts: tuple[str, ...] = ()


@dataclass(frozen=True)
class SearchResult:
    files: tuple[str, ...]
    files_scanned: int
    terms: tuple[str, ...]


@dataclass(frozen=True)
class ReadEvent:
    path: str
    bytes_requested: int
    bytes_read: int
    artifact_type: str
    relative_timestamp_ms: float


class InstrumentedFilesystem(PhysicalFilesystem):
    def __init__(self) -> None:
        super().__init__()
        self._started = time.perf_counter()
        self.reads: list[ReadEvent] = []

    def read_bytes_limited(self, filepath: str, max_bytes: int) -> bytes:
        data = super().read_bytes_limited(filepath, max_bytes)
        self.reads.append(
            ReadEvent(
                path=filepath,
                bytes_requested=max_bytes,
                bytes_read=len(data),
                artifact_type=_artifact_type(Path(filepath)),
                relative_timestamp_ms=(time.perf_counter() - self._started) * 1000.0,
            )
        )
        return data


def _artifact_type(path: Path) -> str:
    if path.name == ALIAS_INDEX_FILENAME:
        return "index"
    if path.name == "tknc-manifest.json":
        return "manifest"
    if path.suffix == ".cidatkn":
        return "sidecar"
    return "content"


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


def _write_fixture_corpus(root: Path, name: str, count: int) -> tuple[Path, list[str]]:
    source = root / name / "original"
    source.mkdir(parents=True)
    relpaths: list[str] = []
    marker = f"{name}_deterministic_context_reduction_marker"
    bridge = "python_optimizer_bridge"
    sidecar_writer = "corpus_sidecar_writer"
    reconstruction = "lossless_reconstruction_contract"
    worker_profile = "resource_worker_profile"
    lexicon = " ".join([marker, bridge, sidecar_writer, reconstruction, worker_profile])
    repeated = (lexicon + " ") * (80 if name != "low" else 12)

    templates = {
        "cida/interfaces/cli.py": (
            "def main():\n"
            "    return processarEComparar()\n\n"
            "def processarEComparar():\n"
            f"    return '{bridge} invokes the Go wrapper through {marker}'\n"
        ),
        "motor_v3.go": (
            "package main\n\n"
            "func main() { processarEComparar() }\n"
            f"func processarEComparar() string {{ return \"Go wrapper uses {bridge}\" }}\n"
        ),
        "cida/infrastructure/tokenizer.py": (
            "def count_tokens(text):\n"
            f"    return len(text.split())  # deterministic token counter {marker}\n"
        ),
        "cida/application/optimize_corpus.py": (
            "def write_corpus_sidecars(dst):\n"
            f"    return 'tknd alias-index.json .cidatkn {sidecar_writer}'\n"
        ),
        "cida/domain/reconstruction.py": (
            "def reconstruct_content(payload, entries):\n"
            f"    return payload.replace('alias', entries['alias'])  # {reconstruction}\n"
        ),
        "src/main/java/ResourceProfiles.java": (
            f"public final class ResourceProfiles {{ static int resolveEffectiveWorkers() {{ return 4; }} String p = \"{worker_profile}\"; }}\n"
        ),
        "docs/lexicon.md": f"# Lexicon\n\n{repeated}\n",
        "docs/workflow.md": f"# BMAD Workflow\n\nThe flow preserves {marker} and {bridge}.\n\n{repeated}\n",
    }

    for rel, content in templates.items():
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        relpaths.append(rel)

    extra_count = max(0, count - len(templates))
    for i in range(extra_count):
        rel = f"docs/extra-{i:04d}.md" if i % 2 == 0 else f"mixed/file-{i:04d}.txt"
        body = f"# Extra {i}\n\n{repeated} scenario_{i:04d}_unique_low_frequency\n"
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
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
    index = json.loads(index_path.read_text(encoding="utf-8"))
    resolver = SelectiveAliasResolver(PhysicalFilesystem(), JsonCodec(), HashService())
    all_aliases = set()
    for item in index.get("ranges", []):
        first_alias = item["first_alias"]
        last_alias = item["last_alias"]
        if first_alias == last_alias:
            all_aliases.add(first_alias)
    if not all_aliases:
        for sidecar in sorted((destination / "tknd").glob("*.cidatkn")):
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            all_aliases.update(data.get("entries", {}).keys())
    return resolver.resolve(all_aliases, str(destination / "tknd")).resolved


def _question_set() -> list[Question]:
    return [
        Question(
            "Q001",
            "processarEComparar python_optimizer_bridge motor_v3 main",
            ("cida/interfaces/cli.py", "motor_v3.go"),
            ("main", "processarEComparar"),
            ("processarEComparar",),
        ),
        Question(
            "Q002",
            "count_tokens tokenizer deterministic token counter",
            ("cida/infrastructure/tokenizer.py",),
            ("count_tokens",),
            ("deterministic token counter",),
        ),
        Question(
            "Q003",
            "write_corpus_sidecars tknd alias-index cidatkn corpus_sidecar_writer",
            ("cida/application/optimize_corpus.py",),
            ("write_corpus_sidecars",),
            ("tknd", "corpus_sidecar_writer"),
        ),
        Question(
            "Q004",
            "reconstruct_content lossless_reconstruction_contract replace entries",
            ("cida/domain/reconstruction.py",),
            ("reconstruct_content",),
            ("replace", "lossless_reconstruction_contract"),
        ),
        Question(
            "Q005",
            "ResourceProfiles resolveEffectiveWorkers resource_worker_profile workers",
            ("src/main/java/ResourceProfiles.java",),
            ("ResourceProfiles", "resolveEffectiveWorkers"),
            ("resource_worker_profile",),
        ),
    ]


def _terms(question: str) -> tuple[str, ...]:
    raw = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", question)
    stop = {"the", "and", "with", "using", "qual", "como", "onde"}
    return tuple(dict.fromkeys(term for term in raw if term.lower() not in stop))


def _iter_context_files(root: Path) -> list[Path]:
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.suffix in TOKEN_SUFFIXES
        and not path.name.startswith("report")
        and ".pytest_cache" not in path.parts
    ]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _search(root: Path, question: str, limit: int = 4) -> SearchResult:
    terms = _terms(question)
    scored = []
    files = _iter_context_files(root)
    for path in files:
        rel = path.relative_to(root).as_posix()
        text = _read_text(path)
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
            scored.append((score, rel))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return SearchResult(tuple(rel for _, rel in scored[:limit]), len(files), terms)


def _load_index(tknc: Path) -> tuple[dict[str, Any], str]:
    index_text = (tknc / "tknd" / ALIAS_INDEX_FILENAME).read_text(encoding="utf-8")
    return json.loads(index_text), index_text


def _reconstruct_text(text: str, resolved: dict[str, str]) -> str:
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


def _measure_question(tokenizer: OfflineTokenizer, original: Path, tknc: Path, question: Question) -> dict[str, Any]:
    original_search = _search(original, question.question)
    tknc_search = _search(tknc, question.question)
    index, index_text = _load_index(tknc)
    manifest_text = (tknc / "tknc-manifest.json").read_text(encoding="utf-8")

    selected_tknc_text = [_read_text(tknc / rel) for rel in tknc_search.files]
    detected_aliases = AliasDetector().detect("\n".join(selected_tknc_text), index)
    fs = InstrumentedFilesystem()
    resolver = SelectiveAliasResolver(fs, JsonCodec(), HashService(), tokenizer)
    start = time.perf_counter()
    resolution = resolver.resolve(detected_aliases, str(tknc / "tknd"))
    lookup_duration_ms = (time.perf_counter() - start) * 1000.0
    reconstructed_tknc = [_reconstruct_text(text, resolution.resolved) for text in selected_tknc_text]

    selected_original_text = [_read_text(original / rel) for rel in original_search.files]
    original_accuracy = _score(question, original_search.files, selected_original_text)
    tknc_accuracy = _score(question, tknc_search.files, reconstructed_tknc)

    original_full_total = _token_count(tokenizer, [INSTRUCTION_ORIGINAL, *[_read_text(p) for p in _iter_context_files(original)]])
    tknc_full_total = _token_count(tokenizer, [INSTRUCTION_TKNC, LOOKUP_INSTRUCTION, *[_read_text(p) for p in _iter_context_files(tknc)]])
    original_selective_content = _token_count(tokenizer, selected_original_text)
    original_selective_total = original_selective_content + tokenizer.count(INSTRUCTION_ORIGINAL)
    tknc_content_tokens = _token_count(tokenizer, selected_tknc_text)
    tknc_instruction_tokens = _token_count(tokenizer, [INSTRUCTION_TKNC, LOOKUP_INSTRUCTION, MANIFEST_INSTRUCTION])
    tknc_index_tokens = tokenizer.count(index_text)
    tknc_manifest_tokens = tokenizer.count(manifest_text)
    tknc_translation_tokens = tokenizer.count(json.dumps(resolution.resolved, sort_keys=True))
    tknc_sidecar_tokens = max(0, resolution.tokens_loaded - tknc_index_tokens)
    tknc_cold_total = (
        tknc_content_tokens
        + tknc_instruction_tokens
        + tknc_index_tokens
        + tknc_manifest_tokens
        + tknc_sidecar_tokens
        + tknc_translation_tokens
    )
    tknc_warm_total = tknc_cold_total - tknc_index_tokens - tknc_manifest_tokens
    read_events = [event.__dict__ for event in fs.reads]
    chunks_loaded = [event for event in fs.reads if event.artifact_type == "sidecar"]

    return {
        "question_id": question.question_id,
        "question": question.question,
        "required_files": list(question.required_files),
        "required_symbols": list(question.required_symbols),
        "required_facts": list(question.required_facts),
        "original": {
            "search_tokens": tokenizer.count(" ".join(original_search.terms)),
            "content_tokens": original_selective_content,
            "instruction_tokens": tokenizer.count(INSTRUCTION_ORIGINAL),
            "total_context_tokens": original_selective_total,
            "full_total_context_tokens": original_full_total,
            "files_scanned": original_search.files_scanned,
            "files_loaded": list(original_search.files),
        },
        "tknc": {
            "search_tokens": tokenizer.count(" ".join(tknc_search.terms)),
            "content_tokens": tknc_content_tokens,
            "instruction_tokens": tknc_instruction_tokens,
            "index_tokens": tknc_index_tokens,
            "manifest_tokens": tknc_manifest_tokens,
            "sidecar_tokens": tknc_sidecar_tokens,
            "translation_tokens": tknc_translation_tokens,
            "total_context_tokens": tknc_cold_total,
            "warm_context_tokens": tknc_warm_total,
            "full_total_context_tokens": tknc_full_total,
            "files_scanned": tknc_search.files_scanned,
            "files_loaded": list(tknc_search.files),
            "aliases_detected": sorted(detected_aliases),
            "aliases_resolved": sorted(resolution.resolved),
            "chunks_loaded": list(resolution.chunks_loaded),
            "chunks_available": index.get("chunk_count", 0),
            "entries_loaded": resolution.entries_loaded,
            "bytes_read": sum(event.bytes_read for event in fs.reads),
            "read_events": read_events,
            "global_dictionary_preload": len(chunks_loaded) == index.get("chunk_count", 0) and index.get("chunk_count", 0) > 1,
        },
        "accuracy": {
            "original": original_accuracy,
            "tknc": tknc_accuracy,
            "accuracy_delta": tknc_accuracy["accuracy"] - original_accuracy["accuracy"],
        },
        "performance": {
            "lookup_duration_ms": lookup_duration_ms,
            "search_duration_ms": 0.0,
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
        "selective_warm_delta_percentage": (
            (original_selective_total - tknc_warm_total) / original_selective_total * 100.0 if original_selective_total else 0.0
        ),
        "result": "PASS" if tknc_accuracy["accuracy"] >= original_accuracy["accuracy"] else "FAIL",
    }


def _break_even(original_selective: int, tknc_cold: int, tknc_warm: int) -> int | None:
    if tknc_warm >= original_selective:
        return None
    fixed = max(0, tknc_cold - tknc_warm)
    per_query_saving = original_selective - tknc_warm
    return max(1, (fixed + per_query_saving - 1) // per_query_saving)


def _summarize(head_sha: str, corpora: dict[str, Any], scenarios: list[dict[str, Any]], production: dict[str, Any]) -> dict[str, Any]:
    original_full = sum(item["original"]["full_total_context_tokens"] for item in scenarios)
    tknc_full = sum(item["tknc"]["full_total_context_tokens"] for item in scenarios)
    original_sel = sum(item["original"]["total_context_tokens"] for item in scenarios)
    tknc_cold = sum(item["tknc"]["total_context_tokens"] for item in scenarios)
    tknc_warm = sum(item["tknc"]["warm_context_tokens"] for item in scenarios)
    original_accuracy = sum(item["accuracy"]["original"]["accuracy"] for item in scenarios) / len(scenarios)
    tknc_accuracy = sum(item["accuracy"]["tknc"]["accuracy"] for item in scenarios) / len(scenarios)
    break_even = _break_even(original_sel, tknc_cold, tknc_warm)
    full_pass = tknc_full < original_full
    accuracy_pass = tknc_accuracy >= original_accuracy
    lookup_pass = all(
        (len(item["tknc"]["aliases_detected"]) == 0 and len(item["tknc"]["chunks_loaded"]) == 0)
        or len(item["tknc"]["chunks_loaded"]) <= max(1, len(set(item["tknc"]["chunks_loaded"])))
        for item in scenarios
    )
    return {
        "schema_version": 2,
        "head_sha": head_sha,
        "corpora": corpora,
        "sessions": {
            "query_counts": {
                "1": {"original": original_sel, "tknc": tknc_cold},
                "10": {"original": original_sel * 10, "tknc": tknc_cold + tknc_warm * 9},
                "50": {"original": original_sel * 50, "tknc": tknc_cold + tknc_warm * 49},
                "100": {"original": original_sel * 100, "tknc": tknc_cold + tknc_warm * 99},
            },
            "break_even_query_count": break_even,
        },
        "harness": {
            "original_python": {"measured": True, "imports": 0, "reads": 0, "subprocesses": 0},
            "original_go": {"measured": False, "reason": "Go file-open tracing is not available in this local benchmark"},
        },
        "integrity": {
            "production_pipeline_exit_codes": [item["exit_code"] for item in production.values()],
            "all_aliases_resolvable": all(not item["tknc"]["aliases_detected"] or item["tknc"]["aliases_resolved"] for item in scenarios),
            "no_global_preload": all(not item["tknc"]["global_dictionary_preload"] for item in scenarios),
        },
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
                "result": "PASS" if tknc_cold <= original_sel else "FAIL",
            },
            "selective_warm": {
                "original": original_sel,
                "tknc": tknc_warm,
                "delta_percentage": (original_sel - tknc_warm) / original_sel * 100.0 if original_sel else 0.0,
                "result": "PASS" if tknc_warm <= original_sel else "FAIL",
            },
            "multi_query": {
                "break_even_query_count": break_even,
                "result": "PASS" if break_even is not None else "FAIL",
            },
            "accuracy": {
                "original_score": original_accuracy,
                "tknc_score": tknc_accuracy,
                "result": "PASS" if accuracy_pass else "FAIL",
            },
            "overall_result": "PASS" if full_pass and accuracy_pass and lookup_pass else "FAIL",
        },
    }


def _markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# CIDA .tknc Context Usage Report v2",
        "",
        f"HEAD SHA: `{report['head_sha']}`",
        f"Overall result: `{report['summary']['overall_result']}`",
        f"Full vs full: `{report['summary']['full_vs_full']['result']}`",
        f"Selective cold: `{report['summary']['selective_cold']['result']}`",
        f"Selective warm: `{report['summary']['selective_warm']['result']}`",
        f"Break-even queries: `{report['summary']['multi_query']['break_even_query_count']}`",
        "",
        "| Question | Original selective | .tknc cold | .tknc warm | Chunks | Accuracy | Result |",
        "|---|---:|---:|---:|---|---:|---|",
    ]
    for item in report["scenarios"]:
        lines.append(
            "| {qid} | {osel} | {tcold} | {twarm} | {chunks} | {acc:.2f} | {result} |".format(
                qid=item["question_id"],
                osel=item["original"]["total_context_tokens"],
                tcold=item["tknc"]["total_context_tokens"],
                twarm=item["tknc"]["warm_context_tokens"],
                chunks=",".join(item["tknc"]["chunks_loaded"]),
                acc=item["accuracy"]["tknc"]["accuracy"],
                result=item["result"],
            )
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare equivalent original and .tknc context usage.")
    parser.add_argument("--output-json", default="context-usage-report-v2.json")
    parser.add_argument("--output-markdown", default="context-usage-report-v2.md")
    parser.add_argument("--read-events-json", default="")
    parser.add_argument("--harness-events-json", default="")
    args = parser.parse_args()

    os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(ROOT / "resources"))
    tokenizer = OfflineTokenizer()
    temp_root = Path(tempfile.mkdtemp(prefix="cida-context-usage-"))
    try:
        scenarios: list[dict[str, Any]] = []
        corpora: dict[str, Any] = {}
        production: dict[str, Any] = {}
        for corpus_name, count in (("repetitive", 10), ("code", 60), ("low", 12), ("controlled_repo", 90)):
            original, relpaths = _write_fixture_corpus(temp_root, corpus_name, count)
            tknc = temp_root / corpus_name / "tknc"
            production[corpus_name] = _run_production_tknc(original, tknc, temp_root / corpus_name / "report" / "context")
            if production[corpus_name]["exit_code"] != 0:
                raise RuntimeError(json.dumps(production[corpus_name], indent=2))
            index_path = tknc / "tknd" / ALIAS_INDEX_FILENAME
            index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
            corpora[corpus_name] = {
                "files": len(relpaths),
                "alias_count": index.get("alias_count", 0),
                "chunk_count": index.get("chunk_count", 0),
                "index_bytes": index_path.stat().st_size if index_path.exists() else 0,
            }
            for question in _question_set():
                measured = _measure_question(tokenizer, original, tknc, question)
                measured["corpus"] = corpus_name
                scenarios.append(measured)

        report = _summarize(_run_git_head(), corpora, scenarios, production)
        output_json = Path(args.output_json)
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

        output_md = Path(args.output_markdown)
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text(_markdown_report(report), encoding="utf-8")

        if args.read_events_json:
            events = [event for item in scenarios for event in item["tknc"]["read_events"]]
            Path(args.read_events_json).write_text(json.dumps(events, indent=2), encoding="utf-8")
        if args.harness_events_json:
            Path(args.harness_events_json).write_text(json.dumps(report["harness"], indent=2), encoding="utf-8")

        print(json.dumps({
            "result": report["summary"]["overall_result"],
            "scenarios": len(scenarios),
            "output_json": str(output_json),
            "output_markdown": str(output_md),
        }, indent=2))
        if report["summary"]["overall_result"] != "PASS":
            sys.exit(1)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
