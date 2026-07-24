from cida.infrastructure.tokenizer import OfflineTokenizer


class DummyEncoder:
    def __init__(self):
        self.calls = 0

    def encode(self, text):
        self.calls += 1
        return text.split()


def test_tokenizer_cache_returns_cached_count_without_reencoding():
    tokenizer = OfflineTokenizer(enable_cache=True)
    encoder = DummyEncoder()
    tokenizer._enc = encoder

    assert tokenizer.count("one two") == 2
    assert tokenizer.count("one two") == 2
    assert encoder.calls == 1


def test_tokenizer_cache_clears_when_max_size_is_reached():
    tokenizer = OfflineTokenizer(enable_cache=True)
    tokenizer._enc = DummyEncoder()
    tokenizer._cache_max_size = 1
    tokenizer._token_cache["old"] = 1

    assert tokenizer.count("new value") == 2
    assert "old" not in tokenizer._token_cache
    assert tokenizer._token_cache["new value"] == 2
