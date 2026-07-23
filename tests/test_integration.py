import unittest
import sys
import os
import shutil
import subprocess
import json
import tempfile
from unittest.mock import patch

# Configure offline tiktoken cache
dir_path = os.path.dirname(os.path.abspath(__file__))
os.environ["TIKTOKEN_CACHE_DIR"] = os.path.normpath(os.path.join(dir_path, "../resources"))

from cida.interfaces.cli import main, counter_main, translate_main  # noqa: E402
from cida.application.generate_manifest import ManifestGeneratorUsecase  # noqa: E402
from cida.application.optimize_corpus import CorpusOptimizerUsecase  # noqa: E402
from cida.application.validate_sidecar import SidecarValidatorUsecase  # noqa: E402
from cida.application.generate_report import ReportGeneratorUsecase  # noqa: E402
from cida.infrastructure.filesystem import PhysicalFilesystem  # noqa: E402
from cida.infrastructure.tokenizer import OfflineTokenizer  # noqa: E402
from cida.infrastructure.hashing import HashService  # noqa: E402
from cida.infrastructure.json_codec import JsonCodec  # noqa: E402
from cida.markdown.dictionary import CorpusDictionaryBuilder  # noqa: E402
from cida.domain.errors import SidecarValidationError  # noqa: E402

