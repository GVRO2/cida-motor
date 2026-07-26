import re
from collections import Counter
from typing import Any, Callable
from cida.application.ports import TokenCounter, FileRepository, HashService, JsonCodec
from cida.markdown.protected_regions import ProtectedRegionsManager
from cida.markdown.dictionary import _needs_protection, generate_alias_candidates, find_candidate_words, apply_dictionary
from cida.domain.sidecar import create_sidecar_data


def _load_semantic_dependencies() -> tuple[type[Any], Callable[..., tuple[bool, str]]]:
    from cida.markdown.semantic_equivalence import ParsedOriginalDocument, validate_semantics
    return ParsedOriginalDocument, validate_semantics


class FileOptimizerUsecase:
    """Orchestrates token minification and dictionary replacement for a single file."""

    def __init__(self, token_counter: TokenCounter, file_repo: FileRepository,
                 hash_service: HashService, json_codec: JsonCodec):
        self.token_counter = token_counter
        self.file_repo = file_repo
        self.hash_service = hash_service
        self.json_codec = json_codec

    def detect_profile(self, filepath: str, content: str) -> str:
        name = self.file_repo.basename(filepath).lower()
        ext = "." + name.rsplit(".", 1)[1] if "." in name else ""

        if ext == '.java':
            return 'java'

        bmad_names = [
            'workflow.md', 'skill.md', 'agents.md', 'checklist.md',
            'project-context.md', 'prd.md', 'architecture.md'
        ]
        if name in bmad_names or name.startswith('step-') or name.endswith('-template.md'):
            return 'bmad'

        path_parts = filepath.lower().replace('\\', '/').split('/')
        if any(p in path_parts for p in ['_bmad', '_bmad-output', 'steps-c', 'steps-e', 'steps-v']):
            return 'bmad'

        if ext not in ['.md', '.txt']:
            return 'code'

        if re.search(r'stepsCompleted|workflowType|inputDocuments|nextStepFile|outputFile', content):
            return 'bmad'

        if re.search(r'<[^>]+>|\{[\w.-]+\}|\$\{\w+\}', content):
            return 'bmad'

        return 'markdown'

    def optimize_markdown_dictionary_file_scope(
        self,
        content: str,
        transformed_text: str,
        filepath: str,
        verify_semantics: bool,
        precomputed_source_sha256: str = "",
        precomputed_transformed_tokens: int | None = None,
    ) -> tuple:
        content_bytes = content.encode('utf-8')
        source_sha256 = precomputed_source_sha256 if precomputed_source_sha256 else self.hash_service.sha256(content_bytes)

        base_tokens = (
            precomputed_transformed_tokens
            if precomputed_transformed_tokens is not None
            else self.token_counter.count(transformed_text)
        )
        best_tokens = base_tokens
        best_minified = transformed_text
        best_sidecar_data = None

        candidate_words = find_candidate_words(transformed_text)
        if not candidate_words:
            return transformed_text, None, 0

        word_counts = Counter(candidate_words)
        if not any(freq >= 2 for freq in word_counts.values()):
            return transformed_text, None, 0

        pm = ProtectedRegionsManager()
        if _needs_protection(transformed_text):
            protected_text = pm.protect(transformed_text)
            candidate_words = find_candidate_words(protected_text)
            if not candidate_words:
                return transformed_text, None, 0
            word_counts = Counter(candidate_words)

        exclude_set = set(re.findall(r'\b\w+\b', transformed_text))
        sorted_words = [
            item for item in sorted(word_counts.items(), key=lambda x: x[1] * len(x[0]), reverse=True)
            if item[1] >= 2
        ]
        if not sorted_words:
            return transformed_text, None, 0

        aliases = generate_alias_candidates(exclude_set, limit=len(sorted_words) + 10)

        current_dict = {}
        alias_idx = 0

        # First pass: collect words with individual token savings
        for word, freq in sorted_words:
            if alias_idx >= len(aliases):
                break

            alias = aliases[alias_idx]
            tokens_word = self.token_counter.count(word)
            tokens_alias = self.token_counter.count(alias)
            if freq * (tokens_word - tokens_alias) > 0:
                current_dict[word] = alias
                alias_idx += 1

        if not current_dict:
            return transformed_text, None, 0

        parsed_orig = None
        ParsedOriginalDocument: type[Any] | None = None
        validate_semantics: Callable[..., tuple[bool, str]] | None = None
        if verify_semantics:
            ParsedOriginalDocument, validate_semantics = _load_semantic_dependencies()

        words_to_eval = list(current_dict.items())
        working_dict = {}

        for word, alias in words_to_eval:
            working_dict[word] = alias
            candidate_minified = apply_dictionary(transformed_text, working_dict, pm)
            entries_dict = {a: w for w, a in working_dict.items()}

            try:
                sidecar_data = create_sidecar_data(filepath, content_bytes, entries_dict, self.hash_service, precomputed_sha256=source_sha256)
            except Exception:
                working_dict.pop(word)
                continue

            sidecar_ref = self.file_repo.basename(filepath) + ".cidatkn"
            envelope_header = (
                f"<!-- CIDA_COMPRESSED_FORMAT\n"
                f"version: 1\n"
                f"mode: lossless\n"
                f"sidecar_required: true\n"
                f"sidecar_ref: {sidecar_ref}\n"
                f"source_sha256: {sidecar_data['source_sha256']}\n"
                f"compression_strategy: dictionary\n"
                f"-->\n"
            )

            tokens_min = self.token_counter.count(candidate_minified)
            tokens_envelope = self.token_counter.count(envelope_header)
            tokens_sidecar = self.token_counter.count(self.json_codec.encode(sidecar_data, indent=4))

            effective_tokens = tokens_min + tokens_envelope + tokens_sidecar

            if effective_tokens >= best_tokens:
                continue

            if verify_semantics:
                assert ParsedOriginalDocument is not None
                assert validate_semantics is not None
                if parsed_orig is None:
                    parsed_orig = ParsedOriginalDocument(content)
                is_valid, _ = validate_semantics(content, candidate_minified, working_dict, parsed_original=parsed_orig)
                if not is_valid:
                    working_dict.pop(word)
                    continue

            best_tokens = effective_tokens
            best_minified = candidate_minified
            best_sidecar_data = sidecar_data

        final_sidecar_tokens = self.token_counter.count(self.json_codec.encode(best_sidecar_data, indent=4)) if best_sidecar_data else 0
        return best_minified, best_sidecar_data, final_sidecar_tokens
