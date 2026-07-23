import unittest
import os
import subprocess
import sys
import tempfile
import shutil
import time

class TestCLIExitCodes(unittest.TestCase):
    def setUp(self):
        dir_path = os.path.dirname(os.path.abspath(__file__))
        self.project_root = os.path.normpath(os.path.join(dir_path, "../"))
        self.temp_dir = tempfile.mkdtemp()
        self.go_cli = os.path.join(self.temp_dir, "motor_v3.exe" if sys.platform == "win32" else "motor_v3")
        self.tool_root = self.temp_dir

        for filename in ("token_counter.py", "token_optimizer.py", "translate.py"):
            shutil.copy2(
                os.path.join(self.project_root, filename),
                os.path.join(self.tool_root, filename),
            )
        for dirname in ("cida", "markdown", "resources"):
            shutil.copytree(
                os.path.join(self.project_root, dirname),
                os.path.join(self.tool_root, dirname),
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )

        # Build Go binary unconditionally to ensure latest changes are included
        env = os.environ.copy()
        env.setdefault("GOCACHE", os.path.join(self.temp_dir, "go-build-cache"))
        last_result = None
        for attempt in range(3):
            last_result = subprocess.run(
                ["go", "build", "-o", self.go_cli, "motor_v3.go"],
                cwd=self.project_root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if last_result.returncode == 0:
                break
            if attempt < 2 and b"Access is denied" in last_result.stderr:
                time.sleep(0.5)
                continue
            break
        self.assertIsNotNone(last_result)
        self.assertEqual(
            last_result.returncode,
            0,
            last_result.stderr.decode("utf-8", errors="replace"),
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def run_cli(self, args, **kwargs):
        kwargs.setdefault("cwd", self.tool_root)
        return subprocess.run([self.go_cli, *args], **kwargs)

    def test_exit_code_0_success(self):
        # Normal successful execution
        src = os.path.join(self.temp_dir, "src")
        dst = os.path.join(self.temp_dir, "dst")
        os.makedirs(src)
        with open(os.path.join(src, "file.md"), "w", encoding="utf-8") as f:
            f.write("# Title\n" + "test " * 100)

        res = self.run_cli([src, dst, "--profile", "markdown"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(res.returncode, 0)
        self.assertTrue(os.path.exists(os.path.join(dst, "file.md")))

    def test_exit_code_1_no_args(self):
        res = self.run_cli([], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(res.returncode, 1)

    def test_exit_code_1_invalid_flag(self):
        res = self.run_cli(["src", "--unknown-flag"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(res.returncode, 1)

    def test_exit_code_1_invalid_profile(self):
        res = self.run_cli(["src", "--profile=invalid"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(res.returncode, 1)

    def test_exit_code_1_invalid_dict_scope(self):
        res = self.run_cli(["src", "--dictionary-scope=invalid"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(res.returncode, 1)

    def test_exit_code_1_invalid_report(self):
        res = self.run_cli(["src", "--report=invalid"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(res.returncode, 1)

    def test_exit_code_2_tokenizer_missing(self):
        # We temporarily rename token_counter.py to simulate tokenizer missing
        counter_path = os.path.join(self.tool_root, "token_counter.py")
        counter_bak = os.path.join(self.tool_root, "token_counter.py.bak")
        try:
            if os.path.exists(counter_path):
                os.rename(counter_path, counter_bak)

            src = os.path.join(self.temp_dir, "src")
            os.makedirs(src, exist_ok=True)
            with open(os.path.join(src, "App.java"), "w", encoding="utf-8") as f:
                f.write("public class App { public static void main(String[] args) {} }")

            # Running tokenizer-based count should now fail and exit with code 2
            res = self.run_cli([src, os.path.join(self.temp_dir, "dst")], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(res.returncode, 2)
        finally:
            if os.path.exists(counter_bak):
                os.rename(counter_bak, counter_path)

    def test_exit_code_2_invalid_tokenizer_output(self):
        # Mock token_counter.py to output non-numeric
        counter_path = os.path.join(self.tool_root, "token_counter.py")
        with open(counter_path, "rb") as f:
            original_code = f.read()
        try:
            with open(counter_path, "wb") as f:
                f.write(b"print('not_numeric')\n")

            src = os.path.join(self.temp_dir, "src")
            os.makedirs(src, exist_ok=True)
            with open(os.path.join(src, "App.java"), "w", encoding="utf-8") as f:
                f.write("public class App { public static void main(String[] args) {} }")

            res = self.run_cli([src, os.path.join(self.temp_dir, "dst")], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(res.returncode, 2)
        finally:
            with open(counter_path, "wb") as f:
                f.write(original_code)

    def test_exit_code_3_semantic_fail(self):
        # Create a file with duplicate keys in YAML frontmatter to force a semantic check failure
        src = os.path.join(self.temp_dir, "src")
        os.makedirs(src, exist_ok=True)
        with open(os.path.join(src, "file.md"), "w", encoding="utf-8") as f:
            f.write("---\ntitle: test\ntitle: test2\n---\n" + "test " * 100)

        res = self.run_cli([src, os.path.join(self.temp_dir, "dst"), "--verify-semantics"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(res.returncode, 3)

    def test_exit_code_4_not_found(self):
        res = self.run_cli(["non_existent_folder_xyz"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(res.returncode, 4)

    def test_exit_code_5_invalid_sidecar(self):
        # We pre-create an invalid sidecar file in the destination
        src = os.path.join(self.temp_dir, "src")
        dst = os.path.join(self.temp_dir, "dst")
        os.makedirs(src)
        os.makedirs(dst)
        os.makedirs(os.path.join(dst, "tknd"))

        with open(os.path.join(src, "file.md"), "w", encoding="utf-8") as f:
            f.write("test " * 100)

        # Write corrupted JSON to sidecar
        with open(os.path.join(dst, "tknd/A0.cidatkn"), "w", encoding="utf-8") as f:
            f.write("{invalid json")

        res = self.run_cli([src, dst, "--profile", "markdown"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(res.returncode, 5)

    def test_exit_code_6_internal_error(self):
        # Clear PATH to prevent Go CLI from starting Python
        src = os.path.join(self.temp_dir, "src")
        os.makedirs(src)
        with open(os.path.join(src, "file.md"), "w", encoding="utf-8") as f:
            f.write("test " * 100)

        env = os.environ.copy()
        # Set PATH to empty to prevent subprocess execution
        env["PATH"] = ""
        res = self.run_cli([src, os.path.join(self.temp_dir, "dst")], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # Should exit with code 6 due to start/run failure of the python subprocess
        self.assertEqual(res.returncode, 6)

if __name__ == "__main__":
    unittest.main()
