import os
import sys
import argparse
import time
import re
from cida.domain.errors import (
    CidaError, SourcePathError
)
from cida.infrastructure.filesystem import PhysicalFilesystem
from cida.infrastructure.tokenizer import OfflineTokenizer
from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec
from cida.application.optimize_file import FileOptimizerUsecase
from cida.application.optimize_corpus import CorpusOptimizerUsecase
from cida.application.validate_sidecar import SidecarValidatorUsecase
from cida.application.generate_report import ReportGeneratorUsecase
from cida.markdown.protected_regions import ProtectedRegionsManager
from cida.markdown.dictionary import apply_dictionary, CorpusDictionaryBuilder
from cida.markdown.transforms import (
    remove_html_comments, trim_trailing_whitespace, normalize_newlines,
    table_whitespace, list_compaction, minificar_codigo_para_ia
)
from cida.markdown.semantic_equivalence import validate_semantics

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
            print("Uso: python3 translate.py [ID1] [ID2] ... [--path <caminho_da_pasta_de_sidecars>]")
            return

        sidecar_dir = os.path.join(os.getcwd(), "sidecar")
        if not file_repo.exists(sidecar_dir) and file_repo.exists(os.path.join(os.getcwd(), "tknd")):
            sidecar_dir = os.path.join(os.getcwd(), "tknd")

        args = sys.argv[1:]
        if "--path" in args:
            idx = args.index("--path")
            if idx + 1 < len(args):
                sidecar_dir = args[idx+1]
                args = args[:idx] + args[idx+2:]

        mapping = {}
        if not file_repo.exists(sidecar_dir):
            print(f"Erro: Pasta {sidecar_dir} não encontrada.", file=sys.stderr)
            sys.exit(5)

        for file in file_repo.list_dir(sidecar_dir):
            if file.endswith(".cidatkn"):
                try:
                    data = json_codec.decode(file_repo.read_text(os.path.join(sidecar_dir, file)))
                    if isinstance(data, dict) and "entries" in data:
                        for alias, val in data["entries"].items():
                            mapping[alias] = val
                except Exception as e:
                    print(f"Erro ao ler dicionário {file}: {e}", file=sys.stderr)
                    sys.exit(5)

        results = {}
        for t in args:
            results[t] = mapping.get(t, "Não encontrado")
        print(results)
    except CidaError as ce:
        sys.exit(ce.exit_code)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(6)

