from cida.application.ports import FileRepository, HashService, JsonCodec

class ManifestGeneratorUsecase:
    """Usecase to construct deterministic platform/tree manifests."""

    def __init__(self, file_repo: FileRepository, hash_service: HashService, json_codec: JsonCodec):
        self.file_repo = file_repo
        self.hash_service = hash_service
        self.json_codec = json_codec

    def generate_tree_manifest(self, output_dir: str) -> dict:
        files_info = []
        for filepath in self.file_repo.list_files(output_dir):
            rel_path = self.file_repo.relpath(filepath, output_dir).replace('\\', '/')
            # Skip transient/configuration dirs
            if any(p in rel_path.split('/') for p in [".git", ".cida-local", "__pycache__", ".pytest_cache"]):
                continue

            content_bytes = self.file_repo.read_bytes(filepath)
            sha = self.hash_service.sha256(content_bytes)
            size = len(content_bytes)

            files_info.append({
                "path": rel_path,
                "sha256": sha,
                "size": size
            })

        files_info.sort(key=lambda x: str(x["path"]))
        manifest: dict = {"files": files_info}
        manifest_bytes = self.json_codec.canonical_encode(manifest).encode('utf-8')
        tree_sha256 = self.hash_service.sha256(manifest_bytes)
        manifest["tree_sha256"] = tree_sha256
        return manifest
