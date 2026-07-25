from typing import Optional

from cida.application.ports import FileRepository, JsonCodec, HashService
from cida.application.validate_sidecar import SidecarValidatorUsecase


class StrictBundleAuditor:
    """Post-write auditing operations reserved exclusively for strict validation mode."""

    def __init__(self, file_repo: FileRepository, json_codec: JsonCodec, hash_service: HashService):
        self._sidecar_validator = SidecarValidatorUsecase(file_repo, json_codec, hash_service)

    def audit_destination_sidecars(self, src_abs: str, dst_abs: str) -> None:
        """Audit destination sidecars for malformed schemas and orphan source references."""
        self._sidecar_validator.verify_destination_sidecars(src_abs, dst_abs)

    def audit_output_bundle(
        self,
        source_root: str,
        output_root: str,
        output_file: str,
        sidecar_file: Optional[str] = None,
    ) -> None:
        """Re-read persisted output and sidecar, then validate the physical bundle."""
        self._sidecar_validator.validate_output_bundle(
            source_root,
            output_root,
            output_file,
            sidecar_file=sidecar_file,
        )
