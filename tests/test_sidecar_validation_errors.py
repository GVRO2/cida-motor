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
    base["version"] = 3
    with pytest.raises(SidecarValidationError, match="Unsupported version"):
        validate_sidecar_schema(base)

    base["version"] = 2
    with pytest.raises(SidecarValidationError, match="Missing required key: dictionary_id"):
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


def test_parse_compressed_envelope_byte_exactness():
    from cida.domain.sidecar import parse_compressed_envelope, create_compressed_envelope

    payload_with_leading_newline = "\n\nFirst line of content\nSecond line"
    sha = "a" * 64
    envelope = create_compressed_envelope(payload_with_leading_newline, "ref.cidatkn", sha)

    meta, parsed_payload = parse_compressed_envelope(envelope)
    assert meta["version"] == 1
    assert meta["mode"] == "lossless"
    assert meta["sidecar_ref"] == "ref.cidatkn"
    assert meta["source_sha256"] == sha
    assert meta["compression_strategy"] == "dictionary"
    assert parsed_payload == payload_with_leading_newline


def test_parse_compressed_envelope_validation_failures():
    from cida.domain.sidecar import parse_compressed_envelope

    # Duplicate key
    dup_header = "<!-- CIDA_COMPRESSED_FORMAT\nversion: 1\nversion: 2\nmode: lossless\nsidecar_required: true\nsidecar_ref: f.cidatkn\nsource_sha256: " + "a"*64 + "\ncompression_strategy: dictionary\n-->\nPayload"
    with pytest.raises(SidecarValidationError, match="Duplicate key"):
        parse_compressed_envelope(dup_header)

    # Unknown key
    unk_header = "<!-- CIDA_COMPRESSED_FORMAT\nversion: 1\nunknown_key: val\nmode: lossless\nsidecar_required: true\nsidecar_ref: f.cidatkn\nsource_sha256: " + "a"*64 + "\ncompression_strategy: dictionary\n-->\nPayload"
    with pytest.raises(SidecarValidationError, match="Unknown key"):
        parse_compressed_envelope(unk_header)

    # Non-integer version
    bad_ver = "<!-- CIDA_COMPRESSED_FORMAT\nversion: v1\nmode: lossless\nsidecar_required: true\nsidecar_ref: f.cidatkn\nsource_sha256: " + "a"*64 + "\ncompression_strategy: dictionary\n-->\nPayload"
    with pytest.raises(SidecarValidationError, match="version must be an integer"):
        parse_compressed_envelope(bad_ver)

    # Malformed SHA
    bad_sha = "<!-- CIDA_COMPRESSED_FORMAT\nversion: 1\nmode: lossless\nsidecar_required: true\nsidecar_ref: f.cidatkn\nsource_sha256: short\ncompression_strategy: dictionary\n-->\nPayload"
    with pytest.raises(SidecarValidationError, match="malformed"):
        parse_compressed_envelope(bad_sha)


def test_validate_sidecar_ref_traversal():
    from cida.domain.sidecar import validate_sidecar_ref

    assert validate_sidecar_ref("valid_sidecar.cidatkn") == "valid_sidecar.cidatkn"

    with pytest.raises(SidecarValidationError, match="Absolute or UNC"):
        validate_sidecar_ref("/abs/path.cidatkn")

    with pytest.raises(SidecarValidationError, match="Absolute or UNC"):
        validate_sidecar_ref("C:\\abs\\path.cidatkn")

    with pytest.raises(SidecarValidationError, match="Directory traversal"):
        validate_sidecar_ref("../outside.cidatkn")

    with pytest.raises(SidecarValidationError, match="Directory traversal"):
        validate_sidecar_ref("sub/../../outside.cidatkn")


def test_reconcile_envelope_and_sidecar():
    from cida.domain.sidecar import reconcile_envelope_and_sidecar

    env_meta = {
        "version": 1,
        "source_sha256": "a" * 64,
        "sidecar_ref": "file.md.cidatkn"
    }
    sidecar_data = {
        "version": 1,
        "source_sha256": "a" * 64
    }

    # Successful reconciliation
    reconcile_envelope_and_sidecar(env_meta, sidecar_data, "file.md.cidatkn")

    # Version mismatch
    with pytest.raises(SidecarValidationError, match="disagrees with sidecar version"):
        reconcile_envelope_and_sidecar(env_meta, {"version": 2, "source_sha256": "a" * 64}, "file.md.cidatkn")

    # SHA mismatch
    with pytest.raises(SidecarValidationError, match="disagrees with sidecar source_sha256"):
        reconcile_envelope_and_sidecar(env_meta, {"version": 1, "source_sha256": "b" * 64}, "file.md.cidatkn")

    # Ref mismatch
    with pytest.raises(SidecarValidationError, match="disagrees with loaded sidecar"):
        reconcile_envelope_and_sidecar(env_meta, sidecar_data, "other.cidatkn")
