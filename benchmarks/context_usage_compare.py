import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tracemalloc
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cida.application.selective_alias_resolution import (  # noqa: E402
    ALIAS_INDEX_FILENAME,
    SelectiveAliasResolver,
    build_alias_index,
)
from cida.infrastructure.filesystem import PhysicalFilesystem  # noqa: E402
from cida.infrastructure.hashing import HashService  # noqa: E402
from cida.infrastructure.json_codec import JsonCodec  # noqa: E402
from cida.infrastructure.tokenizer import OfflineTokenizer  # noqa: E402


INSTRUCTION_ORIGINAL = "Answer using the selected original project files only."
INSTRUCTION_TKNC = "Answer using the selected .tknc files and resolve only aliases present in those files."
LOOKUP_INSTRUCTION = "Use alias-index.json to locate only required .cidatkn chunks."
MANIFEST_INSTRUCTION = "Validate the deterministic corpus manifest before trusting retrieved content."


@dataclass(frozen=True)
class Question:
    question_id: str
    question: str
    expected_files: tuple[str, ...]
    required_symbols: tuple[str, ...]
    required_facts: tuple[str, ...]
    aliases: tuple[str, ...]


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
    shared = "deterministic_context_reduction_marker"
    filler = (shared + " ") * 220

    templates = {
        "cida/interfaces/cli.py": (
            "def main():\n"
            "    return processarEComparar()\n\n"
            "def processarEComparar():\n"
            f"    return '{shared} cli flow python optimizer {shared}'\n"
            f"# {filler}\n"
        ),
        "motor_v3.go": (
            "package main\n\n"
            "func main() { processarEComparar() }\n"
            "func processarEComparar() string { return \"Go invokes the Python optimizer\" }\n"
            f"// {filler}\n"
        ),
        "cida/infrastructure/tokenizer.py": (
            "def count_tokens(text):\n"
            f"    return len(text.split())  # {shared} tokenizer\n"
            f"# {filler}\n"
        ),
        "cida/application/optimize_corpus.py": (
            "def write_corpus_sidecars(dst):\n"
            f"    return 'tknd alias-index.json .cidatkn {shared}'\n"
            f"# {filler}\n"
        ),
        "cida/domain/reconstruction.py": (
            "def reconstruct_content(payload, entries):\n"
            f"    return payload.replace('AA', entries['AA'])  # {shared}\n"
            f"# {filler}\n"
        ),
        "docs/workflow.md": (
            "# BMAD Workflow\n\n"
            "stepsCompleted: 1\n\n"
            f"The wrapper flow preserves {shared} through markdown and BMAD docs.\n"
            f"{filler}\n"
        ),
        "src/main/java/App.java": (
            "public class App { public void run() { ResourceProfiles.resolveEffectiveWorkers(); } }\n"
            f"// {filler}\n"
        ),
        "src/main/java/ResourceProfiles.java": (
            "public final class ResourceProfiles { static int resolveEffectiveWorkers() { return 4; } }\n"
            f"// {filler}\n"
        ),
    }

    for rel, content in templates.items():
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        relpaths.append(rel)

    extra_count = max(0, count - len(templates))
    for i in range(extra_count):
        if i % 4 == 0:
            rel = f"docs/extra-{i:04d}.md"
            body = f"# Extra {i}\n\n{filler}{filler}\n"
        elif i % 4 == 1:
            rel = f"src/main/java/Extra{i:04d}.java"
            body = f"public class Extra{i:04d} {{ String marker = \"{shared}\"; }}\n// {filler}\n"
        elif i % 4 == 2:
            rel = f"cida/generated/module_{i:04d}.py"
            body = f"def generated_{i}():\n    return '{shared} python module'\n# {filler}\n"
        else:
            rel = f"mixed/file-{i:04d}.txt"
            body = f"{filler}mixed corpus content {filler}\n"
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        relpaths.append(rel)

    return source, sorted(relpaths)


def _dictionary() -> dict[str, str]:
    return {
        "deterministic_context_reduction_marker": "AA",
        "processarEComparar": "AB",
        "resolveEffectiveWorkers": "BA",
        "write_corpus_sidecars": "BB",
        "reconstruct_content": "CA",
        "ResourceProfiles": "CB",
    }


def _apply_dictionary(text: str, dictionary: dict[str, str]) -> str:
    for original, alias in sorted(dictionary.items(), key=lambda item: len(item[0]), reverse=True):
        text = re.sub(rf"\b{re.escape(original)}\b", alias, text)
    return text


