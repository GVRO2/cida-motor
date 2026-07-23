import re
from cida.domain.errors import SidecarValidationError

def create_sidecar_data(source_name: str, original_content: bytes, entries: dict, hash_service) -> dict:
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
    return {
        "format": "cida-token-sidecar",
        "version": 1,
        "source": source_rel,
        "source_sha256": hash_service.sha256(original_content),
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
    """
    Fully validates the sidecar data against the expected relative path and original file bytes.
    """
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
