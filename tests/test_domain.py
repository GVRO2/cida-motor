"""
Comprehensive unit tests for cida.domain (errors, models, metrics, policies).
"""
import unittest
from dataclasses import FrozenInstanceError

from cida.domain.errors import (
    CidaError, UsageError, TokenizerError, SemanticValidationError,
    SourcePathError, SidecarValidationError, InternalProcessingError,
)
from cida.domain.models import (
    TokenMetrics, Sidecar, OptimizationRequest, OptimizationResult,
    ManifestFile, CorpusManifest, SemanticValidationResult,
)
from cida.domain.metrics import is_optimization_beneficial
from cida.domain.policies import classify_comment, normalize_path, is_binary_extension


# ---------------------------------------------------------------------------
# 1. Error hierarchy
# ---------------------------------------------------------------------------
class TestErrorHierarchy(unittest.TestCase):
    """Verify exit codes, inheritance, and message preservation."""

    def test_all_errors_inherit_from_cida_error(self):
        for cls in (UsageError, TokenizerError, SemanticValidationError,
                    SourcePathError, SidecarValidationError, InternalProcessingError):
            self.assertTrue(issubclass(cls, CidaError), f"{cls.__name__} must inherit CidaError")

    def test_exit_codes(self):
        expected = {
            UsageError: 1,
            TokenizerError: 2,
            SemanticValidationError: 3,
            SourcePathError: 4,
            SidecarValidationError: 5,
            InternalProcessingError: 6,
        }
        for cls, code in expected.items():
            self.assertEqual(cls.exit_code, code, f"{cls.__name__}.exit_code should be {code}")

    def test_sidecar_validation_error_inherits_value_error(self):
        self.assertTrue(issubclass(SidecarValidationError, ValueError))

    def test_error_message_preserved(self):
        msg = "Something went wrong"
        for cls in (UsageError, TokenizerError, SemanticValidationError,
                    SourcePathError, SidecarValidationError, InternalProcessingError):
            err = cls(msg)
            self.assertEqual(str(err), msg)

    def test_cida_error_has_no_exit_code(self):
        self.assertFalse(hasattr(CidaError, "exit_code"))

    def test_errors_are_catchable_as_exception(self):
        with self.assertRaises(Exception):
            raise UsageError("test")


# ---------------------------------------------------------------------------
# 2. TokenMetrics model
# ---------------------------------------------------------------------------
class TestTokenMetrics(unittest.TestCase):

    def test_gross_savings(self):
        m = TokenMetrics(original=100, minified=60, sidecar=5, auxiliary=3)
        self.assertEqual(m.gross_savings, 40)

    def test_overhead(self):
        m = TokenMetrics(original=100, minified=60, sidecar=5, auxiliary=3)
        self.assertEqual(m.overhead, 8)

    def test_net_savings(self):
        m = TokenMetrics(original=100, minified=60, sidecar=5, auxiliary=3)
        self.assertEqual(m.net_savings, 32)

    def test_net_savings_percentage(self):
        m = TokenMetrics(original=200, minified=100, sidecar=10, auxiliary=10)
        self.assertAlmostEqual(m.net_savings_percentage, 40.0)

    def test_net_savings_percentage_zero_original(self):
        m = TokenMetrics(original=0, minified=0, sidecar=0, auxiliary=0)
        self.assertEqual(m.net_savings_percentage, 0.0)

    def test_negative_net_savings(self):
        m = TokenMetrics(original=100, minified=95, sidecar=10, auxiliary=5)
        self.assertLess(m.net_savings, 0)

    def test_frozen_immutability(self):
        m = TokenMetrics(original=10, minified=5, sidecar=1, auxiliary=1)
        with self.assertRaises(FrozenInstanceError):
            m.original = 999


