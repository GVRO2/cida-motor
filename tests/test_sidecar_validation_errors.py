import pytest
from cida.domain.errors import SidecarValidationError
from cida.domain.sidecar import create_sidecar_data, validate_sidecar_schema, validate_sidecar
from cida.infrastructure.hashing import HashService

def test_sidecar_create_errors():
    hs = HashService()

    with pytest.raises(SidecarValidationError, match="Entries must be a dictionary"):
        create_sidecar_data("file.md", b"content", "not_a_dict", hs)

    with pytest.raises(SidecarValidationError, match="Alias and value must be strings"):
        create_sidecar_data("file.md", b"content", {123: "val"}, hs)
    with pytest.raises(SidecarValidationError, match="Alias and value must be strings"):
        create_sidecar_data("file.md", b"content", {"alias": 123}, hs)

    with pytest.raises(SidecarValidationError, match="Alias and value must not be empty"):
        create_sidecar_data("file.md", b"content", {"": "val"}, hs)
    with pytest.raises(SidecarValidationError, match="Alias and value must not be empty"):
        create_sidecar_data("file.md", b"content", {"alias": "   "}, hs)

    with pytest.raises(SidecarValidationError, match="Duplicate value in entries"):
        create_sidecar_data("file.md", b"content", {"A": "val", "B": "val"}, hs)

def test_sidecar_validate_schema_errors():
    with pytest.raises(SidecarValidationError, match="Sidecar must be a JSON object"):
        validate_sidecar_schema("not_a_dict")

    with pytest.raises(SidecarValidationError, match="Missing required key"):
        validate_sidecar_schema({"format": "cida-token-sidecar"})

    base = {
        "format": "wrong-format",
        "version": 1,
        "source": "file.md",
        "source_sha256": "a" * 64,
        "entries": {}
    }
    with pytest.raises(SidecarValidationError, match="Unsupported format"):
        validate_sidecar_schema(base)

    base["format"] = "cida-token-sidecar"
    base["version"] = 2
    with pytest.raises(SidecarValidationError, match="Unsupported version"):
        validate_sidecar_schema(base)

    base["version"] = 1
    base["entries"] = ["list"]
    with pytest.raises(SidecarValidationError, match="entries must be a dictionary"):
        validate_sidecar_schema(base)

    base["entries"] = {123: "val"}
    with pytest.raises(SidecarValidationError, match="Alias and value must be strings"):
        validate_sidecar_schema(base)

    base["entries"] = {"": "val"}
    with pytest.raises(SidecarValidationError, match="Alias and value must not be empty"):
        validate_sidecar_schema(base)

    base["entries"] = {"A": "val", "B": "val"}
    with pytest.raises(SidecarValidationError, match="Duplicate value detected"):
        validate_sidecar_schema(base)

def test_validate_sidecar_full_errors():
    hs = HashService()
    content = b"Hello world text"
    valid_sha = hs.sha256(content)
    sidecar_data = {
        "format": "cida-token-sidecar",
        "version": 1,
        "source": "sub/file.md",
        "source_sha256": valid_sha,
        "entries": {"XY": "something"}
    }

    with pytest.raises(SidecarValidationError, match="Source path mismatch"):
        validate_sidecar(sidecar_data, "other/file.md", content, hs)

    sidecar_data["source"] = "sub/file.md"
    sidecar_data["source_sha256"] = "invalid_sha"
    with pytest.raises(SidecarValidationError, match="SHA-256 is missing"):
        validate_sidecar(sidecar_data, "sub/file.md", content, hs)

    sidecar_data["source_sha256"] = "a" * 64
    with pytest.raises(SidecarValidationError, match="SHA-256 mismatch"):
        validate_sidecar(sidecar_data, "sub/file.md", content, hs)

    sidecar_data["source_sha256"] = valid_sha
    sidecar_data["entries"] = {"world": "something"}
    with pytest.raises(SidecarValidationError, match="collides with content word"):
        validate_sidecar(sidecar_data, "sub/file.md", content, hs)
