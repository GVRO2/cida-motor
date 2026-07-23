import unittest
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))
from markdown.semantic_validator import validate_semantics

class TestAdversarialEquivalence(unittest.TestCase):
    def test_paragraph_modification(self):
        orig = "This is a normal paragraph with some instruction."
        mini = "This is a normal paragraph with some other instruction."
        is_valid, msg = validate_semantics(orig, mini)
        self.assertFalse(is_valid)
        self.assertIn("Paragraph mismatch", msg)

    def test_negation_removal(self):
        orig = "Você não deve executar esta ação."
        mini = "Você deve executar esta ação."
        is_valid, msg = validate_semantics(orig, mini)
        self.assertFalse(is_valid)
        # Should be caught by paragraph mismatch or protected element/normative mismatch
        self.assertFalse(is_valid)

    def test_obligation_alteration(self):
        orig = "O agente deve validar os dados."
        mini = "O agente pode validar os dados."
        is_valid, msg = validate_semantics(orig, mini)
        self.assertFalse(is_valid)

    def test_list_item_modified(self):
        orig = "- Item A\n- Item B"
        mini = "- Item A\n- Item C"
        is_valid, msg = validate_semantics(orig, mini)
        self.assertFalse(is_valid)
        self.assertIn("List item content mismatch", msg)

    def test_list_order_changed(self):
        orig = "- Item A\n- Item B"
        mini = "- Item B\n- Item A"
        is_valid, msg = validate_semantics(orig, mini)
        self.assertFalse(is_valid)
        self.assertIn("List item content mismatch", msg)

    def test_list_depth_changed(self):
        orig = "- Item A\n  - Item B"
        mini = "- Item A\n- Item B"
        is_valid, msg = validate_semantics(orig, mini)
        self.assertFalse(is_valid)
        self.assertIn("List item indentation depth mismatch", msg)

    def test_list_marker_changed(self):
        orig = "- Item A\n- Item B"
        mini = "- Item A\n* Item B"
        is_valid, msg = validate_semantics(orig, mini)
        self.assertFalse(is_valid)
        self.assertIn("List item marker mismatch", msg)

    def test_table_cell_changed(self):
        orig = "| Col 1 | Col 2 |\n|---|---|\n| Val A | Val B |"
        mini = "| Col 1 | Col 2 |\n|---|---|\n| Val A | Val C |"
        is_valid, msg = validate_semantics(orig, mini)
        self.assertFalse(is_valid)
        self.assertIn("Table cells content mismatch", msg)

    def test_table_alignment_changed(self):
        orig = "| Col 1 | Col 2 |\n|:---|---:|\n| Val A | Val B |"
        mini = "| Col 1 | Col 2 |\n|---|---|\n| Val A | Val B |"
        is_valid, msg = validate_semantics(orig, mini)
        self.assertFalse(is_valid)
        self.assertIn("Table columns alignment mismatch", msg)

    def test_blockquote_changed(self):
        orig = "> Attention: do this first."
        mini = "> Attention: do this last."
        is_valid, msg = validate_semantics(orig, mini)
        self.assertFalse(is_valid)
        self.assertIn("Blockquote mismatch", msg)

    def test_inline_code_changed(self):
        orig = "Use `my_val` for configuration."
        mini = "Use `other_val` for configuration."
        is_valid, msg = validate_semantics(orig, mini)
        self.assertFalse(is_valid)
        self.assertIn("Inline element 'inline_codes' mismatch", msg)

    def test_yaml_duplicate_key(self):
        orig = "---\nstepsCompleted: [0]\nstepsCompleted: [1]\n---"
        mini = "---\nstepsCompleted: [0]\n---"
        is_valid, msg = validate_semantics(orig, mini)
        self.assertFalse(is_valid)
        self.assertIn("YAML frontmatter error", msg)

    def test_operational_comment_changed(self):
        orig = "<!-- stepsCompleted=[1] -->"
        mini = "<!-- stepsCompleted=[2] -->"
        is_valid, msg = validate_semantics(orig, mini)
        self.assertFalse(is_valid)
        self.assertIn("Inline element 'tags' mismatch", msg)

    def test_fenced_code_blocks(self):
        orig = "```python\nprint('hello')\n```"
        mini = "```python\nprint('world')\n```"
        is_valid, msg = validate_semantics(orig, mini)
        self.assertFalse(is_valid)
        self.assertIn("Code block content mismatch", msg)

    def test_unclosed_fenced_block(self):
        from markdown.block_parser import parse_markdown
        with self.assertRaises(ValueError):
            parse_markdown("```python\nprint('hello')")

    def test_corrupted_yaml_frontmatter_types(self):
        orig = "---\n- item1\n- item2\n---"
        mini = "---\nitem1: val\n---"
        is_valid, msg = validate_semantics(orig, mini)
        self.assertFalse(is_valid)
        self.assertIn("YAML frontmatter error", msg)

    def test_sidecar_validation_path_mismatch(self):
        from markdown.sidecar import validate_sidecar
        sidecar_data = {
            "format": "cida-token-sidecar",
            "version": 1,
            "source": "other/file.md",
            "source_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "entries": {"A": "val"}
        }
        with self.assertRaises(ValueError) as ctx:
            validate_sidecar(sidecar_data, "some/file.md", b"")
        self.assertIn("Source path mismatch", str(ctx.exception))

    def test_sidecar_validation_alias_collision(self):
        from markdown.sidecar import validate_sidecar
        sidecar_data = {
            "format": "cida-token-sidecar",
            "version": 1,
            "source": "some/file.md",
            "source_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "entries": {"A": "val", "B": "val"}
        }
        with self.assertRaises(ValueError) as ctx:
            validate_sidecar(sidecar_data, "some/file.md", b"")
        self.assertIn("duplicate value", str(ctx.exception).lower())

    def test_sidecar_validation_invalid_hash(self):
        from markdown.sidecar import validate_sidecar
        sidecar_data = {
            "format": "cida-token-sidecar",
            "version": 1,
            "source": "some/file.md",
            "source_sha256": "invalid_hash_value",
            "entries": {"A": "val"}
        }
        with self.assertRaises(ValueError) as ctx:
            validate_sidecar(sidecar_data, "some/file.md", b"")
        self.assertIn("SHA-256", str(ctx.exception))

    def test_comment_cleaning_retains_unknown_and_operational(self):
        from markdown.semantic_validator import clean_comments
        text = "Hello <!-- stepsCompleted=[1] --> and <!-- custom --> and <!-- @CIDA ignore -->"
        cleaned = clean_comments(text)
        self.assertIn("<!-- stepsCompleted=[1] -->", cleaned)
        self.assertIn("<!-- custom -->", cleaned)
        self.assertIn("<!-- @CIDA ignore -->", cleaned)
        self.assertNotIn("<!-- --- -->", clean_comments("Hello <!-- --- -->"))
        self.assertNotIn("<!-- generated by CIDA -->", clean_comments("Hello <!-- generated by CIDA -->"))

if __name__ == "__main__":
    unittest.main()
