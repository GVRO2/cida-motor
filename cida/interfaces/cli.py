import os
import sys
import argparse
import time
import re
from dataclasses import dataclass, field
from typing import Any, List
from cida.domain.errors import (
    CidaError, SourcePathError
)
from cida.domain.policies import validate_mode_profile_combination, ValidationLevel, validate_validation_level
from cida.infrastructure.filesystem import PhysicalFilesystem, validate_filesystem_safety
from cida.infrastructure.tokenizer import OfflineTokenizer
from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec
from cida.application.optimize_corpus import CorpusOptimizerUsecase
from cida.application.generate_report import ReportGeneratorUsecase
from cida.domain.processing_context import ProcessingContext
from cida.markdown.protected_regions import ProtectedRegionsManager
from cida.markdown.transforms import (
    remove_html_comments, trim_trailing_whitespace, normalize_newlines,
    table_whitespace, list_compaction, minificar_codigo_para_ia
)



# ── Exit-code categories (severity order: 6 > 5 > 4 > 3 > 2 > 1) ────────────
_EXIT_CODE_SEVERITY = {6: 6, 5: 5, 4: 4, 3: 3, 2: 2, 1: 1}



@dataclass
class FailureRecord:
    filepath: str
    operation: str
    error_type: str
    exit_code: int
    message: str


@dataclass
class FailureAggregator:
    records: List[FailureRecord] = field(default_factory=list)

    def add(self, filepath: str, operation: str, exc: Exception) -> None:
        exit_code = getattr(exc, 'exit_code', 6)
        self.records.append(FailureRecord(
            filepath=filepath,
            operation=operation,
            error_type=type(exc).__name__,
            exit_code=exit_code,
            message=str(exc),
        ))

    @property
    def final_exit_code(self) -> int:
        if not self.records:
            return 0
        return max(_EXIT_CODE_SEVERITY.get(r.exit_code, 6) for r in self.records)

    @property
    def categories(self) -> List[int]:
        return sorted({r.exit_code for r in self.records}, reverse=True)

    def print_summary(self) -> None:
        print(
            f"\nFAILED_FILES={len(self.records)}",
            f"FAILURE_CATEGORIES={self.categories}",
            f"FINAL_EXIT_CODE={self.final_exit_code}",
            sep="\n",
            file=sys.stderr,
        )


class CidaArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write(f"error: {message}\n")
        sys.exit(1)


def _build_processing_context(
    filepath: str,
    src_abs: str,
    requested_profile: str,
    file_repo: PhysicalFilesystem,
    file_opt: Any,
    hash_service: HashService,
    token_counter: OfflineTokenizer,
) -> ProcessingContext:
    source_bytes = file_repo.read_bytes(filepath)
    source_text = source_bytes.decode('utf-8')
    source_sha256 = hash_service.sha256(source_bytes)
    detected_profile = (
        file_opt.detect_profile(filepath, source_text)
        if requested_profile == "auto"
        else requested_profile
    )
    relative_path = (
        file_repo.relpath(filepath, src_abs)
        if os.path.isdir(src_abs)
        else os.path.basename(filepath)
    )
    return ProcessingContext(
        source_path=filepath,
        source_real_path=os.path.normcase(file_repo.abspath(filepath)),
        relative_path=relative_path,
        source_bytes=source_bytes,
        source_text=source_text,
        source_sha256=source_sha256,
        original_tokens=token_counter.count(source_text, content_hash=source_sha256),
        detected_profile=detected_profile,
    )


def _accept_token_reducing_candidate(
    current_text: str,
    current_tokens: int,
    candidate_text: str,
    token_counter: Any,
) -> tuple[str, int, bool]:
    if candidate_text == current_text:
        return current_text, current_tokens, False

    candidate_tokens = token_counter.count(candidate_text)
    if candidate_tokens < current_tokens:
        return candidate_text, candidate_tokens, True
    return current_text, current_tokens, False


def _load_strict_bundle_auditor() -> Any:
    from cida.application.strict_auditing import StrictBundleAuditor
    return StrictBundleAuditor


def _load_semantic_dependencies() -> tuple[type[Any], Any]:
    from cida.markdown.semantic_equivalence import ParsedOriginalDocument, validate_semantics
    return ParsedOriginalDocument, validate_semantics


