import os
import pytest
from cida.markdown.dictionary import (
    generate_alias_candidates, find_candidate_words, build_file_dictionary, build_corpus_dictionary, apply_dictionary
)
from cida.markdown.parser import parse_markdown
from cida.markdown.semantic_equivalence import validate_semantics
from cida.markdown.protected_regions import ProtectedRegionsManager
from cida.infrastructure.tokenizer import OfflineTokenizer

@pytest.fixture(autouse=True)
def setup_env():
    old_val = os.environ.get("TIKTOKEN_CACHE_DIR")
    os.environ["TIKTOKEN_CACHE_DIR"] = os.path.abspath("resources")
    yield
    if old_val is not None:
        os.environ["TIKTOKEN_CACHE_DIR"] = old_val
    else:
        os.environ.pop("TIKTOKEN_CACHE_DIR", None)

def test_generate_alias_candidates_limit_and_3letter():
    cands = generate_alias_candidates(set(), limit=10)
    assert len(cands) == 10

    cands_large = generate_alias_candidates(set(), limit=2000)
    assert len(cands_large) == 2000
    assert any(len(c) == 3 for c in cands_large)

def test_find_candidate_words():
    text = "word short longer_word_candidate regular_word"
    candidates = find_candidate_words(text)
    assert "longer_word_candidate" in candidates
    assert "regular_word" in candidates
    assert "short" not in candidates

def test_build_file_dictionary_no_gain_and_empty():
    tok = OfflineTokenizer()
    pm = ProtectedRegionsManager()

    dict1, header1 = build_file_dictionary("short text", pm, tok)
    assert dict1 == {}
    assert header1 == ""

    assert apply_dictionary("some text", {}, pm) == "some text"

def test_build_corpus_dictionary_gain():
    tok = OfflineTokenizer()
    doc1 = "This is longer_word_candidate repeated. " * 10
    doc2 = "Another longer_word_candidate text. " * 10
    corpus = [doc1, doc2]

    cdict = build_corpus_dictionary(corpus, tok, min_margin=1)
    assert "longer_word_candidate" in cdict

    doc_single = "word_unique_test " * 2
    cdict_low = build_corpus_dictionary([doc_single], tok, min_margin=100)
    assert cdict_low == {}

def test_parse_markdown_blocks():
    text_table = "| Col 1 | Col 2 |\n| --- | --- |\n| val 1 | val 2 |\n"
    blocks_table = parse_markdown(text_table)
    assert any(b.type == "table" for b in blocks_table)

    text_bq = "> line 1\n> line 2\n"
    blocks_bq = parse_markdown(text_bq)
    assert any(b.type == "blockquote" for b in blocks_bq)

    text_hr = "Some text\n\n---\n\nMore text\n"
    blocks_hr = parse_markdown(text_hr)
    assert any(b.type in ["hr", "frontmatter"] for b in blocks_hr)

def test_parse_markdown_unclosed_fence():
    text = "---\nkey: val\n---\n```python\ndef foo(): pass\n"
    with pytest.raises(ValueError, match="Fenced code block not closed"):
        parse_markdown(text)

def test_validate_semantics_edge_cases():
    orig = "# Header\n\n- item 1\n- item 2\n"
    mini = "# Header\n\n- item 1\n- item 2"
    is_valid, _ = validate_semantics(orig, mini)
    assert is_valid is True

    bad_mini = "# Header\n\n- item 1\n- item 3"
    bad_valid, msg = validate_semantics(orig, bad_mini)
    assert bad_valid is False

    orig_b = "# H1\n\nParagraph 1\n\nParagraph 2\n"
    mini_b = "# H1\n\nParagraph 1\n"
    val_b, _ = validate_semantics(orig_b, mini_b)
    assert val_b is False

    orig_h = "# Header One\n\nText"
    mini_h = "# Header Two\n\nText"
    val_h, _ = validate_semantics(orig_h, mini_h)
    assert val_h is False

    orig_c = "```python\nprint(1)\n```"
    mini_c = "```ruby\nprint(1)\n```"
    val_c, _ = validate_semantics(orig_c, mini_c)
    assert val_c is False

    orig_t = "| A | B |\n|---|---|\n| 1 | 2 |"
    mini_t = "| A |\n|---|\n| 1 |"
    val_t, _ = validate_semantics(orig_t, mini_t)
    assert val_t is False

    orig_f = "---\na: 1\nb: 2\n---\nText"
    mini_f = "---\na: 1\nc: 2\n---\nText"
    val_f, _ = validate_semantics(orig_f, mini_f)
    assert val_f is False
