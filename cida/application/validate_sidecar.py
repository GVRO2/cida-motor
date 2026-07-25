import os
from typing import Optional
from cida.application.ports import FileRepository, JsonCodec, HashService
from cida.domain.sidecar import (
    validate_sidecar,
    validate_sidecar_schema,
    validate_sidecar_ref,
    parse_compressed_envelope,
    reconcile_envelope_and_sidecar,
)
from cida.domain.errors import SidecarValidationError


def validate_sidecar_ref_physical(
    compressed_file: str,
    sidecar_ref: str,
    output_root: str,
) -> str:
    """Validate *sidecar_ref* using physical path resolution.

    Checks (application-layer; uses os — forbidden in domain):
    - Not an absolute path or traversal (via validate_sidecar_ref)
    - Resolved path contained within the compressed file's parent directory
    - Resolved path contained within *output_root*

    Returns the validated *sidecar_ref* unchanged on success.
    Raises SidecarValidationError on any violation.
    """
    # Lexical checks first (fast, no I/O — delegates to domain)
    validate_sidecar_ref(sidecar_ref)

    parent_real = os.path.normcase(
        os.path.realpath(os.path.dirname(os.path.abspath(compressed_file)))
    )
    candidate_real = os.path.normcase(
        os.path.realpath(os.path.join(parent_real, sidecar_ref))
    )
    root_real = os.path.normcase(
        os.path.realpath(os.path.abspath(output_root))
    )

    try:
        if os.path.commonpath([parent_real, candidate_real]) != parent_real:
            raise SidecarValidationError(
                f"sidecar_ref '{sidecar_ref}' resolves outside the compressed file's "
                f"parent directory: {candidate_real}"
            )
    except ValueError as exc:
        raise SidecarValidationError(
            f"sidecar_ref '{sidecar_ref}' path comparison failed: {exc}"
        ) from exc

    try:
        if os.path.commonpath([root_real, candidate_real]) != root_real:
            raise SidecarValidationError(
                f"sidecar_ref '{sidecar_ref}' resolves outside the output root: "
                f"{candidate_real}"
            )
    except ValueError as exc:
        raise SidecarValidationError(
            f"sidecar_ref '{sidecar_ref}' path comparison failed: {exc}"
        ) from exc

    return sidecar_ref



def _assert_contained(candidate_real: str, root_real: str, label: str) -> None:
    """Raise SidecarValidationError if *candidate_real* is not under *root_real*."""
    try:
        common = os.path.commonpath([root_real, candidate_real])
    except ValueError as exc:
        raise SidecarValidationError(
            f"{label} path comparison failed (different drives?): {exc}"
        ) from exc
    if common != root_real:
        raise SidecarValidationError(
            f"{label} '{candidate_real}' is outside its expected root '{root_real}'"
        )


