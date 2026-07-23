from cida.application.ports import FileRepository, JsonCodec, HashService
from cida.domain.sidecar import validate_sidecar, validate_sidecar_schema
from cida.domain.errors import SidecarValidationError

class SidecarValidatorUsecase:
    """Usecase to audit generated sidecar files."""

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
                        orig_file_path = self.file_repo.join(src_abs, data["source"])
                        if self.file_repo.exists(orig_file_path):
                            orig_bytes = self.file_repo.read_bytes(orig_file_path)
                            validate_sidecar(data, data["source"], orig_bytes, self.hash_service)
                except Exception as e:
                    if isinstance(e, SidecarValidationError):
                        raise
                    raise SidecarValidationError(f"Sidecar validation failed for {self.file_repo.basename(f_path)}: {e}") from e
