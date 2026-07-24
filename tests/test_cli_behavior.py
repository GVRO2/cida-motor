import os
import sys
import pytest
from unittest.mock import patch
from cida.domain.errors import TokenizerError
from cida.interfaces.cli import counter_main, translate_main, main

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