def _requires_identity_semantic_validation(content: str) -> bool:
    return content.startswith("---")


def counter_main():
    try:
        token_counter = OfflineTokenizer()
        text = sys.stdin.read()
        print(token_counter.count(text))
    except CidaError as ce:
        print(f"Error in token_counter: {ce}", file=sys.stderr)
        sys.exit(ce.exit_code)
    except Exception as e:
        print(f"Unexpected error in token_counter: {e}", file=sys.stderr)
        sys.exit(6)

def translate_main():
    try:
        file_repo = PhysicalFilesystem()
        json_codec = JsonCodec()

        if len(sys.argv) < 2:
            print("Usage: translate.py [--sidecar <file.cidatkn>] [--source <source_file>] [--path <dir>] <alias1> [alias2 ...]", file=sys.stderr)
            sys.exit(1)

        args = sys.argv[1:]
        sidecar_file = None
        source_file = None
        sidecar_dir = None

        if "--sidecar" in args:
            idx = args.index("--sidecar")
            if idx + 1 < len(args):
                sidecar_file = args[idx+1]
                args = args[:idx] + args[idx+2:]
        if "--source" in args:
            idx = args.index("--source")
            if idx + 1 < len(args):
                source_file = args[idx+1]
                args = args[:idx] + args[idx+2:]
        if "--path" in args:
            idx = args.index("--path")
            if idx + 1 < len(args):
                sidecar_dir = args[idx+1]
                args = args[:idx] + args[idx+2:]

        tokens_to_translate = [a for a in args if not a.startswith("-")]

        if not tokens_to_translate:
            print("Usage: translate.py [--sidecar <file.cidatkn>] [--source <source_file>] [--path <dir>] <alias1> [alias2 ...]", file=sys.stderr)
            sys.exit(1)

        mapping = {}

        if sidecar_file:
            if not file_repo.exists(sidecar_file):
                print(f"Error: Sidecar file '{sidecar_file}' not found.", file=sys.stderr)
                sys.exit(5)
            data = json_codec.decode(file_repo.read_text(sidecar_file))
            if isinstance(data, dict) and "entries" in data:
                mapping = data["entries"]
        elif source_file:
            cand1 = source_file + ".cidatkn"
            cand2 = os.path.join(os.path.dirname(source_file), os.path.basename(source_file) + ".cidatkn")
            sc_path = cand1 if file_repo.exists(cand1) else cand2
            if not file_repo.exists(sc_path):
                print(f"Error: Sidecar for source file '{source_file}' not found at '{sc_path}'.", file=sys.stderr)
                sys.exit(5)
            data = json_codec.decode(file_repo.read_text(sc_path))
            if isinstance(data, dict) and "entries" in data:
                mapping = data["entries"]
        else:
            if not sidecar_dir:
                sidecar_dir = os.path.join(os.getcwd(), "sidecar")
                if not file_repo.exists(sidecar_dir) and file_repo.exists(os.path.join(os.getcwd(), "tknd")):
                    sidecar_dir = os.path.join(os.getcwd(), "tknd")

            if not file_repo.exists(sidecar_dir):
                print(f"Error: Sidecar directory '{sidecar_dir}' not found.", file=sys.stderr)
                sys.exit(5)

            for file in sorted(file_repo.list_dir(sidecar_dir)):
                if file.endswith(".cidatkn"):
                    try:
                        data = json_codec.decode(file_repo.read_text(os.path.join(sidecar_dir, file)))
                        if isinstance(data, dict) and "entries" in data:
                            for alias, val in data["entries"].items():
                                if alias in mapping and mapping[alias] != val:
                                    print(f"Error: Alias collision detected for '{alias}' across sidecars without explicit sidecar context.", file=sys.stderr)
                                    sys.exit(1)
                                mapping[alias] = val
                    except Exception as e:
                        if isinstance(e, CidaError):
                            raise
                        print(f"Error reading dictionary {file}: {e}", file=sys.stderr)
                        sys.exit(5)

        results = {}
        for t in tokens_to_translate:
            results[t] = mapping.get(t, "Não encontrado")
        print(results)
    except CidaError as ce:
        sys.exit(ce.exit_code)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(6)

