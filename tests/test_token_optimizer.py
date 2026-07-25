import unittest
import os
import sys

# Setup TIKTOKEN_CACHE_DIR for offline execution
dir_path = os.path.dirname(os.path.abspath(__file__))
os.environ["TIKTOKEN_CACHE_DIR"] = os.path.normpath(os.path.join(dir_path, "../resources"))

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from markdown.block_parser import parse_markdown  # noqa: E402
from markdown.protected_regions import ProtectedRegionsManager  # noqa: E402
from markdown.phrase_dictionary import (  # noqa: E402
    generate_alias_candidates, build_file_dictionary,
)
from markdown.semantic_validator import validate_semantics, parse_yaml_frontmatter  # noqa: E402
from token_optimizer import (  # noqa: E402
    detect_profile, minificar_codigo_para_ia, remove_html_comments,
    trim_trailing_whitespace, normalize_newlines, table_whitespace,
    list_compaction, build_corpus_dictionary, is_binary_file
)

class TestMarkdownBlockParser(unittest.TestCase):
    def test_empty_markdown(self):
        blocks = parse_markdown("")
        self.assertEqual(len(blocks), 0)

    def test_block_parser_headers(self):
        text = "# H1\n\n## H2"
        blocks = parse_markdown(text)
        headers = [b for b in blocks if b.type == "header"]
        self.assertEqual(len(headers), 2)
        self.assertEqual(headers[0].content.strip(), "# H1")
        self.assertEqual(headers[1].content.strip(), "## H2")

    def test_block_parser_lists(self):
        text = "- Item 1\n- Item 2\n  - Item 2.1"
        blocks = parse_markdown(text)
        lists = [b for b in blocks if b.type == "list"]
        self.assertEqual(len(lists), 1)
        self.assertIn("Item 2.1", lists[0].content)

    def test_block_parser_code_blocks(self):
        text = "```python\ndef test():\n    pass\n```"
        blocks = parse_markdown(text)
        code_blocks = [b for b in blocks if b.type == "code_block"]
        self.assertEqual(len(code_blocks), 1)
        self.assertEqual(code_blocks[0].metadata.get("lang"), "python")
        self.assertIn("def test():", code_blocks[0].content)

    def test_block_parser_tables(self):
        text = "| Col 1 | Col 2 |\n|---|---|\n| Val 1 | Val 2 |"
        blocks = parse_markdown(text)
        tables = [b for b in blocks if b.type == "table"]
        self.assertEqual(len(tables), 1)
        self.assertIn("Val 1", tables[0].content)

    def test_block_parser_paragraph(self):
        text = "This is a normal paragraph.\nWith multiple lines."
        blocks = parse_markdown(text)
        paras = [b for b in blocks if b.type == "paragraph"]
        self.assertEqual(len(paras), 1)
        self.assertIn("normal paragraph", paras[0].content)

    def test_block_parser_html_comment(self):
        text = "<!-- Comment -->"
        blocks = parse_markdown(text)
        comments = [b for b in blocks if b.type == "comment"]
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0].content.strip(), "<!-- Comment -->")


class TestProtectedRegions(unittest.TestCase):
    def setUp(self):
        self.pm = ProtectedRegionsManager()

    def test_protect_inline_code(self):
        text = "Use `my_var` in the code."
        protected = self.pm.protect(text)
        self.assertNotIn("`my_var`", protected)
        self.assertIn("___PROTECTED_REGION_", protected)
        restored = self.pm.restore(protected)
        self.assertEqual(text, restored)

    def test_protect_urls(self):
        text = "Check http://google.com for info."
        protected = self.pm.protect(text)
        self.assertNotIn("http://google.com", protected)
        restored = self.pm.restore(protected)
        self.assertEqual(text, restored)

    def test_protect_links(self):
        text = "Link [Step 1](steps-c/step-01.md) target."
        protected = self.pm.protect(text)
        self.assertNotIn("steps-c/step-01.md", protected)
        restored = self.pm.restore(protected)
        self.assertEqual(text, restored)

    def test_protect_placeholders(self):
        text = "Variables: {var}, {{var2}} and ${VAR3}."
        protected = self.pm.protect(text)
        self.assertNotIn("{var}", protected)
        self.assertNotIn("{{var2}}", protected)
        self.assertNotIn("${VAR3}", protected)
        restored = self.pm.restore(protected)
        self.assertEqual(text, restored)

    def test_protect_tags(self):
        text = "Use <div class='test'>content</div> tags."
        protected = self.pm.protect(text)
        self.assertNotIn("<div", protected)
        restored = self.pm.restore(protected)
        self.assertEqual(text, restored)

    def test_protect_bmad_keywords(self):
        text = "Check stepsCompleted and workflowType properties."
        protected = self.pm.protect(text)
        self.assertNotIn("stepsCompleted", protected)
        self.assertNotIn("workflowType", protected)
        restored = self.pm.restore(protected)
        self.assertEqual(text, restored)


