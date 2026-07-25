import re
from typing import Any
from cida.domain.errors import SidecarValidationError

ENVELOPE_START = "<!-- CIDA_COMPRESSED_FORMAT"
ENVELOPE_END = "-->"

def create_compressed_envelope(payload: str, sidecar_ref: str, source_sha256: str, mode: str = "lossless", strategy: str = "dictionary") -> str:
    header = (
        f"<!-- CIDA_COMPRESSED_FORMAT\n"
        f"version: 1\n"
        f"mode: {mode}\n"
        f"sidecar_required: true\n"
        f"sidecar_ref: {sidecar_ref}\n"
        f"source_sha256: {source_sha256}\n"
        f"compression_strategy: {strategy}\n"
        f"-->\n"
    )
    return header + payload

def validate_sidecar_ref(sidecar_ref: str) -> str:
    if not sidecar_ref or not isinstance(sidecar_ref, str):
        raise SidecarValidationError("Invalid or missing sidecar_ref")
    if sidecar_ref.startswith('/') or sidecar_ref.startswith('\\') or (len(sidecar_ref) > 1 and sidecar_ref[1] == ':'):
        raise SidecarValidationError(f"Absolute or UNC path in sidecar_ref rejected: {sidecar_ref}")
    if '\\' in sidecar_ref:
        raise SidecarValidationError(f"Invalid path separators in sidecar_ref: {sidecar_ref}")
    parts = sidecar_ref.split('/')
    if ".." in parts or "..." in parts:
        raise SidecarValidationError(f"Directory traversal in sidecar_ref rejected: {sidecar_ref}")
    return sidecar_ref

def parse_compressed_envelope(content: str) -> tuple[dict[str, Any] | None, str]:
    if not content.startswith(ENVELOPE_START):
        return None, content

    end_idx = content.find(ENVELOPE_END)
    if end_idx == -1:
        return None, content

    header_block = content[len(ENVELOPE_START):end_idx].strip()
    after_end = end_idx + len(ENVELOPE_END)

    if content[after_end:after_end+1] == '\n':
        payload = content[after_end+1:]
    elif content[after_end:after_end+2] == '\r\n':
        payload = content[after_end+2:]
    else:
        payload = content[after_end:]

    metadata: dict[str, Any] = {}
    seen_keys = set()
    allowed_keys = {"version", "mode", "sidecar_required", "sidecar_ref", "source_sha256", "compression_strategy"}

    for line in header_block.splitlines():
        line = line.strip()
        if not line:
            continue
        if ':' not in line:
            raise SidecarValidationError(f"Malformed envelope header line: {line}")
        k, v = line.split(':', 1)
        key = k.strip()
        val = v.strip()
        if key in seen_keys:
            raise SidecarValidationError(f"Duplicate key in envelope header: {key}")
        if key not in allowed_keys:
            raise SidecarValidationError(f"Unknown key in envelope header: {key}")
        seen_keys.add(key)
        metadata[key] = val

    required_header_keys = {"version", "mode", "sidecar_required", "sidecar_ref", "source_sha256", "compression_strategy"}
    missing = required_header_keys - seen_keys
    if missing:
        raise SidecarValidationError(f"Missing required envelope keys: {missing}")

    try:
        metadata["version"] = int(metadata["version"])
    except (ValueError, TypeError):
        raise SidecarValidationError(f"Envelope version must be an integer: {metadata.get('version')}")

    if metadata["sidecar_required"] not in ("true", "false", "True", "False", "1", "0"):
        raise SidecarValidationError(f"Invalid sidecar_required value: {metadata['sidecar_required']}")
    metadata["sidecar_required"] = metadata["sidecar_required"].lower() in ("true", "1")

    sha = metadata["source_sha256"]
    if not sha or len(sha) != 64 or not all(c in '0123456789abcdefABCDEF' for c in sha):
        raise SidecarValidationError(f"Envelope source_sha256 is malformed: {sha}")

    if metadata["mode"] not in ("lossless", "semantic"):
        raise SidecarValidationError(f"Invalid envelope mode: {metadata['mode']}")

    if metadata["compression_strategy"] not in ("dictionary", "bmad", "none"):
        raise SidecarValidationError(f"Invalid envelope compression_strategy: {metadata['compression_strategy']}")

    validate_sidecar_ref(metadata["sidecar_ref"])

    return metadata, payload