def main():
    try:
        parser = CidaArgumentParser(description="Token-oriented Markdown Minifier for BMAD")
        parser.add_argument("--src", required=True, help="Source directory or file")
        parser.add_argument("--dst", required=True, help="Destination directory")
        parser.add_argument("--mode", default="lossless", choices=["lossless", "semantic"], help="Compression mode")
        parser.add_argument("--profile", default="auto", choices=["auto", "code", "java", "markdown", "bmad"], help="Processing profile")
        parser.add_argument("--dictionary-scope", default="file", choices=["none", "file", "corpus"], help="Dictionary scope")
        parser.add_argument("--fail-on-inflation", action="store_true", help="Fail if any file has token count inflation")
        parser.add_argument("--continue-on-error", action="store_true", help="Continue processing on file errors")
        parser.add_argument("--no-cache", action="store_true", help="Disable token count and document memoization cache")
        parser.add_argument("--durable-writes", action="store_true", help="Perform durable fsync writes")
        parser.add_argument("--report", default="both", choices=["text", "json", "both"], help="Report format")
        parser.add_argument("--report-path", default="report", help="Report output path (without extension)")
        parser.add_argument("--verify-semantics", action=argparse.BooleanOptionalAction, default=True, help="Run semantic validations")
        parser.add_argument("--validation-level", choices=["balanced", "strict"], help="Validation security level: balanced (default) or strict")
        parser.add_argument("--strict-validation", action="store_true", help="Alias for --validation-level strict")
        parser.add_argument("--dry-run", action="store_true", help="Dry run mode (no files written)")
        parser.add_argument("--java-raw-json", help="Path to temporary Java raw metrics JSON")

        args = parser.parse_args()

        if args.strict_validation and args.validation_level not in (None, ValidationLevel.STRICT):
            parser.error("--strict-validation cannot be combined with --validation-level balanced")
        validation_level = (
            ValidationLevel.STRICT
            if args.strict_validation
            else validate_validation_level(args.validation_level or ValidationLevel.BALANCED)
        )
        validate_mode_profile_combination(args.mode, args.profile, args.dictionary_scope)


        file_repo = PhysicalFilesystem(durable=args.durable_writes)
        token_counter = OfflineTokenizer(enable_cache=not args.no_cache)
        hash_service = HashService()
        json_codec = JsonCodec()

        src_abs = file_repo.abspath(args.src)
        dst_abs = file_repo.abspath(args.dst)

        if not file_repo.exists(src_abs):
            raise SourcePathError(f"Source not found: {src_abs}")

        validate_filesystem_safety(src_abs, dst_abs, args.report_path)

        java_raw_metrics = []
        generated_bundles = []

        java_processed_relpaths = set()
        if args.java_raw_json and file_repo.exists(args.java_raw_json):
            try:
                java_raw_metrics = json_codec.decode(file_repo.read_text(args.java_raw_json))
                file_repo.remove(args.java_raw_json)
                for entry in java_raw_metrics:
                    java_processed_relpaths.add(entry["filepath"].replace('\\', '/'))
            except Exception as je:
                print(f"Warning: failed to read Java raw metrics JSON: {je}")

        report_gen = ReportGeneratorUsecase(file_repo, json_codec)
        file_opt = None
        if args.profile == "auto" or args.dictionary_scope == "file":
            from cida.application.optimize_file import FileOptimizerUsecase
            file_opt = FileOptimizerUsecase(token_counter, file_repo, hash_service, json_codec)
        semantic_dependencies: tuple[type[Any], Any] | None = None

        def ensure_semantic_dependencies() -> tuple[type[Any], Any]:
            nonlocal semantic_dependencies
            if semantic_dependencies is None:
                semantic_dependencies = _load_semantic_dependencies()
            return semantic_dependencies

        dictionary_builder = None
        if args.dictionary_scope == "corpus":
            from cida.markdown.dictionary import CorpusDictionaryBuilder
            dictionary_builder = CorpusDictionaryBuilder()
        corpus_opt = CorpusOptimizerUsecase(token_counter, file_repo, hash_service, json_codec, dictionary_builder)

        inventory = corpus_opt.build_file_inventory(src_abs, java_processed_relpaths)
        files_to_process = inventory.processable_files

        if not files_to_process and not java_raw_metrics:
            raise SourcePathError(f"No processable files found in source: {src_abs}")



        for entry in java_raw_metrics:
            orig_content = entry["original_content"]
            mini_content = entry["minified_content"]

            orig_tokens = token_counter.count(orig_content)
            final_tokens = token_counter.count(mini_content)

            base_content = minificar_codigo_para_ia(orig_content)
            base_tokens = token_counter.count(base_content)

            report_gen.add_entry(
                filepath=os.path.join(src_abs, entry["filepath"]),
                profile="java",
                tokens_orig=orig_tokens,
                tokens_base=base_tokens,
                tokens_new=final_tokens,
                dict_included=entry.get("dict_included", False),
                tokens_sidecar=entry.get("tokens_sidecar", 0),
                tokens_aux=entry.get("tokens_auxiliares", 0),
                accepted_transforms=["go_minification"],
                rejected_transforms=[],
                semantic_status="SUCCESS",
                execution_time=entry["elapsed_ns"] / 1e9
            )

        corpus_dict = {}
        corpus_hash = ""
        sidecar_tokens_total = 0
        auxiliary_tokens = 0

        content_cache: dict[str, ProcessingContext] = {}

        if args.dictionary_scope == "corpus":
            from cida.markdown.dictionary import apply_dictionary
            corpus_dict, corpus_hash, sidecar_tokens_total, auxiliary_tokens = corpus_opt.build_corpus_dict(
                files_to_process,
                src_abs,
                skip_binary_check=True,
            )

            if corpus_dict:
                total_orig_tokens = 0
                total_mini_tokens = 0
                for fp in files_to_process:
                    try:
                        ctx = _build_processing_context(
                            fp, src_abs, args.profile, file_repo, file_opt, hash_service, token_counter
                        )
                        content_cache[fp] = ctx
                    except Exception as exc:
                        raise SourcePathError(
                            f"Failed to read corpus source for token estimation '{fp}': {exc}"
                        ) from exc
                    c = ctx.source_text
                    total_orig_tokens += ctx.original_tokens
                    prof = ctx.detected_profile

                    if prof in ["markdown", "bmad"]:
                        curr = c
                        curr_tokens = ctx.original_tokens
                        curr = remove_html_comments(curr)
                        curr = trim_trailing_whitespace(curr)
                        curr = normalize_newlines(curr)
                        curr = table_whitespace(curr)
                        curr = list_compaction(curr)
                        if curr != c:
                            curr_tokens = token_counter.count(curr)
                        pm = ProtectedRegionsManager()
                        cand = apply_dictionary(curr, corpus_dict, pm)
                        if cand != curr and args.verify_semantics:
                            ParsedOriginalDocument, validate_semantics = ensure_semantic_dependencies()
                            parsed_c = ParsedOriginalDocument(c)
                            is_valid, _ = validate_semantics(c, cand, corpus_dict, parsed_original=parsed_c)
                            cand_tokens = token_counter.count(cand)
                            if is_valid and cand_tokens < curr_tokens:
                                curr = cand
                                curr_tokens = cand_tokens
                        total_mini_tokens += curr_tokens
                    else:
                        mini = minificar_codigo_para_ia(c, corpus_dict)
                        total_mini_tokens += token_counter.count(mini)

                if total_orig_tokens > 0:
                    net_savings = (total_orig_tokens - total_mini_tokens) - (sidecar_tokens_total + auxiliary_tokens)
                    if net_savings <= 0:
                        corpus_dict = {}
                        corpus_hash = ""
                    else:
                        if not args.dry_run:
                            corpus_opt.write_corpus_sidecars(corpus_dict, corpus_hash, dst_abs)
                else:
                    corpus_dict = {}
                    corpus_hash = ""

        inflation_detected = False
        aggregator = FailureAggregator()

        for filepath in files_to_process:
            start_time = time.time()

            try:
                if filepath in content_cache:
                    ctx = content_cache.pop(filepath)
                else:
                    ctx = _build_processing_context(
                        filepath, src_abs, args.profile, file_repo, file_opt, hash_service, token_counter
                    )
            except Exception as e:
                print(f"Error reading {filepath}: {e}", file=sys.stderr)
                report_gen.add_entry(
                    filepath=filepath,
                    profile=args.profile,
                    tokens_orig=0,
                    tokens_base=0,
                    tokens_new=0,
                    dict_included=False,
                    tokens_sidecar=0,
                    tokens_aux=0,
                    accepted_transforms=[],
                    rejected_transforms=[],
                    semantic_status="FAILED",
                    execution_time=0.0
                )
                if not args.continue_on_error:
                    if isinstance(e, CidaError):
                        raise
                    raise CidaError(f"Failed to read file {filepath}: {e}") from e
                aggregator.add(filepath, "read", e if isinstance(e, CidaError) else SourcePathError(str(e)))
                continue

            content = ctx.source_text
            content_bytes = ctx.source_bytes
            profile = ctx.detected_profile
            validate_mode_profile_combination(args.mode, args.profile, args.dictionary_scope, profile)

            content_sha = ctx.source_sha256
            orig_tokens = ctx.original_tokens
            parsed_orig = None
            final_semantics_validated = False

            if profile in ["markdown", "bmad"]:
                legacy = re.sub(r'^---\s*[\r\n]+.*?[\r\n]+---\s*[\r\n]+', '', content, flags=re.DOTALL)
                legacy = re.sub(r'<!--.*?-->', '', legacy, flags=re.DOTALL)
                legacy = re.sub(r'!\[([^\]]*)\]\([^)]+\)', r'[\1]', legacy)
                legacy = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', legacy)
                legacy = re.sub(r'(?<!\w)(\*\*|__|\*|_)(.*?)\1(?!\w)', r'\2', legacy)
                legacy = re.sub(r'^[-*_]{3,}\s*$', '', legacy, flags=re.MULTILINE)
                legacy = re.sub(r'\|\s+', '|', legacy)
                legacy = re.sub(r'\s+\|', '|', legacy)
                legacy = re.sub(r' {2,}', ' ', legacy)
                legacy = re.sub(r'\n{3,}', '\n\n', legacy)
                base_tokens = token_counter.count(legacy.strip())
            else:
                legacy = minificar_codigo_para_ia(content)
                base_tokens = token_counter.count(legacy)

            accepted_transforms = []
            rejected_transforms = []

            dict_included = False
            tokens_sidecar = 0
            tokens_aux = 0
            best_sidecar_data = None

            if profile in ["markdown", "bmad"]:
                current_text = content
                current_tokens = orig_tokens

                candidates = []
                if args.mode == "semantic":
                    candidates = [
                        ("remove_html_comments", remove_html_comments),
                        ("trim_trailing_whitespace", trim_trailing_whitespace),
                        ("normalize_newlines", normalize_newlines),
                        ("table_whitespace", table_whitespace),
                        ("list_compaction", list_compaction),
                    ]

                for name, trans_fn in candidates:
                    candidate_text = trans_fn(current_text)
                    if candidate_text == current_text:
                        rejected_transforms.append(f"{name}_no_gain")
                        continue

                    candidate_tokens = token_counter.count(candidate_text)
                    if candidate_tokens >= current_tokens:
                        rejected_transforms.append(f"{name}_no_gain")
                        continue

                    if args.verify_semantics:
                        ParsedOriginalDocument, validate_semantics = ensure_semantic_dependencies()
                        if parsed_orig is None:
                            parsed_orig = ParsedOriginalDocument(content)
                        is_valid, _ = validate_semantics(content, candidate_text, parsed_original=parsed_orig)
                        if not is_valid:
                            rejected_transforms.append(f"{name}_semantic_fail")
                            continue

                    current_text = candidate_text
                    current_tokens = candidate_tokens
                    accepted_transforms.append(name)
                    final_semantics_validated = args.verify_semantics

                if args.dictionary_scope == "file":
                    candidate_text, sidecar_data, dict_tokens = file_opt.optimize_markdown_dictionary_file_scope(
                        content, current_text, ctx.relative_path, args.verify_semantics, precomputed_source_sha256=content_sha
                    )
                    if sidecar_data:
                        cand_tokens = token_counter.count(candidate_text)
                        cand_sidecar_tokens = token_counter.count(json_codec.encode(sidecar_data, indent=4))
                        cand_aux_tokens = 0

                        economia_bruta = orig_tokens - cand_tokens
                        overhead = cand_sidecar_tokens + cand_aux_tokens
                        if economia_bruta - overhead > 0:
                            current_text = candidate_text
                            current_tokens = cand_tokens
                            dict_included = True
                            tokens_sidecar = cand_sidecar_tokens
                            tokens_aux = cand_aux_tokens
                            best_sidecar_data = sidecar_data
                            accepted_transforms.append("file_dictionary")
                            final_semantics_validated = args.verify_semantics
                        else:
                            rejected_transforms.append("file_dictionary_no_gain")
                    else:
                        rejected_transforms.append("file_dictionary_no_gain")

                elif args.dictionary_scope == "corpus" and corpus_dict:
                    pm = ProtectedRegionsManager()
                    candidate_text = apply_dictionary(current_text, corpus_dict, pm)

                    if args.verify_semantics:
                        ParsedOriginalDocument, validate_semantics = ensure_semantic_dependencies()
                        if parsed_orig is None:
                            parsed_orig = ParsedOriginalDocument(content)
                        is_valid, _ = validate_semantics(content, candidate_text, corpus_dict, parsed_original=parsed_orig)
                        if is_valid:
                            cand_tokens = token_counter.count(candidate_text)
                            if cand_tokens < current_tokens:
                                cand_sidecar_tokens = int(sidecar_tokens_total * orig_tokens / total_orig_tokens) if total_orig_tokens > 0 else 0
                                cand_aux_tokens = int(auxiliary_tokens * orig_tokens / total_orig_tokens) if total_orig_tokens > 0 else 0

                                economia_bruta = orig_tokens - cand_tokens
                                overhead = cand_sidecar_tokens + cand_aux_tokens
                                if economia_bruta - overhead > 0:
                                    current_text = candidate_text
                                    current_tokens = cand_tokens
                                    dict_included = True
                                    tokens_sidecar = cand_sidecar_tokens
                                    tokens_aux = cand_aux_tokens
                                    accepted_transforms.append("corpus_dictionary")
                                    final_semantics_validated = args.verify_semantics
                                else:
                                    rejected_transforms.append("corpus_dictionary_no_gain")
                            else:
                                rejected_transforms.append("corpus_dictionary_no_gain")
                        else:
                            rejected_transforms.append("corpus_dictionary_semantic_fail")

                final_text = current_text
                final_tokens = current_tokens

                economia_bruta = orig_tokens - final_tokens
                overhead = tokens_sidecar + tokens_aux
                if economia_bruta - overhead <= 0:
                    final_text = content
                    final_tokens = orig_tokens
                    dict_included = False
                    tokens_sidecar = 0
                    tokens_aux = 0
                    best_sidecar_data = None
                    semantic_status = "UNCHANGED_NO_TOKEN_GAIN"
                else:
                    semantic_status = "SUCCESS"
            else:
                final_text = minificar_codigo_para_ia(content, corpus_dict if args.dictionary_scope == "corpus" else None)
                final_tokens = token_counter.count(final_text)
                dict_included = True if corpus_dict else False
                tokens_sidecar = 0
                tokens_aux = 0
                if dict_included:
                    tokens_sidecar = int(sidecar_tokens_total * orig_tokens / total_orig_tokens) if total_orig_tokens > 0 else 0
                    tokens_aux = int(auxiliary_tokens * orig_tokens / total_orig_tokens) if total_orig_tokens > 0 else 0

                economia_bruta = orig_tokens - final_tokens
                overhead = tokens_sidecar + tokens_aux
                if economia_bruta - overhead <= 0:
                    final_text = content
                    final_tokens = orig_tokens
                    dict_included = False
                    tokens_sidecar = 0
                    tokens_aux = 0
                    semantic_status = "UNCHANGED_NO_TOKEN_GAIN"
                else:
                    semantic_status = "SUCCESS"

            exec_time = time.time() - start_time

            if (
                args.verify_semantics
                and profile in ["markdown", "bmad"]
                and not final_semantics_validated
                and (final_text != content or _requires_identity_semantic_validation(content))
            ):
                ParsedOriginalDocument, validate_semantics = ensure_semantic_dependencies()
                if parsed_orig is None:
                    parsed_orig = ParsedOriginalDocument(content)
                validation_dict = {}
                if dict_included:
                    if best_sidecar_data:
                        validation_dict = {v: k for k, v in best_sidecar_data["entries"].items()}
                    elif corpus_dict:
                        validation_dict = corpus_dict
                try:
                    is_valid, msg = validate_semantics(content, final_text, validation_dict, parsed_original=parsed_orig)
                except Exception as ve:
                    is_valid = False
                    msg = str(ve)
                if not is_valid:
                    print(f"Semantic validation failed for {filepath}: {msg}", file=sys.stderr)
                    sys.exit(3)

            rel_path = ctx.relative_path
            dest_path = os.path.join(dst_abs, rel_path)
            if profile in ["java", "code"] and not dest_path.endswith('.tknc'):
                dest_path += '.tknc'

            text_to_write = final_text
            sidecar_path = None
            if dict_included and best_sidecar_data is not None:
                from cida.domain.sidecar import create_compressed_envelope
                sidecar_ref = file_repo.basename(dest_path) + ".cidatkn"
                text_to_write = create_compressed_envelope(
                    payload=final_text,
                    sidecar_ref=sidecar_ref,
                    source_sha256=best_sidecar_data["source_sha256"],
                    mode=args.mode,
                    strategy="dictionary"
                )
                sidecar_path = dest_path + ".cidatkn"

            if not args.dry_run:
                out_bytes = text_to_write.encode('utf-8')
                file_repo.write_bytes(dest_path, out_bytes)
                if sidecar_path:
                    file_repo.write_text(sidecar_path, json_codec.encode(best_sidecar_data, indent=4))
                generated_bundles.append((filepath, dest_path, sidecar_path, content_bytes, out_bytes))

            final_written_tokens = token_counter.count(text_to_write)
            if final_written_tokens > orig_tokens:
                inflation_detected = True
                print(f"WARNING: Inflation in {filepath} ({orig_tokens} -> {final_written_tokens})")

            report_gen.add_entry(
                filepath=filepath,
                profile=profile,
                tokens_orig=orig_tokens,
                tokens_base=base_tokens,
                tokens_new=final_written_tokens,
                dict_included=dict_included,
                tokens_sidecar=tokens_sidecar,
                tokens_aux=tokens_aux,
                accepted_transforms=accepted_transforms,
                rejected_transforms=rejected_transforms,
                semantic_status=semantic_status,
                execution_time=exec_time
            )

        report_name = args.report_path
        if not args.dry_run and args.report in ["text", "both", "json"]:
            report_gen.save_reports(report_name + ".md", report_name + ".json", src_abs, args.report)
            print("\nBenchmark reports saved:")
            print(f"  Markdown: {report_name}.md")
            print(f"  JSON:     {report_name}.json")

        if args.fail_on_inflation and inflation_detected:
            print("Error: Inflation detected during token optimization.")
            sys.exit(1)

        if not args.dry_run and validation_level == ValidationLevel.STRICT:
            StrictBundleAuditor = _load_strict_bundle_auditor()
            strict_auditor = StrictBundleAuditor(file_repo, json_codec, hash_service)
            strict_auditor.audit_destination_sidecars(src_abs, dst_abs)
            for item in generated_bundles:
                _, out_f, side_f = item[0], item[1], item[2]
                strict_auditor.audit_output_bundle(src_abs, dst_abs, out_f, side_f)


        if aggregator.records:
            aggregator.print_summary()
            print("Error: One or more files failed to process during execution.", file=sys.stderr)
            sys.exit(aggregator.final_exit_code)

    except CidaError as ce:
        print(f"CIDA execution error: {ce}", file=sys.stderr)
        sys.exit(ce.exit_code)
    except Exception as e:
        print(f"Fatal error in CIDA CLI: {e}", file=sys.stderr)
        sys.exit(6)

if __name__ == "__main__":
    main()
