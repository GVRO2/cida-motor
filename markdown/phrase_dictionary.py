from cida.markdown.dictionary import generate_alias_candidates, find_candidate_words, build_file_dictionary as build_file_dict_pure, apply_dictionary
from cida.infrastructure.tokenizer import OfflineTokenizer

_tokenizer = OfflineTokenizer()

__all__ = [
    "get_encoder",
    "count_tokens",
    "generate_alias_candidates",
    "find_candidate_words",
    "build_file_dictionary",
    "apply_dictionary",
]

def get_encoder():
    return _tokenizer.get_encoder()

def count_tokens(text):
    return _tokenizer.count(text)

def build_file_dictionary(text, protected_manager, min_margin=5):
    return build_file_dict_pure(text, protected_manager, _tokenizer, min_margin)