class TestPhraseDictionary(unittest.TestCase):
    def test_dictionary_alias_candidates(self):
        exclude = {"AA", "AB"}
        candidates = generate_alias_candidates(exclude, limit=5)
        self.assertEqual(len(candidates), 5)
        self.assertNotIn("AA", candidates)
        self.assertNotIn("AB", candidates)
        self.assertEqual(candidates[0], "AC")

    def test_dictionary_gain_calculation(self):
        # Text with high repetition of a word (repeated 30 times to make dictionary net gain positive)
        words = ["repetition_word_test"] * 30
        text = " ".join(words)
        pm = ProtectedRegionsManager()
        file_dict, header = build_file_dictionary(text, pm, min_margin=0)
        self.assertIn("repetition_word_test", file_dict)
        self.assertIn("🤖 AI RAG DICT:", header)

    def test_dictionary_collision_prevention(self):
        # Text contains the word "AA"
        text = "This AA is repeating repeating repeating repeating."
        pm = ProtectedRegionsManager()
        file_dict, _ = build_file_dictionary(text, pm, min_margin=0)
        # AA should not be used as an alias
        self.assertNotIn("AA", file_dict.values())


class TestSemanticValidator(unittest.TestCase):
    def test_semantic_validator_success(self):
        original = "# My Title\n- Item 1\n- Item 2\n"
        minified = "# My Title\n- Item 1\n- Item 2\n"
        is_valid, msg = validate_semantics(original, minified)
        self.assertTrue(is_valid, msg)

    def test_semantic_validator_fail_header_changed(self):
        original = "# My Title\n- Item 1"
        minified = "# Modified Title\n- Item 1"
        is_valid, msg = validate_semantics(original, minified)
        self.assertFalse(is_valid)
        self.assertIn("Header content mismatch", msg)

    def test_semantic_validator_fail_list_item_missing(self):
        original = "- Item 1\n- Item 2"
        minified = "- Item 1"
        is_valid, msg = validate_semantics(original, minified)
        self.assertFalse(is_valid)
        self.assertIn("List items count mismatch", msg)

    def test_semantic_validator_fail_placeholder_changed(self):
        original = "Hello {variable}"
        minified = "Hello {mangled_variable}"
        is_valid, msg = validate_semantics(original, minified)
        self.assertFalse(is_valid)
        self.assertIn("Inline element 'placeholders' mismatch", msg)


class TestTransformations(unittest.TestCase):
    def test_remove_html_comments(self):
        text = "Hello <!-- comment --> World"
        self.assertEqual(remove_html_comments(text), "Hello  World")

        # Preservation of BMAD operational comments
        bmad_comment = "Hello <!-- stepsCompleted=[1] --> World"
        self.assertEqual(remove_html_comments(bmad_comment), bmad_comment)

    def test_trim_trailing_whitespace(self):
        text = "Line 1   \nLine 2 \n"
        # The function strips each line, joining them with \n
        self.assertEqual(trim_trailing_whitespace(text), "Line 1\nLine 2")

    def test_normalize_newlines(self):
        text = "Paragraph 1\n\n\n\nParagraph 2"
        self.assertEqual(normalize_newlines(text), "Paragraph 1\n\nParagraph 2")

    def test_table_whitespace(self):
        text = "|  Col 1  |  Col 2  |\n|---|---|\n|  V1  |  V2  |"
        expected = "|Col 1|Col 2|\n|---|---|\n|V1|V2|"
        self.assertEqual(table_whitespace(text), expected)

    def test_list_compaction(self):
        text = "- Item 1\n\n- Item 2\n\n- Item 3"
        expected = "- Item 1\n- Item 2\n- Item 3"
        self.assertEqual(list_compaction(text), expected)


