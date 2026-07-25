from concurrent.futures import ThreadPoolExecutor

import pytest

from cida.domain.errors import SemanticValidationError, UnsupportedFrontmatterSyntaxError
from cida.infrastructure.frontmatter_codec import FrontmatterCodec


def parse(content: str) -> dict:
    return FrontmatterCodec().parse_frontmatter_safe(content)


def test_empty_frontmatter():
    assert parse("---\n\n---") == {}


def test_bom_lf_and_crlf():
    assert parse("\ufeff---\nkey: value\n---") == {"key": "value"}
    assert parse("---\r\nkey: value\r\n---") == {"key": "value"}


def test_scalar_types_and_quotes():
    parsed = parse(
        "---\n"
        "plain: value\n"
        "single: 'a: b # c'\n"
        'double: "a: b # c"\n'
        "flag: true\n"
        "empty: null\n"
        "count: 42\n"
        "ratio: 1.5\n"
        "---"
    )
    assert parsed == {
        "plain": "value",
        "single": "a: b # c",
        "double": "a: b # c",
        "flag": True,
        "empty": None,
        "count": 42,
        "ratio": 1.5,
    }


def test_inline_and_indented_lists():
    assert parse("---\nitems: [one, 'two:2', 3]\n---") == {"items": ["one", "two:2", 3]}
    assert parse("---\nitems:\n  - one\n  - two\n---") == {"items": ["one", "two"]}


def test_nested_maps():
    assert parse("---\nmeta:\n  owner: docs\n  flags:\n    stable: true\n---") == {
        "meta": {"owner": "docs", "flags": {"stable": True}}
    }


def test_duplicate_keys_rejected():
    with pytest.raises(UnsupportedFrontmatterSyntaxError):
        parse("---\nkey: one\nkey: two\n---")


def test_invalid_indentation_rejected():
    with pytest.raises(UnsupportedFrontmatterSyntaxError):
        parse("---\nmeta:\n   owner: docs\n---")


@pytest.mark.parametrize(
    "body",
    [
        "base: &base value",
        "copy: *base",
        "<<: *base",
        "tagged: !Custom value",
        "---\na: b",
        "text: |\n  long",
    ],
)
def test_unsupported_yaml_syntax_rejected(body: str):
    with pytest.raises((UnsupportedFrontmatterSyntaxError, ValueError)):
        parse(f"---\n{body}\n---")


def test_non_mapping_rejected():
    with pytest.raises(SemanticValidationError):
        parse("---\n- item\n---")


def test_excessive_depth_rejected():
    body = ""
    for i in range(10):
        body += ("  " * i) + f"k{i}:\n"
    body += ("  " * 10) + "leaf: value\n"
    with pytest.raises(UnsupportedFrontmatterSyntaxError):
        parse(f"---\n{body}---")


def test_input_too_large_rejected():
    codec = FrontmatterCodec()
    codec.max_bytes = 8
    with pytest.raises(UnsupportedFrontmatterSyntaxError):
        codec.decode("key: value that is too large")


def test_decode_legacy_null_document_and_comment_only():
    codec = FrontmatterCodec()
    assert codec.decode("---\n") == {}
    assert codec.decode("# only a comment\n") == {}


def test_bom_decode_and_trailing_comment():
    assert FrontmatterCodec().decode("\ufeffkey: value # comment\n") == {"key": "value"}


def test_tabs_and_unsupported_question_rejected():
    codec = FrontmatterCodec()
    with pytest.raises(UnsupportedFrontmatterSyntaxError, match="Tabs"):
        codec.decode("root:\n\tkey: value\n")
    with pytest.raises(UnsupportedFrontmatterSyntaxError, match="Unsupported YAML syntax"):
        codec.decode("? key\n")


def test_empty_nested_value_and_key_count_limit():
    codec = FrontmatterCodec()
    assert codec.decode("key:\n") == {"key": None}
    codec.max_keys = 1
    with pytest.raises(UnsupportedFrontmatterSyntaxError, match="maximum key count"):
        codec.decode("a: 1\nb: 2\n")


def test_indented_list_null_nested_map_and_item_map():
    parsed = FrontmatterCodec().decode(
        "items:\n"
        "  -\n"
        "  - name: cida\n"
        "  -\n"
        "    nested: true\n"
        "after: ok\n"
    )
    assert parsed == {"items": [None, {"name": "cida"}, {"nested": True}], "after": "ok"}


def test_invalid_list_indentation_and_missing_pair():
    codec = FrontmatterCodec()
    with pytest.raises(UnsupportedFrontmatterSyntaxError, match="Invalid list indentation"):
        codec.decode("items:\n  - one\n    - two\n")
    with pytest.raises(UnsupportedFrontmatterSyntaxError, match="Expected key/value pair"):
        codec.decode("key\n")