# ---------------------------------------------------------------------------
# 3. Other dataclass models
# ---------------------------------------------------------------------------
class TestDataclassModels(unittest.TestCase):

    def test_sidecar_defaults(self):
        s = Sidecar(source="f.md", source_sha256="abc", entries={"A": "B"})
        self.assertEqual(s.format, "cida-token-sidecar")
        self.assertEqual(s.version, 1)

    def test_sidecar_frozen(self):
        s = Sidecar(source="f.md", source_sha256="abc", entries={})
        with self.assertRaises(FrozenInstanceError):
            s.source = "other.md"

    def test_optimization_request(self):
        r = OptimizationRequest(filepath="a.md", profile="markdown",
                                verify_semantics=True, fail_on_inflation=False,
                                dictionary_scope="file")
        self.assertEqual(r.profile, "markdown")
        self.assertTrue(r.verify_semantics)

    def test_optimization_result(self):
        m = TokenMetrics(original=10, minified=5, sidecar=1, auxiliary=0)
        r = OptimizationResult(filepath="a.md", profile="markdown", metrics=m,
                               accepted_transforms=["trim"], rejected_transforms=[],
                               semantic_status="SUCCESS", execution_time=0.5)
        self.assertIsNone(r.sidecar_data)

    def test_optimization_result_with_sidecar_data(self):
        m = TokenMetrics(original=10, minified=5, sidecar=1, auxiliary=0)
        r = OptimizationResult(filepath="a.md", profile="markdown", metrics=m,
                               accepted_transforms=[], rejected_transforms=[],
                               semantic_status="SUCCESS", execution_time=0.1,
                               sidecar_data={"key": "val"})
        self.assertEqual(r.sidecar_data, {"key": "val"})

    def test_manifest_file(self):
        mf = ManifestFile(path="f.md", sha256="abc123", size=512)
        self.assertEqual(mf.size, 512)

    def test_corpus_manifest(self):
        cm = CorpusManifest(schema_version=1, commit_sha="deadbeef",
                            platform="win32", files=[], tree_sha256="abc")
        self.assertEqual(cm.schema_version, 1)
        self.assertEqual(cm.files, [])

    def test_semantic_validation_result_valid(self):
        svr = SemanticValidationResult(is_valid=True, message="ok")
        self.assertTrue(svr.is_valid)

    def test_semantic_validation_result_invalid(self):
        svr = SemanticValidationResult(is_valid=False, message="mismatch")
        self.assertFalse(svr.is_valid)
        self.assertEqual(svr.message, "mismatch")


# ---------------------------------------------------------------------------
# 4. Metrics — is_optimization_beneficial
# ---------------------------------------------------------------------------
class TestIsOptimizationBeneficial(unittest.TestCase):

    def test_beneficial(self):
        self.assertTrue(is_optimization_beneficial(100, 50, 5, 5))

    def test_not_beneficial_zero_net(self):
        # gross=10, overhead=10 → net=0 → not beneficial
        self.assertFalse(is_optimization_beneficial(100, 90, 5, 5))

    def test_not_beneficial_negative_net(self):
        self.assertFalse(is_optimization_beneficial(100, 95, 10, 5))

    def test_all_zeros(self):
        self.assertFalse(is_optimization_beneficial(0, 0, 0, 0))

    def test_large_numbers(self):
        self.assertTrue(is_optimization_beneficial(10**9, 0, 0, 0))

    def test_marginal_benefit(self):
        # gross=1, overhead=0 → net=1 → beneficial
        self.assertTrue(is_optimization_beneficial(100, 99, 0, 0))

    def test_marginal_no_benefit(self):
        # gross=1, overhead=1 → net=0 → not beneficial
        self.assertFalse(is_optimization_beneficial(100, 99, 1, 0))


