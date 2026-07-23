from cida.domain.sidecar import (
    validate_sidecar_schema, validate_sidecar as validate_sidecar_pure, create_sidecar_data as create_sidecar_data_pure,
)
from cida.domain.errors import SidecarValidationError
from cida.infrastructure.json_codec import JsonCodec
from cida.infrastructure.hashing import HashService
from cida.infrastructure.filesystem import PhysicalFilesystem

_json_codec = JsonCodec()
_hash_service = HashService()
_fs = PhysicalFilesystem()

__all__ = [
    "SidecarValidationError",
    "reject_duplicate_keys",
    "calculate_sha256",
    "create_sidecar_data",
    "read_sidecar",
    "write_sidecar",
    "validate_sidecar_schema",
    "validate_sidecar",
]

def reject_duplicate_keys(pairs):
    return _json_codec.reject_duplicate_keys(pairs)

def calculate_sha256(data):
    return _hash_service.sha256(data)

def create_sidecar_data(source_name, original_content, entries):
    return create_sidecar_data_pure(source_name, original_content, entries, _hash_service)

def read_sidecar(sidecar_path):
    data = _json_codec.decode(_fs.read_text(sidecar_path))
    validate_sidecar_schema(data)
    return data

def write_sidecar(sidecar_path, data):
    validate_sidecar_schema(data)
    _fs.write_text(sidecar_path, _json_codec.encode(data, indent=4))

def validate_sidecar(data, expected_rel_path, original_bytes):
    return validate_sidecar_pure(data, expected_rel_path, original_bytes, _hash_service)
