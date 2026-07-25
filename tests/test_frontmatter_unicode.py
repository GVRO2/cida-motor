import pytest

from cida.domain.errors import UnsupportedFrontmatterSyntaxError
from cida.infrastructure.frontmatter_codec import FrontmatterCodec


def test_unicode_contract_preserves_literal_scalars():
    parsed = FrontmatterCodec().parse_frontmatter_safe(
        "---\n"
        'title: "São Paulo"\n'
        'owner: "João"\n'
        'description: "ação e validação"\n'
        'chinese: "你好"\n'
        'japanese: "こんにちは"\n'
        'emoji: "🚀"\n'
        'outside_bmp: "𐍈"\n'
        "---\n"
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


@pytest.mark.parametrize("body", ['bad: "\\q"', 'bad: "\\uD800"', 'bad: "\\uDC00"', 'bad: "abc\\"'])
def test_unicode_contract_rejects_invalid_double_quote_escapes(body: str):
    with pytest.raises(UnsupportedFrontmatterSyntaxError):
        FrontmatterCodec().decode(body)