# ---------------------------------------------------------------------------
# 5. Policies — classify_comment
# ---------------------------------------------------------------------------
class TestClassifyComment(unittest.TestCase):

    def test_empty_comment_is_decorative(self):
        self.assertEqual(classify_comment("<!-- -->"), "decorative")

    def test_whitespace_comment_is_decorative(self):
        self.assertEqual(classify_comment("<!--    -->"), "decorative")

    def test_non_alphanumeric_is_decorative(self):
        self.assertEqual(classify_comment("<!-- === -->"), "decorative")
        self.assertEqual(classify_comment("<!-- --- -->"), "decorative")
        self.assertEqual(classify_comment("<!-- *** -->"), "decorative")

    def test_generated_comment(self):
        self.assertEqual(classify_comment("<!-- Generated by tool v1.2 -->"), "generated")
        self.assertEqual(classify_comment("<!-- GENERATED BY AI -->"), "generated")

    def test_operational_keyword_stepsCompleted(self):
        self.assertEqual(classify_comment("<!-- stepsCompleted: 3 -->"), "operational")

    def test_operational_keyword_workflowType(self):
        self.assertEqual(classify_comment("<!-- workflowType: build -->"), "operational")

    def test_operational_keyword_inputDocuments(self):
        self.assertEqual(classify_comment("<!-- inputDocuments: foo -->"), "operational")

    def test_operational_keyword_nextStepFile(self):
        self.assertEqual(classify_comment("<!-- nextStepFile: bar.md -->"), "operational")

    def test_operational_keyword_outputFile(self):
        self.assertEqual(classify_comment("<!-- outputFile: out.md -->"), "operational")

    def test_operational_json_object(self):
        self.assertEqual(classify_comment('<!-- {"key": "val"} -->'), "operational")

    def test_operational_json_array(self):
        self.assertEqual(classify_comment('<!-- [1, 2, 3] -->'), "operational")

    def test_operational_key_value_pattern(self):
        self.assertEqual(classify_comment("<!-- key: value -->"), "operational")
        self.assertEqual(classify_comment("<!-- key=value -->"), "operational")

    def test_unknown_general_text(self):
        self.assertEqual(classify_comment("<!-- hello world -->"), "unknown")

    def test_unknown_not_a_comment(self):
        self.assertEqual(classify_comment("not a comment"), "unknown")

    def test_invalid_json_inside_braces(self):
        # Starts/ends with braces but is not valid JSON — falls through to unknown
        result = classify_comment("<!-- {not: valid json} -->")
        self.assertEqual(result, "unknown")


# ---------------------------------------------------------------------------
# 6. Policies — normalize_path
# ---------------------------------------------------------------------------
class TestNormalizePath(unittest.TestCase):

    def test_backslash_to_forward(self):
        self.assertEqual(normalize_path("a\\b\\c"), "a/b/c")

    def test_already_forward(self):
        self.assertEqual(normalize_path("a/b/c"), "a/b/c")

    def test_mixed_slashes(self):
        self.assertEqual(normalize_path("a\\b/c\\d"), "a/b/c/d")

    def test_empty_string(self):
        self.assertEqual(normalize_path(""), "")

    def test_idempotent(self):
        p = "a\\b\\c"
        self.assertEqual(normalize_path(normalize_path(p)), normalize_path(p))


# ---------------------------------------------------------------------------
# 7. Policies — is_binary_extension
# ---------------------------------------------------------------------------
class TestIsBinaryExtension(unittest.TestCase):

    def test_known_binary_extensions(self):
        for ext in ("png", "jpg", "jpeg", "gif", "zip", "pdf", "exe", "dll",
                    "class", "jar", "db", "pyc"):
            self.assertTrue(is_binary_extension(f"file.{ext}"), f".{ext} should be binary")

    def test_case_insensitive(self):
        self.assertTrue(is_binary_extension("image.PNG"))
        self.assertTrue(is_binary_extension("image.Jpg"))

    def test_non_binary_extensions(self):
        for ext in ("md", "txt", "py", "java", "go", "html", "css", "js"):
            self.assertFalse(is_binary_extension(f"file.{ext}"), f".{ext} should not be binary")

    def test_no_extension(self):
        self.assertFalse(is_binary_extension("Makefile"))

    def test_empty_string(self):
        self.assertFalse(is_binary_extension(""))

    def test_dotfile(self):
        self.assertFalse(is_binary_extension(".gitignore"))


if __name__ == "__main__":
    unittest.main()