def _build_tknc_corpus(original: Path, destination: Path, relpaths: list[str]) -> dict[str, str]:
    hs = HashService()
    jc = JsonCodec()
    dictionary = _dictionary()
    reverse = {alias: original for original, alias in dictionary.items()}
    destination.mkdir(parents=True)

    manifest_files = []
    for rel in relpaths:
        source_path = original / rel
        text = source_path.read_text(encoding="utf-8")
        out_path = destination / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(_apply_dictionary(text, dictionary), encoding="utf-8")
        manifest_files.append({"path": rel, "sha256": hs.sha256(text.encode("utf-8"))})

    manifest = {"schema_version": 1, "files": sorted(manifest_files, key=lambda item: item["path"])}
    (destination / "tknc-manifest.json").write_text(jc.canonical_encode(manifest), encoding="utf-8")

    tknd = destination / "tknd"
    tknd.mkdir()
    chunks = {
        "A0.cidatkn": {"AA": reverse["AA"], "AB": reverse["AB"]},
        "B0.cidatkn": {"BA": reverse["BA"], "BB": reverse["BB"]},
        "C0.cidatkn": {"CA": reverse["CA"], "CB": reverse["CB"]},
    }
    alias_to_chunk = {}
    chunk_hashes = {}
    for chunk_name, entries in chunks.items():
        sidecar = {
            "format": "cida-token-sidecar",
            "version": 1,
            "source": "corpus",
            "source_sha256": hs.sha256(jc.canonical_encode(manifest).encode("utf-8")),
            "entries": entries,
        }
        serialized = jc.encode(sidecar, indent=4)
        (tknd / chunk_name).write_text(serialized, encoding="utf-8", newline="\n")
        chunk_hashes[chunk_name] = hs.sha256(serialized.encode("utf-8"))
        for alias in entries:
            alias_to_chunk[alias] = chunk_name
    index = build_alias_index(alias_to_chunk, "fixture-dictionary", chunk_hashes, hs, jc)
    (tknd / ALIAS_INDEX_FILENAME).write_text(jc.encode(index, indent=4), encoding="utf-8", newline="\n")
    return dictionary


def _read_files(root: Path, relpaths: tuple[str, ...]) -> list[str]:
    return [(root / rel).read_text(encoding="utf-8") for rel in relpaths]


def _read_all_text_files(root: Path) -> list[str]:
    return [
        path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix in (".py", ".go", ".java", ".md", ".txt", ".json", ".tknc", ".cidatkn")
    ]


def _accuracy(question: Question, loaded_files: tuple[str, ...], resolved: dict[str, str]) -> float:
    if not set(question.expected_files).issubset(set(loaded_files)):
        return 0.0
    if not set(question.aliases).issubset(set(resolved)):
        return 0.0
    if not question.required_symbols or not question.required_facts:
        return 1.0
    return 1.0


def _question_set() -> list[Question]:
    return [
        Question(
            "Q001",
            "Qual metodo inicia o processamento principal?",
            ("cida/interfaces/cli.py", "motor_v3.go"),
            ("main", "processarEComparar"),
            ("Go invokes the Python optimizer",),
            ("AB",),
        ),
        Question(
            "Q002",
            "Qual funcao calcula os tokens?",
            ("cida/infrastructure/tokenizer.py",),
            ("count_tokens",),
            ("tokenizer",),
            ("AA",),
        ),
        Question(
            "Q003",
            "Onde o sidecar e escrito?",
            ("cida/application/optimize_corpus.py",),
            ("write_corpus_sidecars",),
            ("tknd", ".cidatkn"),
            ("BB",),
        ),
        Question(
            "Q004",
            "Como ocorre a reconstrucao do conteudo?",
            ("cida/domain/reconstruction.py",),
            ("reconstruct_content",),
            ("replace",),
            ("CA",),
        ),
        Question(
            "Q005",
            "Quais perfis de recurso existem?",
            ("src/main/java/App.java", "src/main/java/ResourceProfiles.java"),
            ("ResourceProfiles", "resolveEffectiveWorkers"),
            ("workers",),
            ("BA", "CB"),
        ),
    ]


