import os
import sys
import pytest
from unittest.mock import MagicMock, patch
from cida.domain.errors import TokenizerError
from cida.application.strict_auditing import StrictBundleAuditor
from cida.interfaces.cli import (
    counter_main, translate_main, main, _accept_token_reducing_candidate,
    _load_semantic_dependencies, _load_strict_bundle_auditor,
    _requires_identity_semantic_validation,
)

@pytest.fixture(autouse=True)
def setup_env():
    old_val = os.environ.get("TIKTOKEN_CACHE_DIR")
    os.environ["TIKTOKEN_CACHE_DIR"] = os.path.abspath("resources")
    yield
    if old_val is not None:
        os.environ["TIKTOKEN_CACHE_DIR"] = old_val
    else:
        os.environ.pop("TIKTOKEN_CACHE_DIR", None)

def test_counter_main_success():
    with patch("sys.stdin.read", return_value="test text"), \
         patch("builtins.print") as mock_print:
        counter_main()
        mock_print.assert_called_once_with(2)

def test_counter_main_cida_error():
    with patch("sys.stdin.read", side_effect=TokenizerError("mock failure")), \
         patch("sys.exit") as mock_exit:
        counter_main()
        mock_exit.assert_called_with(2)

def test_counter_main_generic_error():
    with patch("sys.stdin.read", side_effect=Exception("crash")), \
         patch("sys.exit") as mock_exit:
        counter_main()
        mock_exit.assert_called_with(6)


def test_accept_token_reducing_candidate_keeps_token_state_consistent():
    token_counter = MagicCounter({"short": 1, "same tokens": 5})

    text, tokens, accepted = _accept_token_reducing_candidate("current text", 3, "short", token_counter)

    assert accepted is True
    assert text == "short"
    assert tokens == 1
    assert token_counter.calls == ["short"]

    text, tokens, accepted = _accept_token_reducing_candidate(text, tokens, "same tokens", token_counter)

    assert accepted is False
    assert text == "short"
    assert tokens == 1
    assert token_counter.calls == ["short", "same tokens"]


def test_accept_token_reducing_candidate_rejects_identical_without_counting():
    token_counter = MagicCounter({})

    text, tokens, accepted = _accept_token_reducing_candidate("current text", 3, "current text", token_counter)

    assert accepted is False
    assert text == "current text"
    assert tokens == 3
    assert token_counter.calls == []


def test_identity_semantic_validation_detects_frontmatter_only():
    assert _requires_identity_semantic_validation("---\ntitle: test\n---\nbody") is True
    assert _requires_identity_semantic_validation("# Title\n\nbody") is False


def test_load_strict_bundle_auditor_returns_auditor_class():
    assert _load_strict_bundle_auditor() is StrictBundleAuditor


def test_load_semantic_dependencies_returns_runtime_objects():
    parsed_cls, validator = _load_semantic_dependencies()

    assert parsed_cls.__name__ == "ParsedOriginalDocument"
    assert callable(validator)


class MagicCounter:
    def __init__(self, counts):
        self.counts = counts
        self.calls = []

    def count(self, text):
        self.calls.append(text)
        return self.counts[text]

def test_translate_main_no_args():
    with patch.object(sys, "argv", ["translate.py"]), \
         pytest.raises(SystemExit) as exc:
        translate_main()
    assert exc.value.code == 1

def test_translate_main_missing_sidecar_dir():
    with patch.object(sys, "argv", ["translate.py", "AA", "--path", "/non/existent/sidecar/dir/cida"]), \
         pytest.raises(SystemExit) as exc:
        translate_main()
    assert exc.value.code == 5

def test_translate_main_with_valid_sidecar(tmp_path):
    sidecar_dir = tmp_path / "sidecar"
    sidecar_dir.mkdir()
    sidecar_file = sidecar_dir / "test.cidatkn"
    sidecar_file.write_text('{"entries": {"AA": "hello"}}')

    with patch.object(sys, "argv", ["translate.py", "AA", "BB", "--path", str(sidecar_dir)]), \
         patch("builtins.print") as mock_print:
        translate_main()
        mock_print.assert_called_with({"AA": "hello", "BB": "Não encontrado"})

