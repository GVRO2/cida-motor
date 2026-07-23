import re
from typing import Optional
from cida.domain.errors import SidecarValidationError, ReconstructionError, SourcePathError
from cida.domain.reconstruction import reconstruct_content
from cida.domain.sidecar import validate_sidecar_schema

class FileDecompressorUsecase:
    def __init__(self, file_repo, json_codec, hash_service=None):
        self.file_repo = file_repo
        self.json_codec = json_codec
        self.hash_service = hash_service

    def _has_compression_marker(self, text: str) -> bool:
        if re.search(r'>\s*🤖\s*AI RAG DICT:|AI RAG DICT:', text):
            return True
        return False

    def decompress(self, compressed_filepath: str, sidecar_filepath: Optional[str] = None) -> bytes:
        if not self.file_repo.exists(compressed_filepath):
            raise SourcePathError(f"Compressed file not found: {compressed_filepath}")

        raw_bytes = self.file_repo.read_bytes(compressed_filepath)
        try:
            compressed_text = raw_bytes.decode('utf-8')
        except UnicodeDecodeError as e:
            from cida.domain.errors import EncodingValidationError
            raise EncodingValidationError(f"Invalid UTF-8 encoding in compressed file: {e}") from e

        has_marker = self._has_compression_marker(compressed_text)

        if sidecar_filepath is None:
            sidecar_filepath = compressed_filepath + ".cidatkn"

        sidecar_exists = self.file_repo.exists(sidecar_filepath)

        if not sidecar_exists:
            if has_marker:
                raise SidecarValidationError(f"Missing required sidecar file '{sidecar_filepath}' for compressed file '{compressed_filepath}'")
            return raw_bytes

        try:
            sidecar_raw = self.file_repo.read_text(sidecar_filepath)
            sidecar_data = self.json_codec.decode(sidecar_raw)
        except Exception as e:
            if isinstance(e, SidecarValidationError):
                raise
            raise SidecarValidationError(f"Failed to read/decode sidecar file '{sidecar_filepath}': {e}") from e

        validate_sidecar_schema(sidecar_data)

        sidecar_source = sidecar_data.get("source")
        if not sidecar_source or not isinstance(sidecar_source, str):
            raise SidecarValidationError("Sidecar source field must be a non-empty string")

        source_norm = sidecar_source.replace('\\', '/')
        parts = source_norm.split('/')
        if ".." in parts or source_norm.startswith('/'):
            raise SidecarValidationError(f"Path traversal detected in sidecar source: {sidecar_source}")

        if source_norm != "corpus":
            expected_filename = self.file_repo.basename(compressed_filepath)
            expected_base = expected_filename[:-5] if expected_filename.endswith(".tknc") else expected_filename
            sidecar_source_base = self.file_repo.basename(source_norm)

            if sidecar_source_base != expected_filename and sidecar_source_base != expected_base:
                raise SidecarValidationError(
                    f"Sidecar source mismatch: sidecar specifies '{sidecar_source}', but compressed file is '{compressed_filepath}'"
                )

        reconstructed_text = reconstruct_content(compressed_text, sidecar_data)
        reconstructed_bytes = reconstructed_text.encode('utf-8')

        if "source_sha256" in sidecar_data and self.hash_service:
            expected_sha = sidecar_data["source_sha256"]
            actual_sha = self.hash_service.sha256(reconstructed_bytes)
            if expected_sha.lower() != actual_sha.lower():
                raise ReconstructionError(f"Reconstructed SHA256 mismatch: expected '{expected_sha}', got '{actual_sha}'")

        return reconstructed_bytes

    def decompress_to_file(self, compressed_filepath: str, output_filepath: str, sidecar_filepath: Optional[str] = None) -> bytes:
        reconstructed_bytes = self.decompress(compressed_filepath, sidecar_filepath)
        self.file_repo.write_bytes(output_filepath, reconstructed_bytes)
        return reconstructed_bytes
