import os
from typing import Optional
from cida.application.ports import FileRepository, JsonCodec, HashService
from cida.domain.sidecar import validate_sidecar, validate_sidecar_schema, parse_compressed_envelope
from cida.domain.errors import SidecarValidationError

class SidecarValidatorUsecase:
    """Usecase to audit generated sidecar files and bundle integrity."""

    def __init__(self, file_repo: FileRepository, json_codec: JsonCodec, hash_service: HashService):
        self.file_repo = file_repo
        self.json_codec = json_codec
        self.hash_service = hash_service

    def verify_destination_sidecars(self, src_abs: str, dst_abs: str) -> None:
        for f_path in self.file_repo.list_files(dst_abs):
            if f_path.endswith(".cidatkn"):
                try:
                    content = self.file_repo.read_text(f_path)
                    data = self.json_codec.decode(content)
                    validate_sidecar_schema(data)

                    if data.get("source") != "corpus":
                        src_dir = self.file_repo.dirname(src_abs) if self.file_repo.is_file(src_abs) else src_abs
                        orig_file_path = self.file_repo.join(src_dir, data["source"])
                        if not self.file_repo.exists(orig_file_path):
                            raise SidecarValidationError(
                                f"Orphan sidecar detected: source file '{data['source']}' does not exist in '{src_abs}'"
                            )
                        orig_bytes = self.file_repo.read_bytes(orig_file_path)
                        validate_sidecar(data, data["source"], orig_bytes, self.hash_service)
                except Exception as e:
                    if isinstance(e, SidecarValidationError):
                        raise
                    raise SidecarValidationError(f"Sidecar validation failed for {self.file_repo.basename(f_path)}: {e}") from e

    def validate_output_bundle(self, source_root: str, output_root: str, output_file: str,
                               sidecar_file: Optional[str] = None, manifest: Optional[dict] = None) -> None:
        src_root_abs = self.file_repo.dirname(source_root) if self.file_repo.is_file(source_root) else self.file_repo.abspath(source_root)
        out_root_abs = self.file_repo.dirname(output_root) if self.file_repo.is_file(output_root) else self.file_repo.abspath(output_root)
        out_file_abs = self.file_repo.abspath(output_file)

        if not self.file_repo.exists(out_file_abs):
            raise SidecarValidationError(f"Output file does not exist: {out_file_abs}")

        try:
            if os.path.commonpath([out_root_abs, out_file_abs]) != out_root_abs:
                raise SidecarValidationError(f"Output file is outside output root: {out_file_abs}")
        except ValueError:
            raise SidecarValidationError(f"Output file is outside output root: {out_file_abs}")

        content_bytes = self.file_repo.read_bytes(out_file_abs)
        try:
            content_text = content_bytes.decode('utf-8')
        except UnicodeDecodeError as e:
            from cida.domain.errors import EncodingValidationError
            raise EncodingValidationError(f"Invalid UTF-8 encoding in output file {out_file_abs}: {e}") from e

        envelope_meta, payload = parse_compressed_envelope(content_text)

        if envelope_meta and envelope_meta.get("sidecar_required"):
            if not sidecar_file:
                ref = envelope_meta.get("sidecar_ref", self.file_repo.basename(out_file_abs) + ".cidatkn")
                from cida.domain.sidecar import validate_sidecar_ref
                validate_sidecar_ref(ref)
                sidecar_file = self.file_repo.join(self.file_repo.dirname(out_file_abs), ref)

            if not self.file_repo.exists(sidecar_file):
                raise SidecarValidationError(f"Required sidecar file does not exist: {sidecar_file}")

            sidecar_raw = self.file_repo.read_text(sidecar_file)
            sidecar_data = self.json_codec.decode(sidecar_raw)
            validate_sidecar_schema(sidecar_data)

            from cida.domain.sidecar import reconcile_envelope_and_sidecar
            reconcile_envelope_and_sidecar(envelope_meta, sidecar_data, sidecar_file)

            source_rel = sidecar_data.get("source")
            if isinstance(source_rel, str) and source_rel != "corpus":
                src_path = self.file_repo.join(src_root_abs, source_rel)
                if not self.file_repo.exists(src_path):
                    raise SidecarValidationError(f"Source file specified in sidecar does not exist: {src_path}")
                try:
                    if os.path.commonpath([src_root_abs, self.file_repo.abspath(src_path)]) != src_root_abs:
                        raise SidecarValidationError(f"Source path is outside source root: {src_path}")
                except ValueError:
                    raise SidecarValidationError(f"Source path is outside source root: {src_path}")

                orig_bytes = self.file_repo.read_bytes(src_path)
                validate_sidecar(sidecar_data, source_rel, orig_bytes, self.hash_service)

