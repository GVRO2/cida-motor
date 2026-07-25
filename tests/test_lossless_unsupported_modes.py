import pytest
import sys
import subprocess
from cida.domain.errors import UsageError
from cida.domain.policies import validate_mode_profile_combination

def test_lossless_code_profile_rejected():
    with pytest.raises(UsageError) as exc:
        validate_mode_profile_combination("lossless", "code", "file")
    assert "Lossless mode currently supports only Markdown and BMAD profiles" in str(exc.value)

def test_lossless_java_profile_rejected():
    with pytest.raises(UsageError) as exc:
        validate_mode_profile_combination("lossless", "java", "file")
    assert "Lossless mode currently supports only Markdown and BMAD profiles" in str(exc.value)

def test_lossless_auto_detecting_code_rejected():
    with pytest.raises(UsageError) as exc:
        validate_mode_profile_combination("lossless", "auto", "file", detected_profile="code")
    assert "Lossless mode currently supports only Markdown and BMAD profiles" in str(exc.value)

def test_lossless_auto_detecting_java_rejected():
    with pytest.raises(UsageError) as exc:
        validate_mode_profile_combination("lossless", "auto", "file", detected_profile="java")
    assert "Lossless mode currently supports only Markdown and BMAD profiles" in str(exc.value)

def test_lossless_corpus_dictionary_rejected():
    with pytest.raises(UsageError) as exc:
        validate_mode_profile_combination("lossless", "markdown", "corpus")
    assert "Corpus dictionary is not currently supported in lossless mode" in str(exc.value)

def test_semantic_code_allowed():
    validate_mode_profile_combination("semantic", "code", "file")
    validate_mode_profile_combination("semantic", "java", "file")
    validate_mode_profile_combination("semantic", "auto", "corpus", detected_profile="code")

def test_lossless_markdown_allowed():
    validate_mode_profile_combination("lossless", "markdown", "file")
    validate_mode_profile_combination("lossless", "bmad", "file")

def test_cli_e2e_lossless_code_rejected(tmp_path):
    src_file = tmp_path / "app.py"
    src_file.write_text("def hello(): pass\n", encoding="utf-8")
    dst_dir = tmp_path / "dst"

    cmd = [
        sys.executable, "-m", "cida.interfaces.cli",
        "--src", str(src_file),
        "--dst", str(dst_dir),
        "--mode", "lossless",
        "--profile", "code"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 1
    assert "Lossless mode currently supports only Markdown and BMAD profiles" in res.stderr
    assert not dst_dir.exists()

def test_cli_e2e_lossless_corpus_rejected(tmp_path):
    src_file = tmp_path / "doc.md"
    src_file.write_text("# Title\nContent\n", encoding="utf-8")
    dst_dir = tmp_path / "dst"

    cmd = [
        sys.executable, "-m", "cida.interfaces.cli",
        "--src", str(src_file),
        "--dst", str(dst_dir),
        "--mode", "lossless",
        "--dictionary-scope", "corpus"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 1
    assert "Corpus dictionary is not currently supported in lossless mode" in res.stderr
    assert not dst_dir.exists()
