from cida.application.ports import TokenCounter, FileRepository, HashService, JsonCodec, DictionaryBuilder
from cida.domain.errors import SourcePathError, InternalProcessingError
from cida.domain.policies import is_binary_extension
from cida.domain.processing_context import FileInventory


class CorpusOptimizerUsecase:
    """Orchestrates corpus-wide dictionary generation and sidecar writing."""

    def __init__(self, token_counter: TokenCounter, file_repo: FileRepository,
                 hash_service: HashService, json_codec: JsonCodec, dictionary_builder: DictionaryBuilder):
        self.token_counter = token_counter
        self.file_repo = file_repo
        self.hash_service = hash_service
        self.json_codec = json_codec
        self.dictionary_builder = dictionary_builder

    def build_file_inventory(
        self,
        src_abs: str,
        java_processed_relpaths: set[str] | None = None,
        supported_exts: tuple[str, ...] = ('.md', '.txt', '.py', '.java', '.go', '.js', '.ts'),
    ) -> FileInventory:
        java_processed_relpaths = java_processed_relpaths or set()
        inventory = FileInventory()
        if self.file_repo.is_file(src_abs):
            candidates = [src_abs]
            src_is_dir = False
        else:
            candidates = self.file_repo.list_files(src_abs)
            src_is_dir = True

        for filepath in sorted(candidates):
            inventory.all_files.append(filepath)
            rel_p = self.file_repo.relpath(filepath, src_abs).lstrip('./') if src_is_dir else self.file_repo.basename(filepath)
            ext = "." + filepath.rsplit(".", 1)[1].lower() if "." in filepath else ""
            is_supported = ext in supported_exts
            is_binary = is_binary_extension(filepath) or (not is_supported and self.file_repo.is_binary_file(filepath))

            if is_binary:
                inventory.binary_files.append(filepath)
                continue
            if ext in ('.md', '.txt'):
                inventory.markdown_files.append(filepath)
            elif ext == '.java':
                inventory.java_files.append(filepath)
            elif ext in supported_exts:
                inventory.code_files.append(filepath)

            if (
                rel_p not in java_processed_relpaths
                and not rel_p.startswith("tknd/")
                and filepath.endswith(supported_exts)
            ):
                inventory.processable_files.append(filepath)

        return inventory

    def build_corpus_dict(self, files: list, src_abs: str, skip_binary_check: bool = False) -> tuple:
        all_contents = []
        text_by_path = {}
        for fp in files:
            if (skip_binary_check or not self.file_repo.is_binary_file(fp)) and (fp.endswith('.md') or fp.endswith('.txt')):
                try:
                    content = self.file_repo.read_text(fp)
                    text_by_path[fp] = content
                    all_contents.append(content)
                except Exception as exc:
                    raise SourcePathError(
                        f"Failed to read corpus source '{fp}': {exc}"
                    ) from exc
        corpus_dict = self.dictionary_builder.build_corpus_dictionary(all_contents, self.token_counter)
        if not corpus_dict:
            return {}, "", 0, 0

        manifest_files = []
        for fp in files:
            if (skip_binary_check or not self.file_repo.is_binary_file(fp)) and (fp.endswith('.md') or fp.endswith('.txt')):
                rel = self.file_repo.relpath(fp, src_abs).replace('\\', '/')
                try:
                    source_bytes = text_by_path[fp].encode('utf-8')
                    sha = self.hash_service.sha256(source_bytes)
                    manifest_files.append({"path": rel, "sha256": sha})
                except Exception as exc:
                    raise InternalProcessingError(
                        f"Failed to hash corpus source '{fp}': {exc}"
                    ) from exc
        manifest_files.sort(key=lambda x: x["path"])
        manifest = {"files": manifest_files}
        manifest_bytes = self.json_codec.canonical_encode(manifest).encode('utf-8')
        corpus_hash = self.hash_service.sha256(manifest_bytes)

        items = list(corpus_dict.items())
        sidecar_tokens_total = 0
        for i in range(0, len(items), 500):
            chunk = items[i:i+500]
            entries_map = {alias: word for word, alias in chunk}
            sidecar_data = {
                "format": "cida-token-sidecar",
                "version": 1,
                "source": "corpus",
                "source_sha256": corpus_hash,
                "entries": entries_map
            }
            sidecar_tokens_total += self.token_counter.count(self.json_codec.encode(sidecar_data, indent=4))

        auxiliary_tokens = self.token_counter.count("Use the companion sidecar file to resolve aliases.")

        return corpus_dict, corpus_hash, sidecar_tokens_total, auxiliary_tokens

    def write_corpus_sidecars(self, corpus_dict: dict, corpus_hash: str, dst_abs: str) -> None:
        if not corpus_dict:
            return
        items = list(corpus_dict.items())
        tknd_dir = self.file_repo.join(dst_abs, "tknd")
        self.file_repo.makedirs(tknd_dir)
        for i in range(0, len(items), 500):
            chunk = items[i:i+500]
            prefixChars = "ABCDEF"
            start_id = prefixChars[min(i // 500, len(prefixChars)-1)] + str(i % 500)
            entries_map = {alias: word for word, alias in chunk}
            sidecar_data = {
                "format": "cida-token-sidecar",
                "version": 1,
                "source": "corpus",
                "source_sha256": corpus_hash,
                "entries": entries_map
            }
            dict_file_path = self.file_repo.join(tknd_dir, f"{start_id}.cidatkn")
            self.file_repo.write_text(dict_file_path, self.json_codec.encode(sidecar_data, indent=4))
