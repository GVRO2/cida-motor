import unittest
import os
import tempfile
import shutil
from markdown.sidecar import (
    create_sidecar_data, validate_sidecar_schema, read_sidecar, write_sidecar,
    SidecarValidationError, calculate_sha256, validate_sidecar,
)

class TestSidecarDictionaries(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.source_content = "This is a clean implementation test content."
        self.entries = {
            "AA": "implementation",
            "BB": "content"
        }

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_create_and_validate_sidecar_success(self):
        data = create_sidecar_data("test.md", self.source_content.encode('utf-8'), self.entries)
        # Should validate without throwing any error
        validate_sidecar_schema(data)

        self.assertEqual(data["format"], "cida-token-sidecar")
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["source"], "test.md")
        self.assertEqual(data["source_sha256"], calculate_sha256(self.source_content.encode('utf-8')))
        self.assertEqual(len(data["entries"]), 2)

        # Verify entries structure
        self.assertEqual(data["entries"]["AA"], "implementation")
        self.assertEqual(data["entries"]["BB"], "content")

    def test_duplicate_value_throws(self):
        bad_entries = {
            "AA": "common",
            "BB": "common"
        }
        with self.assertRaises(SidecarValidationError):
            create_sidecar_data("test.md", self.source_content.encode('utf-8'), bad_entries)

    def test_unsupported_format(self):
        data = create_sidecar_data("test.md", self.source_content.encode('utf-8'), self.entries)
        data["format"] = "other-format"
        with self.assertRaises(SidecarValidationError):
            validate_sidecar_schema(data)

    def test_unsupported_version(self):
        data = create_sidecar_data("test.md", self.source_content.encode('utf-8'), self.entries)
        data["version"] = 2
        with self.assertRaises(SidecarValidationError):
            validate_sidecar_schema(data)

    def test_missing_required_keys(self):
        data = create_sidecar_data("test.md", self.source_content.encode('utf-8'), self.entries)
        del data["source_sha256"]
        with self.assertRaises(SidecarValidationError):
            validate_sidecar_schema(data)

    def test_write_and_read_sidecar(self):
        data = create_sidecar_data("test.md", self.source_content.encode('utf-8'), self.entries)
        sidecar_path = os.path.join(self.temp_dir, "test.md.cidatkn")

        write_sidecar(sidecar_path, data)

        self.assertTrue(os.path.exists(sidecar_path))

        read_data = read_sidecar(sidecar_path)
        self.assertEqual(read_data["source_sha256"], data["source_sha256"])
        self.assertEqual(read_data["entries"], data["entries"])

    def run_roundtrip_test(self, original_content, expected_sidecar=True):
        dir_path = os.path.dirname(os.path.abspath(__file__))
        os.environ["TIKTOKEN_CACHE_DIR"] = os.path.normpath(os.path.join(dir_path, "../resources"))

        from token_optimizer import optimize_markdown_dictionary_file_scope
        from markdown.semantic_validator import validate_semantics
        import re

        original_bytes = original_content.encode("utf-8")
        minified, sidecar_data, dict_tokens = optimize_markdown_dictionary_file_scope(
            original_content, original_content, "test.md", verify_semantics=True
        )

        if expected_sidecar:
            self.assertIsNotNone(sidecar_data)
            self.assertNotEqual(minified, original_content)

            # Validate sidecar
            validate_sidecar(sidecar_data, "test.md", original_bytes)

            # Reconstruct
            decompressed = minified
            mapping = sidecar_data["entries"]
            sorted_aliases = sorted(mapping.keys(), key=len, reverse=True)
            for alias in sorted_aliases:
                pattern = re.compile(rf'\b{re.escape(alias)}\b')
                decompressed = pattern.sub(mapping[alias], decompressed)

            self.assertEqual(decompressed.encode("utf-8"), original_bytes)

            # Semantic validation check with correct dictionary direction
            validation_dict = {v: k for k, v in mapping.items()}
            is_valid, msg = validate_semantics(original_content, minified, validation_dict)
            self.assertTrue(is_valid, f"Semantic check failed: {msg}")
        else:
            self.assertIsNone(sidecar_data)

    def test_roundtrip_repetitive_content(self):
        content = "supercalifragilisticexpialidocious " * 100
        self.run_roundtrip_test(content, expected_sidecar=True)

    def test_roundtrip_unicode(self):
        content = "cora\u00e7\u00e3o " * 10 + "supercalifragilisticexpialidocious " * 100
        self.run_roundtrip_test(content, expected_sidecar=True)

    def test_roundtrip_crlf(self):
        content = "supercalifragilisticexpialidocious\r\n" * 100
        self.run_roundtrip_test(content, expected_sidecar=True)

    def test_roundtrip_lf(self):
        content = "supercalifragilisticexpialidocious\n" * 100
        self.run_roundtrip_test(content, expected_sidecar=True)

    def test_roundtrip_frontmatter(self):
        content = "---\ntitle: test\n---\n" + "supercalifragilisticexpialidocious " * 100
        self.run_roundtrip_test(content, expected_sidecar=True)

    def test_roundtrip_bmad_comments(self):
        content = "<!-- stepsCompleted=[1] -->\n" + "supercalifragilisticexpialidocious " * 100
        self.run_roundtrip_test(content, expected_sidecar=True)

    def test_roundtrip_lists(self):
        content = "".join(f"- item {i} supercalifragilisticexpialidocious\n" for i in range(100))
        self.run_roundtrip_test(content, expected_sidecar=True)

    def test_roundtrip_tables(self):
        content = "| col1 | col2 |\n|---|---|\n" + "".join(f"| val {i} | supercalifragilisticexpialidocious |\n" for i in range(50))
        self.run_roundtrip_test(content, expected_sidecar=True)

    def test_roundtrip_backticks(self):
        content = "```python\nprint('code')\n```\n" + "supercalifragilisticexpialidocious " * 100
        self.run_roundtrip_test(content, expected_sidecar=True)

    def test_roundtrip_tildes(self):
        content = "~~~python\nprint('code')\n~~~\n" + "supercalifragilisticexpialidocious " * 100
        self.run_roundtrip_test(content, expected_sidecar=True)

    def test_roundtrip_inline_code(self):
        content = "`code` " * 50 + "supercalifragilisticexpialidocious " * 50
        self.run_roundtrip_test(content, expected_sidecar=True)

    def test_roundtrip_links(self):
        content = "[link](http://example.com/code) " * 50 + "supercalifragilisticexpialidocious " * 50
        self.run_roundtrip_test(content, expected_sidecar=True)

    def test_roundtrip_tabs(self):
        content = "\tsupercalifragilisticexpialidocious\t " * 100
        self.run_roundtrip_test(content, expected_sidecar=True)

    def test_roundtrip_trailing_spaces(self):
        content = "supercalifragilisticexpialidocious   \n" * 100
        self.run_roundtrip_test(content, expected_sidecar=True)

    def test_roundtrip_empty(self):
        self.run_roundtrip_test("", expected_sidecar=False)

    def test_roundtrip_no_gain(self):
        self.run_roundtrip_test("small content", expected_sidecar=False)

if __name__ == "__main__":
    unittest.main()