class TestJavaRegression(unittest.TestCase):
    def test_java_regression_comments(self):
        code = "/* comment */\n// line comment\npublic class A {}"
        minified = minificar_codigo_para_ia(code)
        self.assertNotIn("/*", minified)
        self.assertNotIn("//", minified)
        self.assertIn("class A{}", minified)

    def test_java_regression_package_import(self):
        code = "package com.test;\nimport java.util.*;\nclass A {}"
        minified = minificar_codigo_para_ia(code)
        self.assertNotIn("package", minified)
        self.assertNotIn("import", minified)

    def test_java_regression_annotations(self):
        code = "@Component\n@Autowired\nclass A {}"
        minified = minificar_codigo_para_ia(code)
        self.assertNotIn("@Component", minified)
        self.assertNotIn("@Autowired", minified)

    def test_java_regression_modifiers(self):
        code = "public private protected final volatile class A {}"
        minified = minificar_codigo_para_ia(code)
        self.assertNotIn("public", minified)
        self.assertNotIn("private", minified)
        self.assertNotIn("protected", minified)
        self.assertNotIn("final", minified)
        self.assertEqual(minified, "class A{}")

    def test_java_regression_long_strings(self):
        code = 'String s = "This is a very long string (> 15 chars)";'
        minified = minificar_codigo_para_ia(code)
        self.assertIn('s=""', minified)

    def test_java_regression_short_strings(self):
        code = 'String s = "short";'
        minified = minificar_codigo_para_ia(code)
        self.assertIn('s="short"', minified)


class TestCLIOptions(unittest.TestCase):
    def test_binary_file_detection(self):
        # Create a text file
        txt_path = "tests/test_txt.txt"
        with open(txt_path, "w") as f:
            f.write("Normal text")
        self.assertFalse(is_binary_file(txt_path))
        os.remove(txt_path)

        # Create a binary file
        bin_path = "tests/test_bin.bin"
        with open(bin_path, "wb") as f:
            f.write(b"\x00\x01\x02\x03\x04")
        self.assertTrue(is_binary_file(bin_path))
        os.remove(bin_path)

    def test_bmad_profile_detection(self):
        self.assertEqual(detect_profile("workflow.md", "some content"), "bmad")
        self.assertEqual(detect_profile("steps-c/step-01.md", "some content"), "bmad")
        self.assertEqual(detect_profile("some_file.md", "stepsCompleted: [0]"), "bmad")
        self.assertEqual(detect_profile("some_file.md", "Hello {variable}"), "bmad")
        self.assertEqual(detect_profile("normal.md", "Normal Markdown"), "markdown")
        self.assertEqual(detect_profile("App.java", "public class App {}"), "java")

    def test_semantic_validator_fail_code_block_content_changed(self):
        original_c = "```python\nprint('hello')\n```"
        minified_c = "```python\nprint('world')\n```"
        is_valid, msg = validate_semantics(original_c, minified_c)
        self.assertFalse(is_valid)
        self.assertIn("Code block content mismatch", msg)

    def test_semantic_validator_fail_link_destination_changed(self):
        original = "[Label](http://google.com)"
        minified = "[Label](http://yahoo.com)"
        is_valid, msg = validate_semantics(original, minified)
        self.assertFalse(is_valid)
        self.assertIn("Inline element 'links' mismatch", msg)

    def test_semantic_validator_fail_frontmatter_value_changed(self):
        original = "---\nkey: val1\n---\n"
        minified = "---\nkey: val2\n---\n"
        is_valid, msg = validate_semantics(original, minified)
        self.assertFalse(is_valid)
        self.assertIn("Frontmatter value mismatch", msg)

    def test_corpus_dictionary_generation(self):
        contents = [
            "repetition_test_word repetition_test_word repetition_test_word",
            "repetition_test_word repetition_test_word repetition_test_word"
        ]
        dic = build_corpus_dictionary(contents, min_margin=0)
        self.assertIn("repetition_test_word", dic)

    def test_detect_profile_unrecognized(self):
        self.assertEqual(detect_profile("config.json", "{}"), "code")

    def test_bmad_profile_step_file_detection(self):
        self.assertEqual(detect_profile("step-12.md", "content"), "bmad")

    def test_parse_yaml_frontmatter(self):
        fm_str = "---\nstepsCompleted: [0, 1]\nworkflowType: dev\n---"
        parsed = parse_yaml_frontmatter(fm_str)
        self.assertEqual(parsed["stepsCompleted"], [0, 1])
        self.assertEqual(parsed["workflowType"], "dev")

    def test_is_binary_file_with_non_binary(self):
        self.assertFalse(is_binary_file(__file__))

if __name__ == "__main__":
    unittest.main()