def main():
    try:
        parser = argparse.ArgumentParser(description="Token-oriented Markdown Minifier for BMAD")
        parser.add_argument("--src", required=True, help="Source directory or file")
        parser.add_argument("--dst", required=True, help="Destination directory")
        parser.add_argument("--mode", default="lossless", choices=["lossless", "semantic"], help="Compression mode")
        parser.add_argument("--profile", default="auto", choices=["auto", "code", "java", "markdown", "bmad"], help="Processing profile")
        parser.add_argument("--dictionary-scope", default="file", choices=["none", "file", "corpus"], help="Dictionary scope")
        parser.add_argument("--fail-on-inflation", action="store_true", help="Fail if any file has token count inflation")
        parser.add_argument("--report", default="both", choices=["text", "json", "both"], help="Report format")
        parser.add_argument("--report-path", default="report", help="Report output path (without extension)")
        parser.add_argument("--verify-semantics", action=argparse.BooleanOptionalAction, default=True, help="Run semantic validations")
        parser.add_argument("--dry-run", action="store_true", help="Dry run mode (no files written)")
        parser.add_argument("--java-raw-json", help="Path to temporary Java raw metrics JSON")

        args = parser.parse_args()

        file_repo = PhysicalFilesystem()
        token_counter = OfflineTokenizer()
        hash_service = HashService()
        json_codec = JsonCodec()

        src_abs = file_repo.abspath(args.src)
        dst_abs = file_repo.abspath(args.dst)

        if not file_repo.exists(src_abs):
            raise SourcePathError(f"Source not found: {src_abs}")

        java_raw_metrics = []


        java_processed_relpaths = set()
        if args.java_raw_json and file_repo.exists(args.java_raw_json):
            try:
                java_raw_metrics = json_codec.decode(file_repo.read_text(args.java_raw_json))
                file_repo.remove(args.java_raw_json)
                for entry in java_raw_metrics:
                    java_processed_relpaths.add(entry["filepath"].replace('\\', '/'))
            except Exception as je:
                print(f"Warning: failed to read Java raw metrics JSON: {je}")

        supported_exts = ('.md', '.txt', '.py', '.java', '.go', '.js', '.ts')
        files_to_process = []
        if file_repo.is_file(src_abs):
            if any(src_abs.endswith(ext) for ext in supported_exts):
                files_to_process.append(src_abs)
            else:
                raise SourcePathError(f"Unsupported file extension: {src_abs}")
        else:
            for filepath in file_repo.list_files(src_abs):
                if dst_abs in file_repo.abspath(filepath):
                    continue
                name = os.path.basename(filepath)
                if "tknd" in filepath or "sidecar" in filepath or "_mimificado" in name:
                    continue
                rel_p = file_repo.relpath(filepath, src_abs).replace('\\', '/')
                if rel_p in java_processed_relpaths or name in java_processed_relpaths:
                    continue
                if any(filepath.endswith(ext) for ext in supported_exts):
                    files_to_process.append(filepath)
        files_to_process.sort()

        if not files_to_process and not java_raw_metrics:
            raise SourcePathError(f"No processable files found in source: {src_abs}")

        report_gen = ReportGeneratorUsecase(file_repo, json_codec)
        file_opt = FileOptimizerUsecase(token_counter, file_repo, hash_service, json_codec)
        dictionary_builder = CorpusDictionaryBuilder()
        corpus_opt = CorpusOptimizerUsecase(token_counter, file_repo, hash_service, json_codec, dictionary_builder)
        sidecar_val = SidecarValidatorUsecase(file_repo, json_codec, hash_service)


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

        if args.dictionary_scope == "corpus":
            corpus_dict, corpus_hash, sidecar_tokens_total, auxiliary_tokens = corpus_opt.build_corpus_dict(files_to_process, src_abs)

            if corpus_dict:
                total_orig_tokens = 0
                total_mini_tokens = 0
                for fp in files_to_process:
                    if file_repo.is_binary_file(fp):
                        continue
                    try:
                        c = file_repo.read_text(fp)
                    except Exception:
                        continue
                    total_orig_tokens += token_counter.count(c)

                    prof = args.profile
                    if prof == "auto":
                        prof = file_opt.detect_profile(fp, c)

                    if prof in ["markdown", "bmad"]:
                        curr = c
                        curr = remove_html_comments(curr)
                        curr = trim_trailing_whitespace(curr)
                        curr = normalize_newlines(curr)
                        curr = table_whitespace(curr)
                        curr = list_compaction(curr)
                        pm = ProtectedRegionsManager()
                        cand = apply_dictionary(curr, corpus_dict, pm)
                        if args.verify_semantics:
                            is_valid, _ = validate_semantics(c, cand, corpus_dict)
                            if is_valid and token_counter.count(cand) < token_counter.count(curr):
                                curr = cand
                        total_mini_tokens += token_counter.count(curr)
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

        for filepath in files_to_process:
            start_time = time.time()

            if file_repo.is_binary_file(filepath):
                if not args.dry_run:
                    rel_path = file_repo.relpath(filepath, src_abs) if os.path.isdir(src_abs) else os.path.basename(filepath)
                    dest_path = os.path.join(dst_abs, rel_path)
                    file_repo.copy(filepath, dest_path)
                continue

            try:
                content = file_repo.read_text(filepath)
            except Exception as e:
                print(f"Error reading {filepath}: {e}")
                continue

            profile = args.profile
            if profile == "auto":
                profile = file_opt.detect_profile(filepath, content)

            orig_tokens = token_counter.count(content)

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

                    if args.verify_semantics:
                        is_valid, _ = validate_semantics(content, candidate_text)
                        if not is_valid:
                            rejected_transforms.append(f"{name}_semantic_fail")
                            continue

                    cand_tokens = token_counter.count(candidate_text)
                    curr_tokens = token_counter.count(current_text)

                    if cand_tokens < curr_tokens:
                        current_text = candidate_text
                        accepted_transforms.append(name)
                    else:
                        rejected_transforms.append(f"{name}_no_gain")

                if args.dictionary_scope == "file":
                    rel_path = file_repo.relpath(filepath, src_abs) if os.path.isdir(src_abs) else os.path.basename(filepath)
                    candidate_text, sidecar_data, dict_tokens = file_opt.optimize_markdown_dictionary_file_scope(
                        content, current_text, rel_path, args.verify_semantics
                    )
                    if sidecar_data:
                        cand_tokens = token_counter.count(candidate_text)
                        cand_sidecar_tokens = token_counter.count(json_codec.encode(sidecar_data, indent=4))
                        cand_aux_tokens = token_counter.count("Use the companion sidecar file to resolve aliases.")

                        economia_bruta = orig_tokens - cand_tokens
                        overhead = cand_sidecar_tokens + cand_aux_tokens
                        if economia_bruta - overhead > 0:
                            current_text = candidate_text
                            dict_included = True
                            tokens_sidecar = cand_sidecar_tokens
                            tokens_aux = cand_aux_tokens
                            best_sidecar_data = sidecar_data
                            accepted_transforms.append("file_dictionary")
                        else:
                            rejected_transforms.append("file_dictionary_no_gain")
                    else:
                        rejected_transforms.append("file_dictionary_no_gain")

                elif args.dictionary_scope == "corpus" and corpus_dict:
                    pm = ProtectedRegionsManager()
                    candidate_text = apply_dictionary(current_text, corpus_dict, pm)

                    if args.verify_semantics:
                        is_valid, _ = validate_semantics(content, candidate_text, corpus_dict)
                        if is_valid:
                            cand_tokens = token_counter.count(candidate_text)
                            curr_tokens = token_counter.count(current_text)
                            if cand_tokens < curr_tokens:
                                cand_sidecar_tokens = int(sidecar_tokens_total * orig_tokens / total_orig_tokens) if total_orig_tokens > 0 else 0
                                cand_aux_tokens = int(auxiliary_tokens * orig_tokens / total_orig_tokens) if total_orig_tokens > 0 else 0

                                economia_bruta = orig_tokens - cand_tokens
                                overhead = cand_sidecar_tokens + cand_aux_tokens
                                if economia_bruta - overhead > 0:
                                    current_text = candidate_text
                                    dict_included = True
                                    tokens_sidecar = cand_sidecar_tokens
                                    tokens_aux = cand_aux_tokens
                                    accepted_transforms.append("corpus_dictionary")
                                else:
                                    rejected_transforms.append("corpus_dictionary_no_gain")
                            else:
                                rejected_transforms.append("corpus_dictionary_no_gain")
                        else:
                            rejected_transforms.append("corpus_dictionary_semantic_fail")

                final_text = current_text
                final_tokens = token_counter.count(final_text)

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

            if args.verify_semantics and profile in ["markdown", "bmad"]:
                validation_dict = {}
                if dict_included:
                    if best_sidecar_data:
                        validation_dict = {v: k for k, v in best_sidecar_data["entries"].items()}
                    elif corpus_dict:
                        validation_dict = corpus_dict
                try:
                    is_valid, msg = validate_semantics(content, final_text, validation_dict)
                except Exception as ve:
                    is_valid = False
                    msg = str(ve)
                if not is_valid:
                    print(f"Semantic validation failed for {filepath}: {msg}", file=sys.stderr)
                    sys.exit(3)


            if final_tokens > orig_tokens:
                inflation_detected = True
                print(f"WARNING: Inflation in {filepath} ({orig_tokens} -> {final_tokens})")

            if not args.dry_run:
                rel_path = file_repo.relpath(filepath, src_abs) if os.path.isdir(src_abs) else os.path.basename(filepath)
                dest_path = os.path.join(dst_abs, rel_path)

                if profile in ["java", "code"] and not dest_path.endswith('.tknc'):
                    dest_path += '.tknc'

                file_repo.write_bytes(dest_path, final_text.encode('utf-8'))

                if dict_included and best_sidecar_data is not None:
                    sidecar_path = dest_path + ".cidatkn"
                    file_repo.write_text(sidecar_path, json_codec.encode(best_sidecar_data, indent=4))

            report_gen.add_entry(
                filepath=filepath,
                profile=profile,
                tokens_orig=orig_tokens,
                tokens_base=base_tokens,
                tokens_new=final_tokens,
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

        if not args.dry_run:
            sidecar_val.verify_destination_sidecars(src_abs, dst_abs)

    except CidaError as ce:
        print(f"CIDA execution error: {ce}", file=sys.stderr)
        sys.exit(ce.exit_code)
    except Exception as e:
        print(f"Fatal error in CIDA CLI: {e}", file=sys.stderr)
        sys.exit(6)