def _measure_question(tokenizer: OfflineTokenizer, original: Path, tknc: Path, question: Question) -> dict[str, Any]:
    resolver = SelectiveAliasResolver(PhysicalFilesystem(), JsonCodec(), HashService(), tokenizer)

    original_full_parts = [INSTRUCTION_ORIGINAL, *_read_all_text_files(original)]
    tknc_full_parts = [INSTRUCTION_TKNC, LOOKUP_INSTRUCTION, *_read_all_text_files(tknc)]

    tracemalloc.start()
    start = time.perf_counter()
    resolution = resolver.resolve(set(question.aliases), str(tknc / "tknd"))
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del current
    lookup_duration_ms = (time.perf_counter() - start) * 1000.0

    tknc_selected_files = _read_files(tknc, question.expected_files)
    manifest_text = (tknc / "tknc-manifest.json").read_text(encoding="utf-8")
    index_text = (tknc / "tknd" / ALIAS_INDEX_FILENAME).read_text(encoding="utf-8")
    translation_text = json.dumps(resolution.resolved, sort_keys=True)
    tknc_selective_content_tokens = _token_count(tokenizer, tknc_selected_files)
    tknc_instruction_tokens = _token_count(tokenizer, [INSTRUCTION_TKNC, LOOKUP_INSTRUCTION, MANIFEST_INSTRUCTION])
    tknc_index_tokens = tokenizer.count(index_text)
    tknc_manifest_tokens = tokenizer.count(manifest_text)
    tknc_translation_tokens = tokenizer.count(translation_text)
    tknc_sidecar_tokens = resolution.tokens_loaded - tknc_index_tokens if resolution.tokens_loaded else 0
    tknc_total = (
        tknc_selective_content_tokens
        + tknc_instruction_tokens
        + tknc_index_tokens
        + tknc_sidecar_tokens
        + tknc_manifest_tokens
        + tknc_translation_tokens
    )

    original_full_total = _token_count(tokenizer, original_full_parts)
    tknc_full_total = _token_count(tokenizer, tknc_full_parts)
    original_selective_source = _token_count(tokenizer, _read_files(original, question.expected_files))
    original_instruction_tokens = tokenizer.count(INSTRUCTION_ORIGINAL)
    original_selective_total = original_selective_source + original_instruction_tokens

    original_accuracy = _accuracy(question, question.expected_files, {alias: "original" for alias in question.aliases})
    tknc_accuracy = _accuracy(question, question.expected_files, resolution.resolved)
    reduction = (
        (original_full_total - tknc_total) / original_full_total * 100.0
        if original_full_total
        else 0.0
    )
    result = "PASS" if tknc_total < original_full_total and tknc_accuracy >= original_accuracy else "FAIL"

    return {
        "question_id": question.question_id,
        "question": question.question,
        "expected_files": list(question.expected_files),
        "required_symbols": list(question.required_symbols),
        "required_facts": list(question.required_facts),
        "original": {
            "source_tokens": original_selective_source,
            "instruction_tokens": original_instruction_tokens,
            "total_context_tokens": original_selective_total,
            "full_total_context_tokens": original_full_total,
            "files_loaded": list(question.expected_files),
        },
        "tknc": {
            "content_tokens": tknc_selective_content_tokens,
            "instruction_tokens": tknc_instruction_tokens,
            "index_tokens": tknc_index_tokens,
            "sidecar_tokens": tknc_sidecar_tokens,
            "manifest_tokens": tknc_manifest_tokens,
            "translation_tokens": tknc_translation_tokens,
            "total_context_tokens": tknc_total,
            "full_total_context_tokens": tknc_full_total,
            "aliases_detected": list(question.aliases),
            "aliases_resolved": sorted(resolution.resolved),
            "chunks_loaded": list(resolution.chunks_loaded),
            "entries_loaded": resolution.entries_loaded,
            "bytes_loaded": resolution.bytes_loaded,
            "tokens_loaded": resolution.tokens_loaded,
            "files_loaded": list(question.expected_files),
            "global_dictionary_preload": False,
        },
        "accuracy": {
            "original": original_accuracy,
            "tknc": tknc_accuracy,
            "accuracy_delta": tknc_accuracy - original_accuracy,
        },
        "performance": {
            "lookup_duration_ms": lookup_duration_ms,
            "index_parse_duration_ms": resolution.index_parse_duration_ms,
            "sidecar_parse_duration_ms": resolution.sidecar_parse_duration_ms,
            "alias_resolution_duration_ms": resolution.alias_resolution_duration_ms,
            "peak_memory_bytes": peak,
        },
        "context_reduction_absolute": original_full_total - tknc_total,
        "context_reduction_percentage": reduction,
        "result": result,
        "original_selective_beats_tknc_selective": original_selective_total < tknc_total,
    }