def _resolve_declared_source(root_real: str, source_ref: str, label: str) -> str:
    """Return the real path for a relative sidecar source inside *root_real*."""
    if not isinstance(source_ref, str) or not source_ref:
        raise SidecarValidationError(f"{label} is missing or invalid")
    if (
        os.path.isabs(source_ref)
        or source_ref.startswith(("/", "\\"))
        or source_ref.startswith("//")
        or source_ref.startswith("\\\\")
        or (len(source_ref) > 1 and source_ref[1] == ":")
    ):
        raise SidecarValidationError(f"{label} must be a relative path: {source_ref}")
    if "\\" in source_ref:
        raise SidecarValidationError(f"{label} must use '/' separators: {source_ref}")

    parts = source_ref.split("/")
    if ".." in parts or "..." in parts:
        raise SidecarValidationError(f"{label} traversal rejected: {source_ref}")

    candidate = os.path.join(root_real, source_ref)
    candidate_real = os.path.normcase(os.path.realpath(candidate))
    _assert_contained(candidate_real, root_real, label)
    return candidate_real


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
                        source_field = data["source"]

                        # ── Traversal / absolute-path guard ──────────────────
                        # Validate the 'source' field before constructing any path.
                        src_dir = (
                            self.file_repo.dirname(src_abs)
                            if self.file_repo.is_file(src_abs)
                            else src_abs
                        )
                        src_dir_real = os.path.normcase(os.path.realpath(
                            self.file_repo.abspath(src_dir)
                        ))

                        orig_file_path = _resolve_declared_source(
                            src_dir_real,
                            source_field,
                            f"Sidecar source '{source_field}'",
                        )
                        if not self.file_repo.exists(orig_file_path):
                            raise SidecarValidationError(
                                f"Orphan sidecar detected: source file '{source_field}' "
                                f"does not exist in '{src_abs}'"
                            )
                        orig_bytes = self.file_repo.read_bytes(orig_file_path)
                        validate_sidecar(data, source_field, orig_bytes, self.hash_service)
                except Exception as e:
                    if isinstance(e, SidecarValidationError):
                        raise
                    raise SidecarValidationError(
                        f"Sidecar validation failed for {self.file_repo.basename(f_path)}: {e}"
                    ) from e

    def validate_output_bundle(
        self,
        source_root: str,
        output_root: str,
        output_file: str,
        sidecar_file: Optional[str] = None,
        manifest: Optional[dict] = None,
        preloaded_source_bytes: Optional[bytes] = None,
        preloaded_output_bytes: Optional[bytes] = None,
    ) -> None:
        # ── Resolve all roots using realpath (not abspath) ─────────────────
        src_root_real = os.path.normcase(os.path.realpath(
            self.file_repo.dirname(source_root)
            if self.file_repo.is_file(source_root)
            else self.file_repo.abspath(source_root)
        ))
        out_root_real = os.path.normcase(os.path.realpath(
            self.file_repo.dirname(output_root)
            if self.file_repo.is_file(output_root)
            else self.file_repo.abspath(output_root)
        ))
        out_file_real = os.path.normcase(os.path.realpath(self.file_repo.abspath(output_file)))

        if not self.file_repo.exists(output_file):
            raise SidecarValidationError(f"Output file does not exist: {output_file}")

        # Output file must be inside the output root (symlink-aware).
        _assert_contained(out_file_real, out_root_real, "Output file")

        content_bytes = preloaded_output_bytes if preloaded_output_bytes is not None else self.file_repo.read_bytes(output_file)
        try:
            content_text = content_bytes.decode('utf-8')
        except UnicodeDecodeError as e:
            from cida.domain.errors import EncodingValidationError
            raise EncodingValidationError(
                f"Invalid UTF-8 encoding in output file {output_file}: {e}"
            ) from e

        envelope_meta, payload = parse_compressed_envelope(content_text)

        if envelope_meta and envelope_meta.get("sidecar_required"):
            if not sidecar_file:
                ref = envelope_meta.get("sidecar_ref", self.file_repo.basename(output_file) + ".cidatkn")
                from cida.domain.sidecar import validate_sidecar_ref
                validate_sidecar_ref(ref)
                sidecar_file = self.file_repo.join(self.file_repo.dirname(output_file), ref)

            if not self.file_repo.exists(sidecar_file):
                raise SidecarValidationError(f"Required sidecar file does not exist: {sidecar_file}")

            # Sidecar must be inside the output root (symlink-aware).
            sidecar_real = os.path.normcase(os.path.realpath(self.file_repo.abspath(sidecar_file)))
            _assert_contained(sidecar_real, out_root_real, "Sidecar file")

            sidecar_raw = self.file_repo.read_text(sidecar_file)
            sidecar_data = self.json_codec.decode(sidecar_raw)
            validate_sidecar_schema(sidecar_data)

            # Physical-path reconciliation (realpath, not just basename).
            parent_real = os.path.normcase(os.path.realpath(os.path.dirname(os.path.abspath(output_file))))
            declared_ref = envelope_meta.get("sidecar_ref", "")
            resolved_declared = os.path.normcase(os.path.realpath(os.path.join(parent_real, declared_ref))) if declared_ref else ""
            resolved_actual = os.path.normcase(os.path.realpath(os.path.abspath(sidecar_file)))

            reconcile_envelope_and_sidecar(
                envelope_meta,
                sidecar_data,
                actual_sidecar_filename=sidecar_file,
                resolved_declared_ref=resolved_declared,
                resolved_actual_sidecar=resolved_actual,
            )

            source_rel = sidecar_data.get("source")
            if isinstance(source_rel, str) and source_rel != "corpus":
                src_path_real = _resolve_declared_source(
                    src_root_real,
                    source_rel,
                    f"Source path '{source_rel}'",
                )

                if preloaded_source_bytes is not None:
                    orig_bytes = preloaded_source_bytes
                else:
                    if not self.file_repo.exists(src_path_real):
                        raise SidecarValidationError(
                            f"Source file specified in sidecar does not exist: {src_path_real}"
                        )
                    orig_bytes = self.file_repo.read_bytes(src_path_real)
                validate_sidecar(sidecar_data, source_rel, orig_bytes, self.hash_service)
