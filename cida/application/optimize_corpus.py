from cida.application.ports import TokenCounter, FileRepository, HashService, JsonCodec, DictionaryBuilder
from cida.domain.errors import SourcePathError, InternalProcessingError
from cida.domain.policies import is_binary_extension
from cida.domain.processing_context import FileInventory, ProcessingContext


class CorpusOptimizerUsecase:
    """Orchestrates corpus-wide dictionary generation and sidecar writing."""

    def __init__(self, token_counter: TokenCounter, file_repo: FileRepository,
                 hash_service: HashService, json_codec: JsonCodec, dictionary_builder: DictionaryBuilder):
        self.token_counter = token_counter
        self.file_repo = file_repo
        self.hash_service = hash_service
        self.json_codec = json_codec
        self.dictionary_builder = dictionary_builder
        self._last_manifest: dict | None = None

    @staticmethod
    def _items_sorted_by_alias(corpus_dict: dict) -> list[tuple[str, str]]:
        from cida.domain.alias_codec import DEFAULT_ALIAS_CODEC

        return sorted(corpus_dict.items(), key=lambda item: DEFAULT_ALIAS_CODEC.decode_alias(item[1]).ordinal)

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
        sources: list[tuple[str, str, str, str]] = []
        for fp in files:
            if (skip_binary_check or not self.file_repo.is_binary_file(fp)) and (fp.endswith('.md') or fp.endswith('.txt')):
                try:
                    content = self.file_repo.read_text(fp)
                except Exception as exc:
                    raise SourcePathError(
                        f"Failed to read corpus source '{fp}': {exc}"
                    ) from exc
                try:
                    rel = self.file_repo.relpath(fp, src_abs).replace('\\', '/')
                    sources.append((fp, rel, content, ""))
                except Exception as exc:
                    raise InternalProcessingError(f"Failed to prepare corpus source '{fp}': {exc}") from exc
        return self._build_corpus_dict_from_sources(sources)

    def build_corpus_dict_from_contexts(self, contexts: list[ProcessingContext]) -> tuple:
        sources = [
            (ctx.source_path, ctx.relative_path.replace("\\", "/"), ctx.source_text, ctx.source_sha256)
            for ctx in contexts
            if ctx.source_path.endswith(('.md', '.txt'))
        ]
        return self._build_corpus_dict_from_sources(sources)

    def _build_corpus_dict_from_sources(self, sources: list[tuple[str, str, str, str]]) -> tuple:
        all_contents = [content for _, _, content, _ in sources]
        corpus_dict = self.dictionary_builder.build_corpus_dictionary(all_contents, self.token_counter)
        if not corpus_dict:
            return {}, "", 0, 0

        manifest_files = []
        for fp, rel, content, source_sha256 in sources:
            try:
                if not source_sha256:
                    source_sha256 = self.hash_service.sha256(content.encode("utf-8"))
                manifest_files.append({"path": rel, "sha256": source_sha256})
            except Exception as exc:
                raise InternalProcessingError(
                    f"Failed to hash corpus source '{fp}': {exc}"
                ) from exc
        manifest_files.sort(key=lambda x: x["path"])
        manifest = {"format": "cida-corpus-manifest", "schema_version": 2, "files": manifest_files}
        manifest_bytes = self.json_codec.canonical_encode(manifest).encode('utf-8')
        corpus_hash = self.hash_service.sha256(manifest_bytes)
        manifest["manifest_sha256"] = corpus_hash
        self._last_manifest = manifest
        dictionary_id = self.hash_service.sha256(self.json_codec.canonical_encode(corpus_dict).encode('utf-8'))

        items = self._items_sorted_by_alias(corpus_dict)
        sidecar_tokens_total = 0
        chunk_count = (len(items) + 499) // 500
        for chunk_index, i in enumerate(range(0, len(items), 500)):
            chunk = items[i:i+500]
            entries_map = {alias: word for word, alias in chunk}
            entries_sha256 = self.hash_service.sha256(self.json_codec.canonical_encode(entries_map).encode('utf-8'))
            sidecar_data = {
                "format": "cida-token-sidecar",
                "version": 2,
                "source": "corpus",
                "dictionary_id": dictionary_id,
                "manifest_sha256": corpus_hash,
                "chunk_index": chunk_index,
                "chunk_count": chunk_count,
                "entries_sha256": entries_sha256,
                "entries": entries_map
            }
            sidecar_tokens_total += self.token_counter.count(self.json_codec.canonical_encode(sidecar_data))

        auxiliary_tokens = self.token_counter.count("Use the companion sidecar file to resolve aliases.")

        return corpus_dict, corpus_hash, sidecar_tokens_total, auxiliary_tokens

    def write_corpus_sidecars(
        self,
        corpus_dict: dict,
        corpus_hash: str,
        dst_abs: str,
        artifact_sizes: dict[str, int] | None = None,
    ) -> dict[str, str]:
        if not corpus_dict:
            return {}
        from cida.application.selective_alias_resolution import (
            ALIAS_INDEX_FILENAME,
            build_alias_index_artifacts,
            corpus_chunk_filename,
        )

        artifact_hashes: dict[str, str] = {}
        items = self._items_sorted_by_alias(corpus_dict)
        tknd_dir = self.file_repo.join(dst_abs, "tknd")
        self.file_repo.makedirs(tknd_dir)
        alias_to_chunk = {}
        chunk_hashes = {}
        chunk_entry_counts = {}
        chunk_entries_sha256 = {}
        dictionary_id = self.hash_service.sha256(self.json_codec.canonical_encode(corpus_dict).encode('utf-8'))
        chunk_count = (len(items) + 499) // 500
        filenames_seen = set()
        for chunk_index, i in enumerate(range(0, len(items), 500)):
            chunk = items[i:i+500]
            entries_map = {alias: word for word, alias in chunk}
            entries_sha256 = self.hash_service.sha256(self.json_codec.canonical_encode(entries_map).encode('utf-8'))
            sidecar_data = {
                "format": "cida-token-sidecar",
                "version": 2,
                "source": "corpus",
                "dictionary_id": dictionary_id,
                "manifest_sha256": corpus_hash,
                "chunk_index": chunk_index,
                "chunk_count": chunk_count,
                "entries_sha256": entries_sha256,
                "entries": entries_map
            }
            chunk_filename = corpus_chunk_filename(chunk_index)
            if chunk_filename in filenames_seen:
                raise InternalProcessingError(f"Duplicate corpus chunk filename generated: {chunk_filename}")
            filenames_seen.add(chunk_filename)
            dict_file_path = self.file_repo.join(tknd_dir, chunk_filename)
            serialized = self.json_codec.canonical_encode(sidecar_data)
            serialized_bytes = serialized.encode("utf-8")
            self.file_repo.write_bytes(dict_file_path, serialized_bytes)
            chunk_hash = self.hash_service.sha256(serialized_bytes)
            chunk_hashes[chunk_filename] = chunk_hash
            rel_chunk = f"tknd/{chunk_filename}"
            artifact_hashes[rel_chunk] = chunk_hash
            if artifact_sizes is not None:
                artifact_sizes[rel_chunk] = len(serialized_bytes)
            chunk_entry_counts[chunk_filename] = len(entries_map)
            chunk_entries_sha256[chunk_filename] = entries_sha256
            for alias in entries_map:
                if alias in alias_to_chunk:
                    raise InternalProcessingError(f"Duplicate alias in corpus dictionary: {alias}")
                alias_to_chunk[alias] = chunk_filename

        index_artifacts = build_alias_index_artifacts(
            alias_to_chunk=alias_to_chunk,
            dictionary_id=dictionary_id,
            chunk_hashes=chunk_hashes,
            hash_service=self.hash_service,
            json_codec=self.json_codec,
            manifest_sha256=corpus_hash,
            chunk_entry_counts=chunk_entry_counts,
            chunk_entries_sha256=chunk_entries_sha256,
        )
        for segment_path, segment_data in sorted(index_artifacts.segments.items()):
            full_segment_path = self.file_repo.join(tknd_dir, *segment_path.split("/"))
            segment_text = self.json_codec.canonical_encode(segment_data)
            segment_bytes = segment_text.encode("utf-8")
            self.file_repo.write_bytes(full_segment_path, segment_bytes)
            rel_segment = f"tknd/{segment_path}"
            artifact_hashes[rel_segment] = self.hash_service.sha256(segment_bytes)
            if artifact_sizes is not None:
                artifact_sizes[rel_segment] = len(segment_bytes)
        index_path = self.file_repo.join(tknd_dir, ALIAS_INDEX_FILENAME)
        index_text = self.json_codec.canonical_encode(index_artifacts.root)
        index_bytes = index_text.encode("utf-8")
        self.file_repo.write_bytes(index_path, index_bytes)
        rel_index = f"tknd/{ALIAS_INDEX_FILENAME}"
        artifact_hashes[rel_index] = self.hash_service.sha256(index_bytes)
        if artifact_sizes is not None:
            artifact_sizes[rel_index] = len(index_bytes)
        if self._last_manifest is not None:
            manifest_path = self.file_repo.join(dst_abs, "tknc-manifest.json")
            manifest_text = self.json_codec.canonical_encode(self._last_manifest)
            manifest_bytes = manifest_text.encode("utf-8")
            self.file_repo.write_bytes(manifest_path, manifest_bytes)
            artifact_hashes["tknc-manifest.json"] = self.hash_service.sha256(manifest_bytes)
            if artifact_sizes is not None:
                artifact_sizes["tknc-manifest.json"] = len(manifest_bytes)
        return artifact_hashes
