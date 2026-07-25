"""
Contract tests — verify infrastructure adapters satisfy application-layer Protocol ports.
"""
import os
import tempfile
import shutil
import unittest

from cida.infrastructure.filesystem import PhysicalFilesystem
from cida.infrastructure.tokenizer import OfflineTokenizer
from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec
from cida.infrastructure.yaml_codec import YamlCodec
from cida.domain.errors import SidecarValidationError, SemanticValidationError


# ---------------------------------------------------------------------------
# 1. HashService contract
# ---------------------------------------------------------------------------
class TestHashServiceContract(unittest.TestCase):
    """Infrastructure HashService must satisfy ports.HashService."""

    def setUp(self):
        self.svc = HashService()

    def test_has_sha256_method(self):
        self.assertTrue(callable(getattr(self.svc, "sha256", None)))

    def test_has_sha1_method(self):
        self.assertTrue(callable(getattr(self.svc, "sha1", None)))

    def test_sha256_returns_hex_string(self):
        result = self.svc.sha256(b"hello")
        self.assertIsInstance(result, str)
        self.assertEqual(len(result), 64)
        # all hex characters
        int(result, 16)

    def test_sha1_returns_hex_string(self):
        result = self.svc.sha1(b"hello")
        self.assertIsInstance(result, str)
        self.assertEqual(len(result), 40)
        int(result, 16)

    def test_sha256_deterministic(self):
        self.assertEqual(self.svc.sha256(b"test"), self.svc.sha256(b"test"))

    def test_sha1_deterministic(self):
        self.assertEqual(self.svc.sha1(b"test"), self.svc.sha1(b"test"))

    def test_sha256_different_inputs_different_outputs(self):
        self.assertNotEqual(self.svc.sha256(b"a"), self.svc.sha256(b"b"))

    def test_sha256_empty_input(self):
        result = self.svc.sha256(b"")
        self.assertEqual(len(result), 64)

    def test_sha256_accepts_str_coercion(self):
        """Implementation accepts str via auto-encode — matches actual usage."""
        result = self.svc.sha256("hello")
        self.assertEqual(len(result), 64)


# ---------------------------------------------------------------------------
# 2. TokenCounter contract
# ---------------------------------------------------------------------------
class TestTokenCounterContract(unittest.TestCase):
    """Infrastructure OfflineTokenizer must satisfy ports.TokenCounter."""

    def setUp(self):
        dir_path = os.path.dirname(os.path.abspath(__file__))
        os.environ["TIKTOKEN_CACHE_DIR"] = os.path.normpath(
            os.path.join(dir_path, "..", "resources")
        )
        self.counter = OfflineTokenizer()

    def test_has_count_method(self):
        self.assertTrue(callable(getattr(self.counter, "count", None)))

    def test_count_returns_int(self):
        result = self.counter.count("Hello world")
        self.assertIsInstance(result, int)

    def test_count_empty_string_returns_zero(self):
        self.assertEqual(self.counter.count(""), 0)

    def test_count_positive_for_text(self):
        self.assertGreater(self.counter.count("Hello world"), 0)

    def test_count_deterministic(self):
        text = "The quick brown fox jumps over the lazy dog"
        self.assertEqual(self.counter.count(text), self.counter.count(text))


# ---------------------------------------------------------------------------
# 3. JsonCodec contract
# ---------------------------------------------------------------------------
class TestJsonCodecContract(unittest.TestCase):
    """Infrastructure JsonCodec must satisfy ports.JsonCodec."""

    def setUp(self):
        self.codec = JsonCodec()

    def test_has_decode_method(self):
        self.assertTrue(callable(getattr(self.codec, "decode", None)))

    def test_has_encode_method(self):
        self.assertTrue(callable(getattr(self.codec, "encode", None)))

    def test_decode_returns_dict(self):
        result = self.codec.decode('{"a": 1}')
        self.assertIsInstance(result, dict)
        self.assertEqual(result["a"], 1)

    def test_encode_returns_string(self):
        result = self.codec.encode({"a": 1})
        self.assertIsInstance(result, str)
        self.assertIn('"a"', result)

    def test_roundtrip(self):
        data = {"key": "value", "num": 42, "list": [1, 2, 3]}
        encoded = self.codec.encode(data)
        decoded = self.codec.decode(encoded)
        self.assertEqual(decoded, data)

    def test_duplicate_key_rejection(self):
        with self.assertRaises(SidecarValidationError):
            self.codec.decode('{"a": 1, "a": 2}')

    def test_invalid_json_raises(self):
        with self.assertRaises(SidecarValidationError):
            self.codec.decode("not json at all")

    def test_encode_indent(self):
        result = self.codec.encode({"a": 1}, indent=2)
        self.assertIn("\n", result)

    def test_encode_unicode(self):
        result = self.codec.encode({"emoji": "🎉"})
        self.assertIn("🎉", result)


