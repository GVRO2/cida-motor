import json

import pytest

from benchmarks.context_usage_compare import _build_tknc_corpus, _write_fixture_corpus
from cida.domain.errors import SidecarValidationError
from cida.infrastructure.tknc_context_session import ContextFilesystem
from harness.artifact_verifier import BundleRuntimeVerifier


@pytest.mark.parametrize(
    "artifact_type",
    [
        "content_output",
        "alias_index",
        "alias_chunk",
        "alias_segment",
        "content_search_index",
        "content_search_segment",
        "source_manifest",
    ],
)
def test_bundle_runtime_verifier_rejects_tampered_artifact_type(tmp_path, artifact_type: str):
    original, relpaths = _write_fixture_corpus(tmp_path, f"tamper-{artifact_type}", 500)
    tknc = tmp_path / f"tamper-{artifact_type}" / "tknc"
    _build_tknc_corpus(original, tknc, relpaths)
    manifest = json.loads((tknc / "tknd" / "bundle-manifest.json").read_text(encoding="utf-8"))
    entry = next(item for item in manifest["files"] if item["artifact_type"] == artifact_type)
    path = tknc / entry["path"]
    path.write_bytes(path.read_bytes() + b"\n# tampered\n")

    with pytest.raises(ValueError, match="mismatch"):
        BundleRuntimeVerifier(tknc).verify_artifact(path)

    with pytest.raises(SidecarValidationError, match="mismatch"):
        ContextFilesystem().read_bytes_limited(str(path), 5_000_000, operation="lookup", reason="tamper-test")