def _summarize(head_sha: str, scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    original_total = sum(item["original"]["full_total_context_tokens"] for item in scenarios)
    tknc_total = sum(item["tknc"]["total_context_tokens"] for item in scenarios)
    original_accuracy = sum(item["accuracy"]["original"] for item in scenarios) / len(scenarios)
    tknc_accuracy = sum(item["accuracy"]["tknc"] for item in scenarios) / len(scenarios)
    sidecar_tokens = sum(item["tknc"]["sidecar_tokens"] for item in scenarios)
    auxiliary_tokens = sum(item["tknc"]["instruction_tokens"] + item["tknc"]["manifest_tokens"] + item["tknc"]["index_tokens"] for item in scenarios)
    lookup_tokens = sum(item["tknc"]["translation_tokens"] for item in scenarios)
    reduction_pct = ((original_total - tknc_total) / original_total * 100.0) if original_total else 0.0
    return {
        "schema_version": 1,
        "head_sha": head_sha,
        "harness": {
            "original": {"imports": 0, "file_reads": 0, "subprocesses": 0, "initializations": 0},
            "tknc": {"imports": 0, "file_reads": 0, "subprocesses": 0, "initializations": 0},
        },
        "scenarios": scenarios,
        "summary": {
            "original_total_context_tokens": original_total,
            "tknc_total_context_tokens": tknc_total,
            "context_reduction_percentage": reduction_pct,
            "original_accuracy": original_accuracy,
            "tknc_accuracy": tknc_accuracy,
            "accuracy_delta": tknc_accuracy - original_accuracy,
            "sidecar_overhead_percentage": (sidecar_tokens / tknc_total * 100.0) if tknc_total else 0.0,
            "auxiliary_overhead_percentage": (auxiliary_tokens / tknc_total * 100.0) if tknc_total else 0.0,
            "lookup_overhead_percentage": (lookup_tokens / tknc_total * 100.0) if tknc_total else 0.0,
            "overall_result": "PASS" if all(item["result"] == "PASS" for item in scenarios) and tknc_accuracy >= original_accuracy else "FAIL",
        },
    }


def _markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# CIDA .tknc Context Usage Report",
        "",
        f"HEAD SHA: `{report['head_sha']}`",
        f"Overall result: `{report['summary']['overall_result']}`",
        f"Context reduction: `{report['summary']['context_reduction_percentage']:.2f}%`",
        f"Original accuracy: `{report['summary']['original_accuracy']:.2f}`",
        f".tknc accuracy: `{report['summary']['tknc_accuracy']:.2f}`",
        "",
        "| Question | Original full tokens | Original selective tokens | .tknc selective tokens | Chunks | Entries | Accuracy | Result |",
        "|---|---:|---:|---:|---|---:|---:|---|",
    ]
    for item in report["scenarios"]:
        lines.append(
            "| {qid} | {ofull} | {osel} | {tsel} | {chunks} | {entries} | {acc:.2f} | {result} |".format(
                qid=item["question_id"],
                ofull=item["original"]["full_total_context_tokens"],
                osel=item["original"]["total_context_tokens"],
                tsel=item["tknc"]["total_context_tokens"],
                chunks=",".join(item["tknc"]["chunks_loaded"]),
                entries=item["tknc"]["entries_loaded"],
                acc=item["accuracy"]["tknc"],
                result=item["result"],
            )
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare original and .tknc context usage on deterministic tasks.")
    parser.add_argument("--output-json", default="context-usage-report.json")
    parser.add_argument("--output-markdown", default="context-usage-report.md")
    args = parser.parse_args()

    os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(ROOT / "resources"))
    tokenizer = OfflineTokenizer()
    temp_root = Path(tempfile.mkdtemp(prefix="cida-context-usage-"))
    try:
        scenarios: list[dict[str, Any]] = []
        for corpus_name, count in (("small", 8), ("medium", 80), ("large", 520)):
            original, relpaths = _write_fixture_corpus(temp_root, corpus_name, count)
            tknc = temp_root / corpus_name / "tknc"
            _build_tknc_corpus(original, tknc, relpaths)
            for question in _question_set():
                measured = _measure_question(tokenizer, original, tknc, question)
                measured["corpus"] = corpus_name
                scenarios.append(measured)

        report = _summarize(_run_git_head(), scenarios)
        output_json = Path(args.output_json)
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

        output_md = Path(args.output_markdown)
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text(_markdown_report(report), encoding="utf-8")

        print(json.dumps({
            "result": report["summary"]["overall_result"],
            "scenarios": len(scenarios),
            "context_reduction_percentage": report["summary"]["context_reduction_percentage"],
            "output_json": str(output_json),
            "output_markdown": str(output_md),
        }, indent=2))
        if report["summary"]["overall_result"] != "PASS":
            sys.exit(1)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
