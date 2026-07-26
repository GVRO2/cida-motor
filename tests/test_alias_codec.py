import pytest

from cida.domain.alias_codec import AliasCodec
from cida.markdown.dictionary import generate_alias_candidates


def test_alias_codec_roundtrips_required_ordinals():
    codec = AliasCodec()

    for ordinal in (0, 1, 675, 676, 1351, 1352, 1353, 5_000, 10_000, 100_000, 1_000_000):
        alias = codec.encode_ordinal(ordinal)
        identity = codec.decode_alias(alias)
        assert identity.ordinal == ordinal
        assert identity.text == alias
        assert identity.length == len(alias)


def test_alias_codec_preserves_current_transitions_and_derives_next_after_three_letters():
    codec = AliasCodec()
    generated = generate_alias_candidates(set(), limit=(26 * 26 * 2) + (26**3) + 2)

    assert codec.encode_ordinal(0) == "AA"
    assert codec.encode_ordinal(675) == "ZZ"
    assert codec.encode_ordinal(676) == "aa"
    assert codec.encode_ordinal(1351) == "zz"
    assert codec.encode_ordinal(1352) == "AAA"
    assert generated[1352 + (26**3)] == codec.encode_ordinal(1352 + (26**3))


@pytest.mark.parametrize(
    "alias",
    [
        "",
        "A",
        "Aa",
        "A0",
        "../AA",
        "AA/BB",
        "AA BB",
        "ÁÁ",
        "A-B",
        "A" * 13,
    ],
)
def test_alias_codec_rejects_malformed_aliases(alias):
    codec = AliasCodec()

    assert codec.is_structurally_valid(alias) is False
    with pytest.raises(ValueError):
        codec.decode_alias(alias)


def test_alias_codec_rejects_negative_ordinal():
    with pytest.raises(ValueError):
        AliasCodec().encode_ordinal(-1)
