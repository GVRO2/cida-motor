import os
import pytest
from unittest.mock import patch
from cida.domain.errors import TokenizerError, SemanticValidationError
from cida.infrastructure.yaml_codec import YamlCodec
from cida.infrastructure.tokenizer import OfflineTokenizer
from cida.infrastructure.hashing import HashService
from cida.infrastructure.filesystem import PhysicalFilesystem

@pytest.fixture(autouse=True)
def setup_env():
    old_val = os.environ.get("TIKTOKEN_CACHE_DIR")
    os.environ["TIKTOKEN_CACHE_DIR"] = os.path.abspath("resources")
    yield
    if old_val is not None:
        os.environ["TIKTOKEN_CACHE_DIR"] = old_val
    else:
        os.environ.pop("TIKTOKEN_CACHE_DIR", None)

def test_yaml_codec_frontmatter_safe():
    codec = YamlCodec()

    # BOM handling
    res = codec.parse_yaml_frontmatter_safe("\ufeff---\nkey: value\n---")
    assert res == {"key": "value"}

    # Invalid start
    with pytest.raises(ValueError, match="must start with '---'"):
        codec.parse_yaml_frontmatter_safe("key: value\n---")

    # Invalid end
    with pytest.raises(ValueError, match="must end with '---'"):
        codec.parse_yaml_frontmatter_safe("---\nkey: value")

    # Empty frontmatter
    assert codec.parse_yaml_frontmatter_safe("---\n\n---") == {}

    # Duplicate key
    with pytest.raises(ValueError, match="YAML parsing error"):
        codec.parse_yaml_frontmatter_safe("---\nkey: val1\nkey: val2\n---")

    # Non-dict frontmatter
    with pytest.raises(ValueError, match="Frontmatter must be a key-value dictionary"):
        codec.parse_yaml_frontmatter_safe("---\n- item1\n- item2\n---")

    # General YAML syntax error
    with pytest.raises(ValueError, match="YAML parsing error"):
        codec.parse_yaml_frontmatter_safe("---\nkey: : bad\n---")

def test_yaml_codec_decode_errors():
    codec = YamlCodec()
    with pytest.raises(SemanticValidationError, match="Frontmatter must be a key-value dictionary"):
        codec.decode("- item1\n- item2")

def test_offline_tokenizer_cache_validation_failures():
    tok = OfflineTokenizer(cache_dir=None)

    # Missing env var
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(TokenizerError, match="TIKTOKEN_CACHE_DIR environment variable is not set"):
            tok.verify_tokenizer_cache()

    # Non-existent cache dir
    tok_bad = OfflineTokenizer(cache_dir="/non/existent/path/for/cida/test")
    with pytest.raises(TokenizerError, match="cache directory does not exist"):
        tok_bad.verify_tokenizer_cache()

    # Missing cache file
    tmp_dir = os.path.join(os.environ.get("TEMP", "C:/Windows/Temp"), "cida_tokenizer_test_dir")
    os.makedirs(tmp_dir, exist_ok=True)
    tok_tmp = OfflineTokenizer(cache_dir=tmp_dir)
    with pytest.raises(TokenizerError, match="Required tokenizer cache file is missing"):
        tok_tmp.verify_tokenizer_cache()

    # Invalid file size
    bad_file = os.path.join(tmp_dir, "9b5ad71b2ce5302211f9c61530b329a4922fc6a4")
    with open(bad_file, "wb") as f:
        f.write(b"too small content")
    with pytest.raises(TokenizerError, match="Tokenizer cache file is corrupted"):
        tok_tmp.verify_tokenizer_cache()

    # Hash mismatch
    with open(bad_file, "wb") as f:
        f.write(b"x" * 1681126)
    with pytest.raises(TokenizerError, match="Tokenizer cache file hash mismatch"):
        tok_tmp.verify_tokenizer_cache()

    # Cleanup bad file and tmp_dir
    os.remove(bad_file)
    os.rmdir(tmp_dir)

def test_offline_tokenizer_count_edge_cases():
    tok = OfflineTokenizer()
    assert tok.count("") == 0
    assert tok.count(None) == 0

    with patch.object(tok, 'get_encoder', side_effect=Exception("Encoding crash")):
        with pytest.raises(TokenizerError, match="Tokenizer error: Encoding crash"):
            tok.count("hello")

def test_hash_service_known_digests():
    hs = HashService()
    # Explicit known SHA-256 and SHA-1 digests for b"hello"
    assert hs.sha256(b"hello") == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    assert hs.sha256("hello") == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    assert hs.sha1(b"hello") == "aaf4c61ddcc5e8a2dabede0f3b482cd9aea9434d"
    assert hs.sha1("hello") == "aaf4c61ddcc5e8a2dabede0f3b482cd9aea9434d"

def test_filesystem_edge_cases():
    fs = PhysicalFilesystem()
    assert fs.exists(os.getcwd())
    assert not fs.exists("/non/existent/path/cida_fs_test")
