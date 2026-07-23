import os
import sys
import subprocess
import json
import hashlib
import shutil
import tempfile
import argparse

def calculate_file_sha256(filepath):
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def calculate_file_sha1(filepath):
    sha1_hash = hashlib.sha1()
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha1_hash.update(byte_block)
    return sha1_hash.hexdigest()

def main():
    parser = argparse.ArgumentParser(description="Deterministic CIDA Motor Benchmark Runner")
    parser.add_argument("--manifest-out", help="Path to write the benchmark manifest json file")
    args_parsed = parser.parse_args()

    dir_path = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.normpath(os.path.join(dir_path, "../"))

    # Configure offline environment
    env = os.environ.copy()
    env["TIKTOKEN_CACHE_DIR"] = os.path.join(project_root, "resources")

    print("==============================================================")
    print("RUNNING CIDA MOTOR BENCHMARK SUITE (DETERMINISTIC VERIFICATION)")
    print("==============================================================\n")

    # 1. Tokenizer Offline Integrity Check
    cache_file = os.path.join(project_root, "resources/9b5ad71b2ce5302211f9c61530b329a4922fc6a4")
    if not os.path.exists(cache_file):
        cache_file = os.path.join(project_root, "resources/cl100k_base.tiktoken")

    if not os.path.exists(cache_file):
        print("Error: cl100k_base tokenizer offline cache not found.")
        sys.exit(1)
    actual_hash = calculate_file_sha1(cache_file)
    expected_hashes = ["9b5ad71b2ce5302211f9c61530b329a4922fc6a4", "6494e42d5aad2bbb441ea9793af9e7db335c8d9c", "86ac4193f03c2214c96a388affad156a9776e42e"]
    if actual_hash not in expected_hashes:
        print(f"Error: Tokenizer cache integrity verification failed. Got {actual_hash}")
        sys.exit(1)
    print("[OK] Tokenizer offline cache integrity verified.")

    # 2. Build Go binary to a temporary directory
    temp_bin_dir = tempfile.mkdtemp(prefix="cida-benchmark-bin-")
    temp_run_dir = tempfile.mkdtemp(prefix="cida-benchmark-runs-")

    go_cli = os.path.join(temp_bin_dir, "motor_v3.exe" if sys.platform == "win32" else "motor_v3")

    try:
        print(f"Building Go binary in temp dir: {go_cli}...")
        build_cmd = ["go", "build", "-o", go_cli, "motor_v3.go"]
        build_res = subprocess.run(build_cmd, cwd=project_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if build_res.returncode != 0:
            print(f"Error building Go binary:\n{build_res.stderr.decode('utf-8')}")
            sys.exit(1)
        print("[OK] Go binary built successfully.")

        fixtures_dir = os.path.join(project_root, "tests/fixtures/bmad_project")
        output_dir_1 = os.path.join(temp_run_dir, "run1")
        output_dir_2 = os.path.join(temp_run_dir, "run2")

        os.makedirs(output_dir_1, exist_ok=True)
        os.makedirs(output_dir_2, exist_ok=True)

        # 3. Execution - RUN 1
        print("Running minifier: Run 1...")
        cmd_1 = [go_cli, fixtures_dir, output_dir_1, "--profile", "auto", "--dictionary-scope", "file"]
        res_1 = subprocess.run(cmd_1, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        if res_1.returncode != 0:
            sys.stderr.buffer.write(f"Run 1 failed:\n{res_1.stderr}\n".encode("utf-8"))
            sys.exit(1)

        # 4. Execution - RUN 2
        print("Running minifier: Run 2...")
        cmd_2 = [go_cli, fixtures_dir, output_dir_2, "--profile", "auto", "--dictionary-scope", "file"]
        res_2 = subprocess.run(cmd_2, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        if res_2.returncode != 0:
            sys.stderr.buffer.write(f"Run 2 failed:\n{res_2.stderr}\n".encode("utf-8"))
            sys.exit(1)

        # 5. Compare Run 1 vs Run 2 Outputs (Strict Determinism Verification)
        print("\nVerifying outputs determinism using Tree Manifests...")

        def generate_tree_manifest(output_dir):
            files_info = []
            for root, _, files in os.walk(output_dir):
                for f in files:
                    filepath = os.path.join(root, f)
                    rel_path = os.path.relpath(filepath, output_dir).replace('\\', '/')
                    sha = calculate_file_sha256(filepath)
                    size = os.path.getsize(filepath)
                    files_info.append({
                        "path": rel_path,
                        "sha256": sha,
                        "size": size
                    })
            # Sort deterministically
            files_info.sort(key=lambda x: x["path"])
            manifest = {"files": files_info}
            manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode('utf-8')
            tree_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
            manifest["tree_sha256"] = tree_sha256
            return manifest, tree_sha256

        manifest_1, hash_1 = generate_tree_manifest(output_dir_1)
        manifest_2, hash_2 = generate_tree_manifest(output_dir_2)

        print(f"Run 1 Tree SHA256: {hash_1}")
        print(f"Run 2 Tree SHA256: {hash_2}")

        if hash_1 != hash_2:
            print("Error: Determinism violation. Tree SHA256 hashes do not match.")
            print(f"Run 1 files: {manifest_1['files']}")
            print(f"Run 2 files: {manifest_2['files']}")
            sys.exit(1)

        print("[OK] Output binary determinism verified.")

        # 6. Verify variable data absence in versioned report.json
        report_json_path = os.path.join(output_dir_1, "report.json")
        if not os.path.exists(report_json_path):
            print("Error: report.json was not generated in Run 1.")
            sys.exit(1)

        with open(report_json_path, 'r', encoding='utf-8') as f:
            entries = json.load(f)

        for e in entries:
            # Verify relative path is used
            if os.path.isabs(e["arquivo"]):
                print(f"Error: Absolute path found in report: {e['arquivo']}")
                sys.exit(1)
            # Verify execution time is zeroed
            if e["tempo_de_execução"] != 0.0:
                print(f"Error: Non-zero execution time found in report: {e['tempo_de_execução']}")
                sys.exit(1)

        # Verify no absolute path separators or typical path indicators like 'IdeaProjects'
        report_content = open(report_json_path, 'r', encoding='utf-8').read()
        if "IdeaProjects" in report_content or "Users" in report_content or "\\" in report_content:
            print("Error: Variable environment path info detected in versioned report.json.")
            sys.exit(1)

        print("[OK] Report determinism verified.")
        print("\nDeterminism verification: SUCCESS")

        # Save manifest to requested location
        if args_parsed.manifest_out:
            # Fetch git commit hash
            try:
                commit_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
            except Exception:
                commit_sha = "unknown"

            platform = "windows" if sys.platform == "win32" else "ubuntu"

            manifest_to_save = {
                "schema_version": 1,
                "commit_sha": commit_sha,
                "platform": platform,
                "files": manifest_1["files"],
                "tree_sha256": hash_1
            }

            os.makedirs(os.path.dirname(os.path.abspath(args_parsed.manifest_out)), exist_ok=True)
            with open(args_parsed.manifest_out, 'w', encoding='utf-8') as mf:
                json.dump(manifest_to_save, mf, indent=4, ensure_ascii=False)
            print(f"[OK] Saved platform manifest to {args_parsed.manifest_out}")

        # Print summary table
        print("\nSummary Results:")
        print("-" * 105)
        print(f"{'File':<25} | {'Profile':<10} | {'Orig Tokens':<12} | {'Base Tokens':<12} | {'New Tokens':<12} | {'Gain':<8} | {'Gain %':<8} | {'Status':<10}")
        print("-" * 105)

        total_orig = 0
        total_new = 0

        for e in entries:
            filename = os.path.basename(e["arquivo"])
            total_orig += e["tokens_originais"]
            total_new += e["tokens_minificados"]
            print(f"{filename:<25} | {e['perfil']:<10} | {e['tokens_originais']:<12} | {e['tokens_baseline']:<12} | {e['tokens_minificados']:<12} | {e['economia_liquida']:<8} | {e['economia_liquida_percentual']:>6.2f}% | {e['status_semântico']:<10}")

        print("-" * 105)
        overall_gain_abs = total_orig - total_new
        overall_gain_pct = (overall_gain_abs / total_orig * 100) if total_orig > 0 else 0
        print(f"{'OVERALL':<25} | {'':<10} | {total_orig:<12} | {'':<12} | {total_new:<12} | {overall_gain_abs:<8} | {overall_gain_pct:>6.2f}% |")
        print("-" * 105)

    finally:
        # Clean temporary directories completely
        shutil.rmtree(temp_bin_dir, ignore_errors=True)
        shutil.rmtree(temp_run_dir, ignore_errors=True)

if __name__ == "__main__":
    main()
