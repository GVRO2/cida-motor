import os
import hashlib
import tiktoken
from cida.domain.errors import TokenizerError

from typing import Optional

class OfflineTokenizer:
    """Concrete offline tiktoken token counter adapter."""

    def __init__(self, cache_dir: Optional[str] = None):
        self.cache_dir = cache_dir
        self._enc = None

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

    def count(self, text: str) -> int:
        if not text:
            return 0
        try:
            return len(self.get_encoder().encode(text))
        except TokenizerError:
            raise
        except Exception as e:
            raise TokenizerError(f"Tokenizer error: {e}") from e