class TestPipelineIntegration(unittest.TestCase):
    def setUp(self):
        self.test_dir = os.path.abspath("tests/fixtures/integration_sandbox")
        self.src_dir = os.path.join(self.test_dir, "src")
        self.dst_dir = os.path.join(self.test_dir, "dst")

        # Clean up
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

        os.makedirs(self.src_dir, exist_ok=True)
        os.makedirs(self.dst_dir, exist_ok=True)

        # Create mock files
        with open(os.path.join(self.src_dir, "A.java"), "w", encoding="utf-8") as f:
            f.write("package com.test;\npublic class A {\n    // comment\n    public void test() {}\n}")

        with open(os.path.join(self.src_dir, "B.java"), "w", encoding="utf-8") as f:
            f.write("package com.test;\npublic class B {\n    public void other() {}\n}")

        with open(os.path.join(self.src_dir, "workflow.md"), "w", encoding="utf-8") as f:
            f.write("# Workflow\n\nThis is workflow content with must option.\n")

        with open(os.path.join(self.src_dir, "step-01.md"), "w", encoding="utf-8") as f:
            f.write("# Step 1\n\nSome step description.\n")

        with open(os.path.join(self.src_dir, "logo.png"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")

        with open(os.path.join(self.src_dir, "config.json"), "w", encoding="utf-8") as f:
            f.write('{"unsupported": true}')

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_integration_pipeline_run(self):
        # Run go program motor_v3.go
        cmd = [
            "go", "run", "motor_v3.go",
            self.src_dir,
            self.dst_dir,
            "--profile", "auto",
            "--dictionary-scope", "file",
            "--report", "both"
        ]

        # Setup Tiktoken cache dir env variable
        env = os.environ.copy()
        env["TIKTOKEN_CACHE_DIR"] = os.path.abspath("resources")

        result = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, f"Execution failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")

        # Check files exist in destination
        self.assertTrue(os.path.exists(os.path.join(self.dst_dir, "A.java.tknc")))
        self.assertTrue(os.path.exists(os.path.join(self.dst_dir, "B.java.tknc")))
        self.assertTrue(os.path.exists(os.path.join(self.dst_dir, "workflow.md")))
        self.assertTrue(os.path.exists(os.path.join(self.dst_dir, "step-01.md")))
        self.assertTrue(os.path.exists(os.path.join(self.dst_dir, "logo.png")))
        self.assertTrue(os.path.exists(os.path.join(self.dst_dir, "config.json")))

        # Check that binary and unsupported files were copied without change
        with open(os.path.join(self.src_dir, "logo.png"), "rb") as sf, open(os.path.join(self.dst_dir, "logo.png"), "rb") as df:
            self.assertEqual(sf.read(), df.read())

        with open(os.path.join(self.src_dir, "config.json"), "rb") as sf, open(os.path.join(self.dst_dir, "config.json"), "rb") as df:
            self.assertEqual(sf.read(), df.read())

        # Check report
        report_json_path = os.path.join(self.dst_dir, "report.json")
        self.assertTrue(os.path.exists(report_json_path))

        with open(report_json_path, "r", encoding="utf-8") as rf:
            entries = json.load(rf)

        # We expect entries for: A.java, B.java, workflow.md, step-01.md
        # Logo.png and config.json should not be treated as text optimization entries
        self.assertEqual(len(entries), 4)

        processed_files = [os.path.basename(e["arquivo"]) for e in entries]
        self.assertIn("A.java", processed_files)
        self.assertIn("B.java", processed_files)
        self.assertIn("workflow.md", processed_files)
        self.assertIn("step-01.md", processed_files)

        # Verify no duplicates
        self.assertEqual(len(processed_files), len(set(processed_files)))

        # Verify reported contents correspond to final files on disk
        for e in entries:
            rel_path = e["arquivo"]
            dest_file = os.path.join(self.dst_dir, rel_path)
            if e["perfil"] == "java":
                dest_file += ".tknc"
            self.assertTrue(os.path.exists(dest_file))
            with open(dest_file, "r", encoding="utf-8") as df:
                disk_content = df.read()

            # Verify that the final token count matches what is on disk
            # Calculate tokens using the tiktoken offline cache
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
            disk_tokens = len(enc.encode(disk_content))
            self.assertEqual(e["tokens_novos"], disk_tokens)


class TestPythonUsecases(unittest.TestCase):
    """Integration tests for Python usecases."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.src_dir = os.path.join(self.temp_dir, "src")
        self.dst_dir = os.path.join(self.temp_dir, "dst")
        os.makedirs(self.src_dir, exist_ok=True)
        os.makedirs(self.dst_dir, exist_ok=True)

        # Write dummy files with repeated long words to trigger dictionary creation
        long_word_content = "supercalifragilisticexpialidocious " * 100
        with open(os.path.join(self.src_dir, "file1.md"), "w", encoding="utf-8") as f:
            f.write(f"# Hello\nThis is a test file for minification.\n{long_word_content}\n")

        with open(os.path.join(self.src_dir, "file2.txt"), "w", encoding="utf-8") as f:
            f.write(f"Some text here\n{long_word_content}\n")

        # Write a dummy binary file to trigger binary file path in optimizer loop
        with open(os.path.join(self.src_dir, "file3.md"), "wb") as f:
            f.write(b"\x00\x00\x00BinaryData")

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_manifest_generator(self):
        repo = PhysicalFilesystem()
        hasher = HashService()
        json_codec = JsonCodec()
        usecase = ManifestGeneratorUsecase(repo, hasher, json_codec)

        manifest = usecase.generate_tree_manifest(self.src_dir)
        self.assertIn("files", manifest)
        self.assertIn("tree_sha256", manifest)
        # file1.md, file2.txt, file3.md
        self.assertEqual(len(manifest["files"]), 3)

    def test_report_generator_usecase(self):
        repo = PhysicalFilesystem()
        json_codec = JsonCodec()
        report = ReportGeneratorUsecase(repo, json_codec)

        report.add_entry(
            filepath=os.path.join(self.src_dir, "file1.md"),
            profile="markdown",
            tokens_orig=100,
            tokens_base=50,
            tokens_new=60,
            dict_included=True,
            tokens_sidecar=10,
            tokens_aux=5,
            accepted_transforms=["trim"],
            rejected_transforms=[],
            semantic_status="SUCCESS",
            execution_time=0.1
        )

        md = report.generate_markdown(deterministic=True)
        self.assertIn("file1.md", md)

        out_json = os.path.join(self.temp_dir, "report.json")
        out_md = os.path.join(self.temp_dir, "report.md")

        report.save_reports(out_md, out_json, self.src_dir, "both")
        self.assertTrue(os.path.exists(out_json))
        self.assertTrue(os.path.exists(out_md))

    def test_sidecar_validator_usecase(self):
        repo = PhysicalFilesystem()
        json_codec = JsonCodec()
        hasher = HashService()
        validator = SidecarValidatorUsecase(repo, json_codec, hasher)

        # Test validation on empty directory
        validator.verify_destination_sidecars(self.src_dir, self.dst_dir)

        # Test sidecar path mismatch/hash validation error
        invalid_sidecar_data = {
            "format": "cida-token-sidecar",
            "version": 1,
            "source": "file1.md",
            "source_sha256": "invalid_hash_value_here_that_does_not_match",
            "entries": {"A": "val"}
        }

        # Write the invalid sidecar to dst_dir
        sidecar_path = os.path.join(self.dst_dir, "file1.cidatkn")
        with open(sidecar_path, "w", encoding="utf-8") as f:
            json.dump(invalid_sidecar_data, f)

        with self.assertRaises(SidecarValidationError):
            validator.verify_destination_sidecars(self.src_dir, self.dst_dir)

    def test_corpus_optimizer_usecase(self):
        token_counter = OfflineTokenizer()
        repo = PhysicalFilesystem()
        hasher = HashService()
        json_codec = JsonCodec()
        dictionary_builder = CorpusDictionaryBuilder()

        corpus_opt = CorpusOptimizerUsecase(token_counter, repo, hasher, json_codec, dictionary_builder)
        files = [os.path.join(self.src_dir, "file1.md"), os.path.join(self.src_dir, "file2.txt")]

        c_dict, c_hash, sidecar_t, aux_t = corpus_opt.build_corpus_dict(files, self.src_dir)
        self.assertNotEqual(c_dict, {})
        corpus_opt.write_corpus_sidecars(c_dict, c_hash, self.dst_dir)
        self.assertTrue(os.path.exists(os.path.join(self.dst_dir, "tknd")))

    def test_cli_main_file_scope(self):
        args = [
            "token_optimizer.py",
            "--src", self.src_dir,
            "--dst", self.dst_dir,
            "--profile", "markdown",
            "--dictionary-scope", "file",
            "--report", "both",
            "--report-path", os.path.join(self.temp_dir, "report_cli")
        ]
        with patch.object(sys, "argv", args):
            main()

    def test_cli_main_corpus_scope(self):
        args = [
            "token_optimizer.py",
            "--src", self.src_dir,
            "--dst", self.dst_dir,
            "--profile", "auto",
            "--dictionary-scope", "corpus",
            "--report", "both",
            "--verify-semantics",
            "--fail-on-inflation",
            "--report-path", os.path.join(self.temp_dir, "report_cli_corpus")
        ]
        with patch.object(sys, "argv", args):
            main()

    def test_cli_main_dry_run_and_errors(self):
        # Dry-run execution
        args_dry = [
            "token_optimizer.py",
            "--src", self.src_dir,
            "--dst", self.dst_dir,
            "--profile", "markdown",
            "--dry-run"
        ]
        with patch.object(sys, "argv", args_dry):
            main()

        # Missing args
        args_invalid = ["token_optimizer.py"]
        with patch.object(sys, "argv", args_invalid):
            with self.assertRaises(SystemExit):
                main()

        # Missing directory trigger code 4
        args_missing_dir = [
            "token_optimizer.py",
            "--src", os.path.join(self.temp_dir, "does_not_exist"),
            "--dst", self.dst_dir
        ]
        with patch.object(sys, "argv", args_missing_dir):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 4)

    @patch("sys.stdin")
    def test_counter_main(self, mock_stdin):
        mock_stdin.read.return_value = "Hello world!"
        args = ["token_counter.py"]
        with patch.object(sys, "argv", args):
            counter_main()

    def test_translate_main(self):
        tknd_path = os.path.join(self.temp_dir, "tknd")
        os.makedirs(tknd_path, exist_ok=True)

        args = [
            "translate.py",
            "A0",
            "--path", tknd_path
        ]
        with patch.object(sys, "argv", args):
            with patch("builtins.print") as mock_print:
                translate_main()
                mock_print.assert_called_once_with({"A0": "Não encontrado"})


if __name__ == "__main__":
    unittest.main()
