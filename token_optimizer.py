from cida.interfaces.cli import main
from cida.markdown.transforms import (
    minificar_codigo_para_ia, remove_html_comments, trim_trailing_whitespace,
    normalize_newlines, table_whitespace, list_compaction
)
from cida.markdown.dictionary import build_corpus_dictionary as build_corpus_dict_pure
from cida.application.optimize_file import FileOptimizerUsecase
from cida.infrastructure.filesystem import PhysicalFilesystem
from cida.infrastructure.tokenizer import OfflineTokenizer
from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec

__all__ = [
    "main",
    "detect_profile",
    "build_corpus_dictionary",
    "is_binary_file",
    "optimize_markdown_dictionary_file_scope",
    "minificar_codigo_para_ia",
    "remove_html_comments",
    "trim_trailing_whitespace",
    "normalize_newlines",
    "table_whitespace",
    "list_compaction",
]

def detect_profile(filepath, content):
    opt = FileOptimizerUsecase(OfflineTokenizer(), PhysicalFilesystem(), HashService(), JsonCodec())
    return opt.detect_profile(filepath, content)

def build_corpus_dictionary(all_files_content, min_margin=5):
    return build_corpus_dict_pure(all_files_content, OfflineTokenizer(), min_margin)

def is_binary_file(filepath):
    fs = PhysicalFilesystem()
    return fs.is_binary_file(filepath)

def optimize_markdown_dictionary_file_scope(content, transformed_text, filepath, verify_semantics):
    opt = FileOptimizerUsecase(OfflineTokenizer(), PhysicalFilesystem(), HashService(), JsonCodec())
    return opt.optimize_markdown_dictionary_file_scope(content, transformed_text, filepath, verify_semantics)

if __name__ == "__main__":
    main()