def test_translate_main_with_source_sidecar(tmp_path):
    source = tmp_path / "doc.md"
    source.write_text("# Source", encoding="utf-8")
    sidecar_file = tmp_path / "doc.md.cidatkn"
    sidecar_file.write_text('{"entries": {"AA": "hello"}}', encoding="utf-8")

    with patch.object(sys, "argv", ["translate.py", "AA", "--source", str(source)]), \
         patch("builtins.print") as mock_print:
        translate_main()

    mock_print.assert_called_with({"AA": "hello"})


def test_translate_main_with_explicit_sidecar(tmp_path):
    sidecar_file = tmp_path / "dict.cidatkn"
    sidecar_file.write_text('{"entries": {"AA": "hello"}}', encoding="utf-8")

    with patch.object(sys, "argv", ["translate.py", "--sidecar", str(sidecar_file), "AA"]), \
         patch("builtins.print") as mock_print:
        translate_main()

    mock_print.assert_called_with({"AA": "hello"})


def test_translate_main_option_without_tokens_exits_usage(tmp_path):
    sidecar_dir = tmp_path / "sidecar"
    sidecar_dir.mkdir()

    with patch.object(sys, "argv", ["translate.py", "--path", str(sidecar_dir)]), \
         pytest.raises(SystemExit) as exc:
        translate_main()

    assert exc.value.code == 1


def test_translate_main_missing_explicit_sidecar(tmp_path):
    with patch.object(sys, "argv", ["translate.py", "--sidecar", str(tmp_path / "missing.cidatkn"), "AA"]), \
         pytest.raises(SystemExit) as exc:
        translate_main()

    assert exc.value.code == 5


def test_translate_main_missing_source_sidecar(tmp_path):
    source = tmp_path / "doc.md"
    source.write_text("# Source", encoding="utf-8")

    with patch.object(sys, "argv", ["translate.py", "AA", "--source", str(source)]), \
         pytest.raises(SystemExit) as exc:
        translate_main()

    assert exc.value.code == 5


def test_translate_main_alias_collision_requires_explicit_sidecar(tmp_path):
    sidecar_dir = tmp_path / "sidecar"
    sidecar_dir.mkdir()
    (sidecar_dir / "a.cidatkn").write_text('{"entries": {"AA": "hello"}}', encoding="utf-8")
    (sidecar_dir / "b.cidatkn").write_text('{"entries": {"AA": "world"}}', encoding="utf-8")

    with patch.object(sys, "argv", ["translate.py", "AA", "--path", str(sidecar_dir)]), \
         pytest.raises(SystemExit) as exc:
        translate_main()

    assert exc.value.code == 1


def test_translate_main_corrupted_sidecar(tmp_path):
    sidecar_dir = tmp_path / "sidecar"
    sidecar_dir.mkdir()
    sidecar_file = sidecar_dir / "bad.cidatkn"
    sidecar_file.write_text('corrupted json')

    with patch.object(sys, "argv", ["translate.py", "AA", "--path", str(sidecar_dir)]), \
         pytest.raises(SystemExit) as exc:
        translate_main()
    assert exc.value.code == 5

def test_cli_main_src_not_found(tmp_path):
    dst = tmp_path / "dst"
    test_args = ["cida", "--src", "/non/existent/src/cida", "--dst", str(dst)]
    with patch.object(sys, "argv", test_args), \
         patch("sys.exit") as mock_exit:
        main()
        mock_exit.assert_called_with(4)

def test_cli_main_success_file(tmp_path):
    src = tmp_path / "doc.md"
    src.write_text("# Hello World\n\nSome text content here.")
    dst = tmp_path / "dst"

    test_args = ["cida", "--src", str(src), "--dst", str(dst), "--dry-run"]
    with patch.object(sys, "argv", test_args):
        main()

