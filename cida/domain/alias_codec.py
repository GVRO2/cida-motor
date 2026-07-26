import re
import string
from dataclasses import dataclass
from functools import cached_property
from typing import Iterable


@dataclass(frozen=True)
class AliasIdentity:
    text: str
    ordinal: int
    length: int
    alphabet_id: str


class AliasCodec:
    """Canonical alias grammar and ordinal codec.

    Version 1 preserves the historic generation order:
    uppercase two-letter aliases, lowercase two-letter aliases, then
    uppercase/lowercase aliases for each larger length.
    """

    version = 1
    alphabet_upper_id = "ascii-upper"
    alphabet_lower_id = "ascii-lower"
    min_length = 2
    max_length = 12

    _alphabets = (
        (alphabet_upper_id, string.ascii_uppercase),
        (alphabet_lower_id, string.ascii_lowercase),
    )

    def encode_ordinal(self, ordinal: int) -> str:
        if not isinstance(ordinal, int) or ordinal < 0:
            raise ValueError(f"Alias ordinal must be non-negative: {ordinal}")
        remaining = ordinal
        for length in range(self.min_length, self.max_length + 1):
            block_size = 26**length
            for _, alphabet in self._alphabets:
                if remaining < block_size:
                    return self._encode_fixed_width(remaining, length, alphabet)
                remaining -= block_size
        raise ValueError(f"Alias ordinal exceeds codec capacity: {ordinal}")

    def decode_alias(self, alias: str) -> AliasIdentity:
        if not self.is_structurally_valid(alias):
            raise ValueError(f"Malformed alias: {alias!r}")
        length = len(alias)
        ordinal = 0
        for candidate_length in range(self.min_length, length):
            ordinal += 2 * (26**candidate_length)
        for alphabet_id, alphabet in self._alphabets:
            if alias[0] in alphabet:
                ordinal += self._decode_fixed_width(alias, alphabet)
                return AliasIdentity(alias, ordinal, length, alphabet_id)
            ordinal += 26**length
        raise ValueError(f"Malformed alias: {alias!r}")

    def is_structurally_valid(self, alias: str) -> bool:
        if not isinstance(alias, str):
            return False
        if len(alias) < self.min_length or len(alias) > self.max_length:
            return False
        if not alias.isascii():
            return False
        if any(ch in alias for ch in ("/", "\\")):
            return False
        if any(ch.isspace() for ch in alias):
            return False
        if alias.isupper():
            return all(ch in string.ascii_uppercase for ch in alias)
        if alias.islower():
            return all(ch in string.ascii_lowercase for ch in alias)
        return False

    @cached_property
    def _candidate_pattern(self) -> re.Pattern[str]:
        return re.compile(rf"\b[A-Za-z]{{{self.min_length},{self.max_length}}}\b")

    def candidate_pattern(self) -> re.Pattern[str]:
        return self._candidate_pattern

    def iter_candidates(self, exclude_set: Iterable[str] = ()) -> Iterable[str]:
        excluded = set(exclude_set)
        ordinal = 0
        while True:
            candidate = self.encode_ordinal(ordinal)
            ordinal += 1
            if candidate not in excluded:
                yield candidate

    def segment_id(self, alias: str) -> str:
        identity = self.decode_alias(alias)
        prefix = alias[0]
        alphabet_prefix = "U" if identity.alphabet_id == self.alphabet_upper_id else "L"
        return f"{identity.length}-{alphabet_prefix}-{prefix}"

    @staticmethod
    def _encode_fixed_width(value: int, length: int, alphabet: str) -> str:
        chars = []
        remaining = value
        for _ in range(length):
            chars.append(alphabet[remaining % 26])
            remaining //= 26
        return "".join(reversed(chars))

    @staticmethod
    def _decode_fixed_width(alias: str, alphabet: str) -> int:
        value = 0
        for ch in alias:
            value = value * 26 + alphabet.index(ch)
        return value


DEFAULT_ALIAS_CODEC = AliasCodec()
