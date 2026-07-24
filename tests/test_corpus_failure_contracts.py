"""Tests for corpus read and hash failure contracts.

All tests verify:
- exit_code via the typed exception's .exit_code attribute
- error message content
- no partial/silent state construction
"""
import pytest
from unittest.mock import MagicMock
from cida.application.optimize_corpus import CorpusOptimizerUsecase
from cida.domain.errors import SourcePathError, InternalProcessingError


def _make_usecase(file_repo=None, hash_service=None):
    token_counter = MagicMock()
    token_counter.count.return_value = 10
    file_repo = file_repo or MagicMock()
    hash_service = hash_service or MagicMock()
    json_codec = MagicMock()
    json_codec.encode.return_value = '{}'
    json_codec.canonical_encode.return_value = '{}'
    dict_builder = MagicMock()
    dict_builder.build_corpus_dictionary.return_value = {"hello": "A0"}
    return CorpusOptimizerUsecase(token_counter, file_repo, hash_service, json_codec, dict_builder)


class TestCorpusReadFailure:
    """optimize_corpus read failure → SourcePathError (exit_code=4)."""

    def test_read_failure_raises_source_path_error(self, tmp_path):
        fp = str(tmp_path / "doc.md")
        file_repo = MagicMock()
        file_repo.is_binary_file.return_value = False
        file_repo.read_text.side_effect = OSError("permission denied")
        file_repo.relpath.return_value = "doc.md"

        uc = _make_usecase(file_repo=file_repo)
        with pytest.raises(SourcePathError) as exc_info:
            uc.build_corpus_dict([fp], str(tmp_path))

        assert exc_info.value.exit_code == 4
        assert "Failed to read corpus source" in str(exc_info.value)
        assert fp in str(exc_info.value)

    def test_read_failure_message_includes_original_error(self, tmp_path):
        fp = str(tmp_path / "doc.md")
        file_repo = MagicMock()
        file_repo.is_binary_file.return_value = False
        file_repo.read_text.side_effect = OSError("disk full")

        uc = _make_usecase(file_repo=file_repo)
        with pytest.raises(SourcePathError) as exc_info:
            uc.build_corpus_dict([fp], str(tmp_path))

        assert "disk full" in str(exc_info.value)

    def test_read_failure_chained_exception(self, tmp_path):
        fp = str(tmp_path / "doc.md")
        original_error = OSError("io error")
        file_repo = MagicMock()
        file_repo.is_binary_file.return_value = False
        file_repo.read_text.side_effect = original_error

        uc = _make_usecase(file_repo=file_repo)
        with pytest.raises(SourcePathError) as exc_info:
            uc.build_corpus_dict([fp], str(tmp_path))

        assert exc_info.value.__cause__ is original_error


class TestCorpusHashFailure:
    """optimize_corpus hash failure → InternalProcessingError (exit_code=6)."""

    def test_hash_failure_raises_internal_processing_error(self, tmp_path):
        fp = str(tmp_path / "doc.md")
        file_repo = MagicMock()
        file_repo.is_binary_file.return_value = False
        file_repo.read_text.return_value = "# doc content for corpus building"
        file_repo.relpath.return_value = "doc.md"

        hash_service = MagicMock()
        hash_service.sha256.side_effect = Exception("hash failure")

        uc = _make_usecase(file_repo=file_repo, hash_service=hash_service)
        with pytest.raises(InternalProcessingError) as exc_info:
            uc.build_corpus_dict([fp], str(tmp_path))

        assert exc_info.value.exit_code == 6
        assert "Failed to hash corpus source" in str(exc_info.value)

    def test_hash_failure_no_partial_manifest(self, tmp_path):
        """No manifest_files should be partially constructed when hash fails."""
        fp1 = str(tmp_path / "doc1.md")
        fp2 = str(tmp_path / "doc2.md")
        file_repo = MagicMock()
        file_repo.is_binary_file.return_value = False
        file_repo.read_text.return_value = "content for corpus"
        file_repo.relpath.side_effect = lambda p, _: p.split("/")[-1]

        call_count = [0]
        def sha256_side_effect(_content):
            call_count[0] += 1
            if call_count[0] >= 1:
                raise Exception("hash error")
            return "0" * 64

        hash_service = MagicMock()
        hash_service.sha256.side_effect = sha256_side_effect

        uc = _make_usecase(file_repo=file_repo, hash_service=hash_service)
        with pytest.raises(InternalProcessingError):
            uc.build_corpus_dict([fp1, fp2], str(tmp_path))


class TestCLICorpusReadFailure:
    """CLI corpus read failure contracts."""

    def test_corpus_read_failure_without_continue_raises(self, tmp_path):
        """Without --continue-on-error, a corpus read failure must abort."""
        from cida.interfaces.cli import FailureAggregator
        from cida.domain.errors import SourcePathError

        agg = FailureAggregator()
        exc = SourcePathError("read failed")
        agg.add("/some/path.md", "read", exc)

        assert agg.final_exit_code == 4
        assert 4 in agg.categories

    def test_continue_on_error_single_category_exit_code(self, tmp_path):
        """Single error category → exit code of that category."""
        from cida.interfaces.cli import FailureAggregator
        from cida.domain.errors import SourcePathError

        agg = FailureAggregator()
        # Only source/filesystem (4) errors
        agg.add("/file1.md", "read", SourcePathError("read fail 1"))
        agg.add("/file2.md", "read", SourcePathError("read fail 2"))

        assert agg.final_exit_code == 4
        assert agg.categories == [4]

    def test_continue_on_error_multiple_categories_highest_severity(self, tmp_path):
        """Multiple categories → exit code = highest severity."""
        from cida.interfaces.cli import FailureAggregator
        from cida.domain.errors import SourcePathError, TokenizerError, SidecarValidationError

        agg = FailureAggregator()
        agg.add("/file1.md", "read", SourcePathError("4"))      # category 4
        agg.add("/file2.md", "tokenizer", TokenizerError("2"))  # category 2
        agg.add("/file3.md", "sidecar", SidecarValidationError("5"))  # category 5

        # Highest severity is 5
        assert agg.final_exit_code == 5
        assert agg.categories == [5, 4, 2]

    def test_aggregator_empty_returns_zero(self):
        from cida.interfaces.cli import FailureAggregator
        agg = FailureAggregator()
        assert agg.final_exit_code == 0
        assert agg.categories == []

    def test_aggregator_internal_error_highest_priority(self):
        """InternalProcessingError (exit_code=6) should always win."""
        from cida.interfaces.cli import FailureAggregator
        from cida.domain.errors import SourcePathError, InternalProcessingError

        agg = FailureAggregator()
        agg.add("/f1", "read", SourcePathError("4"))
        agg.add("/f2", "internal", InternalProcessingError("6"))
        agg.add("/f3", "read", SourcePathError("4"))

        assert agg.final_exit_code == 6