def test_inline_list_empty_unclosed_and_unclosed_string():
    codec = FrontmatterCodec()
    assert codec.decode("items: []\n") == {"items": []}
    with pytest.raises(UnsupportedFrontmatterSyntaxError, match="Unclosed inline list"):
        codec.decode("items: [one\n")
    with pytest.raises(UnsupportedFrontmatterSyntaxError, match="Unclosed string"):
        codec.decode("items: ['one, two]\n")


def test_inline_map_empty_key_quoted_key_and_ambiguous_key():
    codec = FrontmatterCodec()
    assert codec.decode("'123': value\n") == {"123": "value"}
    with pytest.raises(UnsupportedFrontmatterSyntaxError, match="Inline maps"):
        codec.decode("item: {a: b}\n")
    with pytest.raises(UnsupportedFrontmatterSyntaxError, match="Empty frontmatter key"):
        codec.decode(": value\n")
    with pytest.raises(UnsupportedFrontmatterSyntaxError, match="Ambiguous key"):
        codec.decode("bad key: value\n")


def test_single_quote_escape_and_double_quote_comment_escape():
    parsed = FrontmatterCodec().decode("single: 'it''s ok'\ndouble: \"a \\\"# not comment\"\n")
    assert parsed == {"single": "it's ok", "double": 'a "# not comment'}


def test_double_quoted_unicode_literals_are_preserved():
    parsed = FrontmatterCodec().decode(
        'title: "São Paulo"\n'
        'owner: "João"\n'
        'description: "ação e validação"\n'
        'chinese: "你好"\n'
        'japanese: "こんにちは"\n'
        'emoji: "🚀"\n'
        'outside_bmp: "𐍈"\n'
    )

    assert parsed == {
        "title": "São Paulo",
        "owner": "João",
        "description": "ação e validação",
        "chinese": "你好",
        "japanese": "こんにちは",
        "emoji": "🚀",
        "outside_bmp": "𐍈",
    }


def test_double_quoted_supported_escapes():
    parsed = FrontmatterCodec().decode(
        'quote: "a \\"quote\\""\n'
        'slash: "a \\\\ slash"\n'
        'control: "line\\nnext\\rret\\ttab\\bback\\fform"\n'
        'unicode: "\\u00E7 \\uD83D\\uDE80 \\U00010348"\n'
    )

    assert parsed["quote"] == 'a "quote"'
    assert parsed["slash"] == "a \\ slash"
    assert parsed["control"] == "line\nnext\rret\ttab\bback\fform"
    assert parsed["unicode"] == "ç 🚀 𐍈"


@pytest.mark.parametrize(
    "body",
    [
        'bad: "\\q"\n',
        'bad: "abc\\"\n',
        'bad: "\\u12"\n',
        'bad: "\\uZZZZ"\n',
        'bad: "\\uD800"\n',
        'bad: "\\uDC00"\n',
        'bad: "\\uD800\\u0041"\n',
        'bad: "\\U00110000"\n',
    ],
)
def test_double_quoted_invalid_escapes_rejected(body: str):
    with pytest.raises(UnsupportedFrontmatterSyntaxError):
        FrontmatterCodec().decode(body)


def test_single_quoted_backslashes_are_literal():
    assert FrontmatterCodec().decode("value: 'a \\n literal \\\\'\n") == {"value": "a \\n literal \\\\"}


def test_list_items_support_multifield_nested_mappings():
    parsed = FrontmatterCodec().decode(
        "items:\n"
        "  - name: first\n"
        "    enabled: true\n"
        "    metadata:\n"
        "      path: docs/a.md\n"
        "      tags:\n"
        "        - stable\n"
        "        - production\n"
        "  - name: second\n"
        "    enabled: false\n"
    )

    assert parsed == {
        "items": [
            {
                "name": "first",
                "enabled": True,
                "metadata": {"path": "docs/a.md", "tags": ["stable", "production"]},
            },
            {"name": "second", "enabled": False},
        ]
    }


def test_list_items_support_dash_then_mapping_and_reject_duplicate_item_key():
    assert FrontmatterCodec().decode("items:\n  -\n    name: first\n    enabled: true\n") == {
        "items": [{"name": "first", "enabled": True}]
    }

    with pytest.raises(UnsupportedFrontmatterSyntaxError, match="Duplicate key"):
        FrontmatterCodec().decode("items:\n  - name: first\n    name: duplicate\n")


def test_frontmatter_codec_instance_is_concurrency_safe():
    codec = FrontmatterCodec()
    documents = [
        'title: "São Paulo"\nitems:\n  - name: first\n    enabled: true\n',
        'title: "你好"\nitems:\n  - name: second\n    enabled: false\n',
        'title: "🚀"\nitems:\n  - name: third\n    enabled: true\n',
    ]

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(codec.decode, documents * 20))

    assert {result["title"] for result in results} == {"São Paulo", "你好", "🚀"}
    assert all("items" in result for result in results)