# ---------------------------------------------------------------------------
# 4. YamlCodec contract
# ---------------------------------------------------------------------------
class TestYamlCodecContract(unittest.TestCase):
    """Infrastructure YamlCodec must satisfy ports.YamlCodec."""

    def setUp(self):
        self.codec = YamlCodec()

    def test_has_decode_method(self):
        self.assertTrue(callable(getattr(self.codec, "decode", None)))

    def test_decode_returns_dict(self):
        result = self.codec.decode("key: value\nnum: 42\n")
        self.assertIsInstance(result, dict)
        self.assertEqual(result["key"], "value")
        self.assertEqual(result["num"], 42)

    def test_decode_empty_returns_empty_dict(self):
        result = self.codec.decode("")
        self.assertEqual(result, {})

    def test_decode_null_returns_empty_dict(self):
        result = self.codec.decode("---\n")
        self.assertEqual(result, {})

    def test_duplicate_key_raises(self):
        with self.assertRaises(SemanticValidationError):
            self.codec.decode("key: val1\nkey: val2\n")

    def test_non_dict_yaml_raises(self):
        with self.assertRaises(SemanticValidationError):
            self.codec.decode("- item1\n- item2\n")

    def test_keys_coerced_to_strings(self):
        result = self.codec.decode("123: value\n")
        self.assertIn("123", result)
        self.assertIsInstance(list(result.keys())[0], str)


# ---------------------------------------------------------------------------
# 5. FileRepository contract (PhysicalFilesystem)
# ---------------------------------------------------------------------------
class TestFileRepositoryContract(unittest.TestCase):
    """Infrastructure PhysicalFilesystem must satisfy ports.FileRepository."""

    def setUp(self):
        self.fs = PhysicalFilesystem()
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _path(self, name):
        return os.path.join(self.temp_dir, name)

    # --- method existence ---
    def test_has_all_protocol_methods(self):
        expected_methods = [
            "read_text", "read_bytes", "write_text", "write_bytes",
            "exists", "is_file", "is_dir", "makedirs", "copy", "remove",
            "is_binary_file", "list_files", "relpath", "abspath", "basename",
            "dirname", "join", "list_dir",
        ]
        for method_name in expected_methods:
            self.assertTrue(
                callable(getattr(self.fs, method_name, None)),
                f"PhysicalFilesystem missing method: {method_name}"
            )

    # --- read/write ---
    def test_write_and_read_text(self):
        p = self._path("test.txt")
        self.fs.write_text(p, "hello")
        self.assertEqual(self.fs.read_text(p), "hello")

    def test_write_and_read_bytes(self):
        p = self._path("test.bin")
        data = b"\x00\x01\x02"
        self.fs.write_bytes(p, data)
        self.assertEqual(self.fs.read_bytes(p), data)

    def test_write_text_normalizes_crlf(self):
        p = self._path("crlf.txt")
        self.fs.write_text(p, "line1\r\nline2")
        content = self.fs.read_bytes(p)
        self.assertNotIn(b"\r\n", content)

    # --- exists / is_file / is_dir ---
    def test_exists_true(self):
        p = self._path("e.txt")
        self.fs.write_text(p, "x")
        self.assertTrue(self.fs.exists(p))

    def test_exists_false(self):
        self.assertFalse(self.fs.exists(self._path("no_such")))

    def test_is_file(self):
        p = self._path("f.txt")
        self.fs.write_text(p, "x")
        self.assertTrue(self.fs.is_file(p))
        self.assertFalse(self.fs.is_dir(p))

    def test_is_dir(self):
        d = self._path("sub")
        self.fs.makedirs(d)
        self.assertTrue(self.fs.is_dir(d))
        self.assertFalse(self.fs.is_file(d))

    # --- copy / remove ---
    def test_copy(self):
        src = self._path("src.txt")
        dst = self._path("dst.txt")
        self.fs.write_text(src, "content")
        self.fs.copy(src, dst)
        self.assertEqual(self.fs.read_text(dst), "content")

    def test_remove(self):
        p = self._path("r.txt")
        self.fs.write_text(p, "x")
        self.assertTrue(self.fs.exists(p))
        self.fs.remove(p)
        self.assertFalse(self.fs.exists(p))

    def test_remove_nonexistent_is_noop(self):
        self.fs.remove(self._path("nope"))  # should not raise

    # --- is_binary_file ---
    def test_is_binary_file_png(self):
        p = self._path("img.png")
        self.fs.write_bytes(p, b"\x89PNG\r\n\x1a\n")
        self.assertTrue(self.fs.is_binary_file(p))

    def test_is_binary_file_text(self):
        p = self._path("readme.md")
        self.fs.write_text(p, "# Hello")
        self.assertFalse(self.fs.is_binary_file(p))

    def test_is_binary_file_null_bytes(self):
        p = self._path("data.dat")
        self.fs.write_bytes(p, b"Hello\x00World")
        self.assertTrue(self.fs.is_binary_file(p))

    # --- listing ---
    def test_list_files(self):
        self.fs.write_text(self._path("a.txt"), "a")
        self.fs.write_text(self._path("b.txt"), "b")
        files = self.fs.list_files(self.temp_dir)
        basenames = sorted(os.path.basename(f) for f in files)
        self.assertEqual(basenames, ["a.txt", "b.txt"])

    def test_list_dir(self):
        self.fs.write_text(self._path("c.txt"), "c")
        self.fs.makedirs(self._path("sub"))
        entries = self.fs.list_dir(self.temp_dir)
        self.assertIn("c.txt", entries)
        self.assertIn("sub", entries)

    def test_list_dir_nonexistent(self):
        self.assertEqual(self.fs.list_dir(self._path("nope")), [])

    # --- relpath / abspath ---
    def test_relpath(self):
        rel = self.fs.relpath(os.path.join(self.temp_dir, "a", "b.txt"), self.temp_dir)
        self.assertEqual(rel, "a/b.txt")

    def test_abspath(self):
        result = self.fs.abspath("relative/path")
        self.assertTrue(os.path.isabs(result))


if __name__ == "__main__":
    unittest.main()
