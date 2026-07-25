import os
import hashlib
from collections import OrderedDict
from typing import Optional
import tiktoken
from cida.domain.errors import TokenizerError


class OfflineTokenizer:
    """Concrete offline tiktoken token counter adapter."""

    def __init__(self, cache_dir: Optional[str] = None, enable_cache: bool = True):
        self.cache_dir = cache_dir
        self.enable_cache = enable_cache
        self._enc = None
        self._token_cache: OrderedDict = OrderedDict()
        self._cache_max_size = 5000
        self.tokenizer_calls = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self.cache_evictions = 0
        self.hashes_for_cache = 0

    def _resolve_cache_dir(self) -> Optional[str]:
        return self.cache_dir or os.environ.get("TIKTOKEN_CACHE_DIR")

    def verify_tokenizer_cache(self):
        c_dir = self._resolve_cache_dir()
        if not c_dir:
            raise TokenizerError("TIKTOKEN_CACHE_DIR environment variable is not set")
        if not os.path.exists(c_dir):
            raise TokenizerError(f"Tokenizer cache directory does not exist: {c_dir}")

        expected_file = os.path.join(c_dir, "9b5ad71b2ce5302211f9c61530b329a4922fc6a4")
        if not os.path.exists(expected_file):
            raise TokenizerError(f"Required tokenizer cache file is missing: {expected_file}")

        file_size = os.path.getsize(expected_file)
        if file_size not in [1681126, 1781382]:
            raise TokenizerError(f"Tokenizer cache file is corrupted (invalid size: {file_size})")

        h = hashlib.sha1()
        with open(expected_file, 'rb') as f:
            h.update(f.read())
        file_hash = h.hexdigest()
        expected_hashes = ["9b5ad71b2ce5302211f9c61530b329a4922fc6a4", "6494e42d5aad2bbb441ea9793af9e7db335c8d9c", "86ac4193f03c2214c96a388affad156a9776e42e"]
        if file_hash not in expected_hashes:
            raise TokenizerError(f"Tokenizer cache file hash mismatch (got {file_hash})")

    def get_encoder(self):
        if self._enc is None:
            self.verify_tokenizer_cache()
            try:
                self._enc = tiktoken.get_encoding("cl100k_base")
            except Exception as e:
                raise TokenizerError(f"Unexpected tokenizer failure: {e}") from e
        return self._enc

    def count(self, text: str, content_hash: Optional[str] = None) -> int:
        if not text:
            return 0

        self.tokenizer_calls += 1

        if self.enable_cache:
            cache_key = content_hash if content_hash else text
            if cache_key in self._token_cache:
                self.cache_hits += 1
                self._token_cache.move_to_end(cache_key)
                return self._token_cache[cache_key]

            self.cache_misses += 1

        try:
            cnt = len(self.get_encoder().encode(text))
            if self.enable_cache:
                if len(self._token_cache) >= self._cache_max_size:
                    self._token_cache.popitem(last=False)
                    self.cache_evictions += 1
                self._token_cache[cache_key] = cnt
            return cnt
        except TokenizerError:
            raise
        except Exception as e:
            raise TokenizerError(f"Tokenizer error: {e}") from e

