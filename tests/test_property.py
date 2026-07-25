"""
Property-based tests using Hypothesis for domain invariants.
"""
import unittest

from hypothesis import given, settings
from hypothesis import strategies as st

from cida.domain.models import TokenMetrics
from cida.domain.metrics import is_optimization_beneficial
from cida.domain.policies import normalize_path, is_binary_extension
from cida.infrastructure.hashing import HashService


# Shared strategies
non_neg_int = st.integers(min_value=0, max_value=10**9)


class TestTokenMetricsProperties(unittest.TestCase):
    """Property-based tests for TokenMetrics arithmetic invariants."""

    @given(original=non_neg_int, minified=non_neg_int,
           sidecar=non_neg_int, auxiliary=non_neg_int)
    @settings(max_examples=200)
    def test_gross_savings_identity(self, original, minified, sidecar, auxiliary):
        m = TokenMetrics(original=original, minified=minified,
                         sidecar=sidecar, auxiliary=auxiliary)
        self.assertEqual(m.gross_savings, original - minified)

    @given(original=non_neg_int, minified=non_neg_int,
           sidecar=non_neg_int, auxiliary=non_neg_int)
    @settings(max_examples=200)
    def test_overhead_identity(self, original, minified, sidecar, auxiliary):
        m = TokenMetrics(original=original, minified=minified,
                         sidecar=sidecar, auxiliary=auxiliary)
        self.assertEqual(m.overhead, sidecar + auxiliary)

    @given(original=non_neg_int, minified=non_neg_int,
           sidecar=non_neg_int, auxiliary=non_neg_int)
    @settings(max_examples=200)
    def test_net_savings_identity(self, original, minified, sidecar, auxiliary):
        m = TokenMetrics(original=original, minified=minified,
                         sidecar=sidecar, auxiliary=auxiliary)
        self.assertEqual(m.net_savings, m.gross_savings - m.overhead)

    @given(original=st.integers(min_value=1, max_value=10**9),
           minified=non_neg_int, sidecar=non_neg_int, auxiliary=non_neg_int)
    @settings(max_examples=200)
    def test_percentage_valid_when_positive_original(self, original, minified,
                                                      sidecar, auxiliary):
        m = TokenMetrics(original=original, minified=minified,
                         sidecar=sidecar, auxiliary=auxiliary)
        expected = (m.net_savings / original) * 100.0
        self.assertAlmostEqual(m.net_savings_percentage, expected, places=6)

    @given(minified=non_neg_int, sidecar=non_neg_int, auxiliary=non_neg_int)
    @settings(max_examples=50)
    def test_percentage_zero_when_original_zero(self, minified, sidecar, auxiliary):
        m = TokenMetrics(original=0, minified=minified,
                         sidecar=sidecar, auxiliary=auxiliary)
        self.assertEqual(m.net_savings_percentage, 0.0)


class TestIsOptimizationBeneficialProperties(unittest.TestCase):
    """Property: beneficial iff (original - minified) - (sidecar + auxiliary) > 0."""

    @given(original=non_neg_int, minified=non_neg_int,
           sidecar=non_neg_int, auxiliary=non_neg_int)
    @settings(max_examples=500)
    def test_equivalent_to_definition(self, original, minified, sidecar, auxiliary):
        result = is_optimization_beneficial(original, minified, sidecar, auxiliary)
        net = (original - minified) - (sidecar + auxiliary)
        self.assertEqual(result, net > 0)


class TestNormalizePathProperties(unittest.TestCase):
    """Property-based tests for normalize_path."""

    @given(st.text(max_size=200))
    @settings(max_examples=300)
    def test_no_backslashes_in_output(self, path):
        result = normalize_path(path)
        self.assertNotIn("\\", result)

    @given(st.text(max_size=200))
    @settings(max_examples=300)
    def test_idempotent(self, path):
        once = normalize_path(path)
        twice = normalize_path(once)
        self.assertEqual(once, twice)

    @given(st.text(
        alphabet=st.sampled_from(list("abcdefghijklmnopqrstuvwxyz0123456789./_")),
        max_size=100
    ))
    @settings(max_examples=100)
    def test_forward_slash_paths_unchanged(self, path):
        """Paths with only forward slashes are not modified."""
        self.assertEqual(normalize_path(path), path)


class TestIsBinaryExtensionProperties(unittest.TestCase):
    """Property-based tests for is_binary_extension."""

    KNOWN_BINARY = ["png", "jpg", "jpeg", "gif", "zip", "pdf",
                    "exe", "dll", "class", "jar", "db", "pyc"]

    @given(st.sampled_from(KNOWN_BINARY))
    def test_known_extensions_return_true(self, ext):
        self.assertTrue(is_binary_extension(f"file.{ext}"))

    def test_empty_always_false(self):
        self.assertFalse(is_binary_extension(""))

    @given(st.text(
        alphabet=st.sampled_from(list("abcdefghijklmnopqrstuvwxyz")),
        min_size=1, max_size=10
    ).filter(lambda s: s not in TestIsBinaryExtensionProperties.KNOWN_BINARY))
    @settings(max_examples=100)
    def test_non_binary_extensions_return_false(self, ext):
        self.assertFalse(is_binary_extension(f"file.{ext}"))


class TestHashServiceProperties(unittest.TestCase):
    """Property-based tests for HashService determinism and format."""

    def setUp(self):
        self.svc = HashService()

    @given(st.binary(max_size=1000))
    @settings(max_examples=200)
    def test_sha256_deterministic(self, data):
        self.assertEqual(self.svc.sha256(data), self.svc.sha256(data))

    @given(st.binary(max_size=1000))
    @settings(max_examples=200)
    def test_sha1_deterministic(self, data):
        self.assertEqual(self.svc.sha1(data), self.svc.sha1(data))

    @given(st.binary(max_size=1000))
    @settings(max_examples=200)
    def test_sha256_length_64(self, data):
        result = self.svc.sha256(data)
        self.assertEqual(len(result), 64)

    @given(st.binary(max_size=1000))
    @settings(max_examples=200)
    def test_sha1_length_40(self, data):
        result = self.svc.sha1(data)
        self.assertEqual(len(result), 40)

    @given(st.binary(max_size=1000))
    @settings(max_examples=200)
    def test_sha256_all_hex(self, data):
        result = self.svc.sha256(data)
        int(result, 16)  # raises if not valid hex

    @given(st.binary(max_size=1000))
    @settings(max_examples=200)
    def test_sha1_all_hex(self, data):
        result = self.svc.sha1(data)
        int(result, 16)


if __name__ == "__main__":
    unittest.main()