def reconcile_envelope_and_sidecar(
    envelope_meta: dict,
    sidecar_data: dict,
    actual_sidecar_filename: str = "",
    compressed_file: str = "",
    # Pre-resolved physical paths (supplied by the application layer).
    # When provided, physical path comparison is used instead of basename.
    resolved_declared_ref: str = "",
    resolved_actual_sidecar: str = "",
) -> None:
    """Reconcile envelope metadata with sidecar data.

    The application layer is responsible for resolving physical paths via
    `os.path.realpath` and passing *resolved_declared_ref* and
    *resolved_actual_sidecar*. The domain layer stays pure (no I/O, no `os`).
    """
    if envelope_meta.get("version") != sidecar_data.get("version"):
        raise SidecarValidationError(
            f"Envelope version ({envelope_meta.get('version')}) disagrees with sidecar version ({sidecar_data.get('version')})"
        )

    env_sha = str(envelope_meta.get("source_sha256", "")).lower()
    side_sha = str(sidecar_data.get("source_sha256", "")).lower()
    if env_sha != side_sha:
        raise SidecarValidationError(
            f"Envelope source_sha256 ({env_sha}) disagrees with sidecar source_sha256 ({side_sha})"
        )

    if actual_sidecar_filename:
        declared_ref = envelope_meta.get("sidecar_ref", "")
        if resolved_declared_ref and resolved_actual_sidecar:
            # Physical path comparison supplied by application layer.
            if resolved_declared_ref != resolved_actual_sidecar:
                raise SidecarValidationError(
                    f"Declared sidecar_ref '{declared_ref}' (resolved: {resolved_declared_ref}) "
                    f"disagrees with loaded sidecar path '{actual_sidecar_filename}' "
                    f"(resolved: {resolved_actual_sidecar})"
                )
        else:
            # Fallback to basename comparison when resolved paths are not available.
            declared_name = declared_ref.replace('\\', '/').split('/')[-1]
            actual_name = actual_sidecar_filename.replace('\\', '/').split('/')[-1]
            if declared_name != actual_name:
                raise SidecarValidationError(
                    f"Declared sidecar_ref '{declared_name}' disagrees with loaded sidecar '{actual_name}'"
                )

# NOTE: validate_sidecar_ref_physical is defined in
# cida.application.validate_sidecar (application layer) because it uses `os`
# for physical path resolution, which is forbidden in the domain layer.


def create_sidecar_data(source_name: str, original_content: bytes, entries: dict, hash_service, precomputed_sha256: str = "") -> dict:
    if not isinstance(entries, dict):
        raise SidecarValidationError("Entries must be a dictionary")

    aliases = set()
    values = set()
    for alias, val in entries.items():
        if not isinstance(alias, str) or not isinstance(val, str):
            raise SidecarValidationError("Alias and value must be strings")
        if not alias.strip() or not val.strip():
            raise SidecarValidationError("Alias and value must not be empty")
        if alias in aliases:
            raise SidecarValidationError(f"Duplicate alias in entries: {alias}")
        if val in values:
            raise SidecarValidationError(f"Duplicate value in entries: {val}")
        aliases.add(alias)
        values.add(val)

    sorted_entries = {}
    for alias in sorted(entries.keys()):
        sorted_entries[alias] = entries[alias]

    source_rel = source_name.replace('\\', '/')
    sha = precomputed_sha256 if precomputed_sha256 else hash_service.sha256(original_content)
    return {
        "format": "cida-token-sidecar",
        "version": 1,
        "source": source_rel,
        "source_sha256": sha,
        "entries": sorted_entries
    }

def validate_sidecar_schema(data: dict):
    if not isinstance(data, dict):
        raise SidecarValidationError("Sidecar must be a JSON object")

    required_keys = ["format", "version", "source", "source_sha256", "entries"]
    for k in required_keys:
        if k not in data:
            raise SidecarValidationError(f"Missing required key: {k}")

    if data["format"] != "cida-token-sidecar":
        raise SidecarValidationError(f"Unsupported format: {data['format']}")

    if data["version"] != 1:
        raise SidecarValidationError(f"Unsupported version: {data['version']}")

    if not isinstance(data["entries"], dict):
        raise SidecarValidationError("entries must be a dictionary")

    aliases = set()
    values = set()
    for alias, val in data["entries"].items():
        if not isinstance(alias, str) or not isinstance(val, str):
            raise SidecarValidationError("Alias and value must be strings")
        if not alias.strip() or not val.strip():
            raise SidecarValidationError("Alias and value must not be empty")
        if alias in aliases:
            raise SidecarValidationError(f"Duplicate alias detected: {alias}")
        if val in values:
            raise SidecarValidationError(f"Duplicate value detected: {val}")
        aliases.add(alias)
        values.add(val)

def validate_sidecar(data: dict, expected_rel_path: str, original_bytes: bytes, hash_service):
    validate_sidecar_schema(data)

    src_norm = data["source"].replace('\\', '/')
    exp_norm = expected_rel_path.replace('\\', '/')
    if src_norm != exp_norm:
        raise SidecarValidationError(f"Source path mismatch: expected '{exp_norm}', got '{src_norm}'")

    sha = data["source_sha256"]
    if not sha or len(sha) != 64 or not all(c in '0123456789abcdefABCDEF' for c in sha):
        raise SidecarValidationError(f"SHA-256 is missing, malformed or non-hexadecimal: {sha}")

    calculated_sha = hash_service.sha256(original_bytes)
    if sha.lower() != calculated_sha.lower():
        raise SidecarValidationError(f"SHA-256 mismatch: calculated '{calculated_sha}', got '{sha}'")

    try:
        original_text = original_bytes.decode('utf-8')
    except UnicodeDecodeError as e:
        from cida.domain.errors import EncodingValidationError
        raise EncodingValidationError(f"Invalid UTF-8 content in original file: {e}") from e
    original_words = set(re.findall(r'\b\w+\b', original_text))
    for alias in data["entries"].keys():
        if alias in original_words:
            raise SidecarValidationError(f"Alias '{alias}' collides with content word in original file")