def test_cli_main_java_raw_json(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    dst = tmp_path / "dst"
    java_json = tmp_path / "java_metrics.json"
    java_json.write_text('[{"filepath": "Test.java", "original_content": "class A {}", "minified_content": "class A{}", "elapsed_ns": 1000000}]')

    test_args = [
        "cida", "--src", str(src), "--dst", str(dst),
        "--java-raw-json", str(java_json), "--dry-run"
    ]
    with patch.object(sys, "argv", test_args):
        main()

def test_cli_main_corrupt_java_raw_json_warns_then_fails_empty_source(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    dst = tmp_path / "dst"
    java_json = tmp_path / "bad_java_metrics.json"
    java_json.write_text("{not-json", encoding="utf-8")

    test_args = [
        "cida", "--src", str(src), "--dst", str(dst),
        "--java-raw-json", str(java_json), "--dry-run"
    ]
    with patch.object(sys, "argv", test_args), \
         pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 4


def test_cli_main_empty_source_dir_exits_source_error(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    dst = tmp_path / "dst"

    with patch.object(sys, "argv", ["cida", "--src", str(src), "--dst", str(dst)]), \
         pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 4


def test_cli_main_corpus_scope(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    f1 = src / "doc1.md"
    f1.write_text("# Doc 1\n\n" + ("repeated_long_keyword_candidate " * 20))
    f2 = src / "doc2.md"
    f2.write_text("# Doc 2\n\n" + ("repeated_long_keyword_candidate " * 20))
    dst = tmp_path / "dst"

    test_args = [
        "cida", "--src", str(src), "--dst", str(dst),
        "--mode", "semantic",
        "--dictionary-scope", "corpus", "--report-path", str(tmp_path / "rep")
    ]
    with patch.object(sys, "argv", test_args):
        main()

def test_cli_main_code_profile(tmp_path):
    src = tmp_path / "code.py"
    src.write_text("def foo():\n    # decorative comment\n    return 42\n")
    dst = tmp_path / "dst"

    test_args = [
        "cida", "--src", str(src), "--dst", str(dst),
        "--mode", "semantic",
        "--profile", "code", "--dictionary-scope", "none"
    ]
    with patch.object(sys, "argv", test_args):
        main()

def test_cli_main_bmad_profile(tmp_path):
    src = tmp_path / "workflow.md"
    src.write_text("# Workflow BMAD\n\n<!-- stepsCompleted: 1 -->\n\n- step 1\n- step 2")
    dst = tmp_path / "dst"

    test_args = [
        "cida", "--src", str(src), "--dst", str(dst),
        "--profile", "bmad"
    ]
    with patch.object(sys, "argv", test_args):
        main()

def test_cli_main_file_dictionary(tmp_path):
    src = tmp_path / "sample.md"
    src.write_text("supercalifragilisticexpialidocious " * 100)
    dst = tmp_path / "dst"

    test_args = [
        "cida", "--src", str(src), "--dst", str(dst),
        "--mode", "lossless", "--profile", "markdown",
        "--dictionary-scope", "file", "--durable-writes", "--no-cache"
    ]
    with patch.object(sys, "argv", test_args):
        main()


def test_cli_main_does_not_repeat_final_semantic_validation(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "doc.md").write_text("# Title\n\nVisible <!-- removable comment --> text", encoding="utf-8")

    validator = MagicMock(return_value=(True, "ok"))

    class Parsed:
        def __init__(self, _content):
            pass

    test_args = [
        "cida", "--src", str(src), "--dst", str(dst),
        "--mode", "semantic", "--profile", "markdown",
        "--dictionary-scope", "none"
    ]
    with patch.object(sys, "argv", test_args), \
         patch("cida.interfaces.cli._load_semantic_dependencies") as load_deps:
        load_deps.return_value = (Parsed, validator)
        main()

    assert validator.call_count == 1


def test_cli_main_continue_on_error(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    dst = tmp_path / "dst"
    # Read-unfriendly or unprocessable directory item
    f1 = src / "file1.md"
    f1.write_text("valid file content")

    test_args = [
        "cida", "--src", str(src), "--dst", str(dst),
        "--continue-on-error"
    ]
    with patch.object(sys, "argv", test_args):
        main()

def test_cli_main_invalid_combination():
    test_args = ["cida", "--src", "s", "--dst", "d", "--mode", "lossless", "--profile", "code"]
    with patch.object(sys, "argv", test_args), \
         pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
