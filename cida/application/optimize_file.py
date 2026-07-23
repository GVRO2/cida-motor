import re
from collections import Counter
from cida.application.ports import TokenCounter, FileRepository, HashService, JsonCodec
from cida.markdown.protected_regions import ProtectedRegionsManager
from cida.markdown.dictionary import generate_alias_candidates, find_candidate_words, apply_dictionary
from cida.domain.sidecar import create_sidecar_data
from cida.markdown.semantic_equivalence import validate_semantics

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

    def optimize_markdown_dictionary_file_scope(self, content: str, transformed_text: str, filepath: str, verify_semantics: bool) -> tuple:
        base_tokens = self.token_counter.count(transformed_text)
        best_tokens = base_tokens
        best_minified = transformed_text
        best_sidecar_data = None

        pm = ProtectedRegionsManager()
        protected_text = pm.protect(transformed_text)

        exclude_set = set(re.findall(r'\b\w+\b', transformed_text))
        candidate_words = find_candidate_words(protected_text)
        if not candidate_words:
            return transformed_text, None, 0

        word_counts = Counter(candidate_words)
        sorted_words = sorted(word_counts.items(), key=lambda x: x[1] * len(x[0]), reverse=True)

        aliases = generate_alias_candidates(exclude_set, limit=len(word_counts) + 10)

        current_dict = {}
        alias_idx = 0

        # First pass: collect words with individual token savings
        for word, freq in sorted_words:
            if freq < 2:
                continue
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

        words_to_eval = list(current_dict.items())
        working_dict = {}

        for word, alias in words_to_eval:
            working_dict[word] = alias
            candidate_minified = apply_dictionary(transformed_text, working_dict, pm)
            entries_dict = {a: w for w, a in working_dict.items()}

            try:
                sidecar_data = create_sidecar_data(filepath, content.encode('utf-8'), entries_dict, self.hash_service)
            except Exception:
                working_dict.pop(word)
                continue

            if verify_semantics:
                is_valid, _ = validate_semantics(content, candidate_minified, working_dict)
                if not is_valid:
                    working_dict.pop(word)
                    continue

            tokens_min = self.token_counter.count(candidate_minified)
            tokens_sidecar = self.token_counter.count(self.json_codec.encode(sidecar_data, indent=4))
            tokens_instr = self.token_counter.count("Use the companion sidecar file to resolve aliases.")

            effective_tokens = tokens_min + tokens_sidecar + tokens_instr

            if effective_tokens < best_tokens:
                best_tokens = effective_tokens
                best_minified = candidate_minified
                best_sidecar_data = sidecar_data

        final_tokens_dict = self.token_counter.count(self.json_codec.encode(best_sidecar_data, indent=4)) if best_sidecar_data else 0
        return best_minified, best_sidecar_data, final_tokens_dict

