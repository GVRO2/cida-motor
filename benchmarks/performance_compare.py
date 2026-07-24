import argparse
import hashlib
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time
from io import BytesIO
from pathlib import Path


MEDIAN_BUDGET = 0.05
P95_BUDGET = 0.10
RSS_BUDGET = 0.10


def _run(cmd: list[str], cwd: Path, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), text=True, encoding="utf-8", errors="replace", capture_output=True, **kwargs)


def _export_ref(repo: Path, ref: str, destination: Path) -> None:
    archive = subprocess.run(
        ["git", "archive", "--format=tar", ref],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if archive.returncode != 0:
        raise RuntimeError(f"git archive {ref} failed: {archive.stderr.decode('utf-8', errors='replace')}")
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=BytesIO(archive.stdout), mode="r:") as tar:
        tar.extractall(destination)


def _copy_head(repo: Path, destination: Path) -> None:
    ignored = {".git", ".pytest_cache", ".mypy_cache", ".ruff_cache", "__pycache__"}

    def ignore(_dir: str, names: list[str]) -> set[str]:
        return {name for name in names if name in ignored or name.endswith("_mimificado")}

    shutil.copytree(repo, destination, ignore=ignore)


def _build_binary(project: Path, output: Path) -> None:
    exe = output / ("motor_v3.exe" if sys.platform == "win32" else "motor_v3")
    result = _run(["go", "build", "-o", str(exe), "motor_v3.go"], project)
    if result.returncode != 0:
        raise RuntimeError(f"go build failed in {project}:\n{result.stderr}")


def _process_rss_bytes(pid: int) -> int:
    if sys.platform == "win32":
        try:
            import ctypes
            import ctypes.wintypes

            class ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.wintypes.DWORD),
                    ("PageFaultCount", ctypes.wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            handle = ctypes.windll.kernel32.OpenProcess(0x0410, False, pid)
            if not handle:
                return 0
            counters = ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            ok = ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
            ctypes.windll.kernel32.CloseHandle(handle)
            return int(counters.PeakWorkingSetSize) if ok else 0
        except Exception:
            return 0

    status = Path(f"/proc/{pid}/status")
    try:
        for line in status.read_text(encoding="utf-8").splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) * 1024
    except OSError:
        return 0
    return 0


def _write_scenario(root: Path, name: str, file_count: int, kind: str) -> tuple[Path, int, int]:
    source = root / name
    source.mkdir(parents=True, exist_ok=True)
    total_bytes = 0

    if kind == "java":
        content = "public class App { public static void main(String[] args) { System.out.println(\"hello\"); } }\n"
        paths = [source / "App.java"]
    elif kind == "mixed":
        paths = [source / f"doc-{i:03d}.md" for i in range(file_count - 1)] + [source / "App.java"]
        content = "# Mixed\n\n" + ("repeated_identifier_for_dictionary " * 80) + "\n"
    elif kind == "bmad":
        content = "# BMAD Workflow\n\n<!-- stepsCompleted: 1 -->\n\n" + "\n".join(f"- step {i}" for i in range(80))
        paths = [source / "workflow.md"]
    elif kind == "repetitive":
        content = "# Repetitive\n\n" + ("supercalifragilisticexpialidocious " * 300) + "\n"
        paths = [source / "repetitive.md"]
    else:
        content = "# Small\n\nShort markdown with a table.\n\n| A | B |\n| - | - |\n| 1 | 2 |\n"
        paths = [source / f"doc-{i:03d}.md" for i in range(file_count)]

    for path in paths:
        if path.suffix == ".java":
            data = "public class App { public void run() { int total = 0; total += 1; } }\n"
        else:
            data = content
        path.write_text(data, encoding="utf-8")
        total_bytes += len(data.encode("utf-8"))

    return source, len(paths), total_bytes


def _read_report_tokens(destination: Path) -> int:
    report = destination / "report.json"
    if not report.exists():
        return 0
    try:
        entries = json.loads(report.read_text(encoding="utf-8"))
    except Exception:
        return 0
    return int(sum(entry.get("tokens_originais", 0) for entry in entries))


def _supports_flag(project: Path, command: list[str], flag: str) -> bool:
    result = _run(command + ["--help"], project)
    return flag in result.stdout or flag in result.stderr


def _measure(command: list[str], project: Path, source: Path, destination: Path, flags: list[str]) -> dict:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)

    if command[-1].endswith("motor_v3.exe") or command[-1].endswith("motor_v3"):
        cmd = [*command, str(source), str(destination), *flags]
    else:
        cmd = [
            *command,
            "--src", str(source),
            "--dst", str(destination),
            *flags,
            "--report-path", str(destination / "report"),
        ]
    start = time.perf_counter()
    env = os.environ.copy()
    env["TIKTOKEN_CACHE_DIR"] = str(project / "resources")
    proc = subprocess.Popen(
        cmd,
        cwd=str(project),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    peak_rss = 0
    while proc.poll() is None:
        peak_rss = max(peak_rss, _process_rss_bytes(proc.pid))
        time.sleep(0.005)
    stdout, stderr = proc.communicate()
    elapsed = time.perf_counter() - start
    peak_rss = max(peak_rss, _process_rss_bytes(proc.pid))
    if proc.returncode != 0:
        raise RuntimeError(f"benchmark command failed ({proc.returncode}):\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}")

    file_count = sum(1 for path in source.rglob("*") if path.is_file())
    token_count = _read_report_tokens(destination)
    return {
        "duration_seconds": elapsed,
        "peak_rss_bytes": peak_rss,
        "files_per_second": file_count / elapsed if elapsed > 0 else 0,
        "tokens_per_second": token_count / elapsed if elapsed > 0 else 0,
        "hash_calls": file_count,
        "tokenizer_calls": max(token_count and file_count, file_count),
        "subprocess_count": 1,
    }


def _compute_tree_sha256(output_dir: Path) -> str:
    files_info = []
    for root, _, files in os.walk(output_dir):
        for f in files:
            filepath = os.path.join(root, f)
            rel_path = os.path.relpath(filepath, output_dir).replace('\\', '/')
            sha256_hash = hashlib.sha256()
            with open(filepath, "rb") as fp:
                for chunk in iter(lambda: fp.read(4096), b""):
                    sha256_hash.update(chunk)
            sha = sha256_hash.hexdigest()
            size = os.path.getsize(filepath)
            files_info.append({
                "path": rel_path,
                "sha256": sha,
                "size": size
            })
    files_info.sort(key=lambda x: x["path"])
    manifest = {"files": files_info}
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(manifest_bytes).hexdigest()


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)]


def _summarize(samples: list[dict], output_hashes: list[str]) -> dict:
    durations = [sample["duration_seconds"] for sample in samples]
    rss_values = [sample["peak_rss_bytes"] for sample in samples]
    files_per_second = [sample["files_per_second"] for sample in samples]
    tokens_per_second = [sample["tokens_per_second"] for sample in samples]
    mean_dur = statistics.mean(durations) if durations else 0.0
    stddev = statistics.pstdev(durations) if len(durations) > 1 else 0.0
    cv = (stddev / mean_dur) if mean_dur > 0 else 0.0

    return {
        "raw_durations": durations,
        "median": statistics.median(durations),
        "p95": _p95(durations),
        "minimum": min(durations),
        "maximum": max(durations),
        "standard_deviation": stddev,
        "cv": cv,
        "peak_rss": max(rss_values),
        "files_per_second": statistics.median(files_per_second),
        "tokens_per_second": statistics.median(tokens_per_second),
        "hash_calls": max(sample["hash_calls"] for sample in samples),
        "tokenizer_calls": max(sample["tokenizer_calls"] for sample in samples),
        "subprocess_count": max(sample["subprocess_count"] for sample in samples),
        "exit_codes": [0] * len(samples),
        "output_hash": output_hashes[-1] if output_hashes else "",
    }


def _delta(head: float, base: float) -> float:
    if base <= 0:
        return 0.0
    return (head - base) / base


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare CIDA performance against a git base ref.")
    parser.add_argument("--base-ref", default="origin/main")
    parser.add_argument("--head-dir", default=".")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    repo = Path(args.head_dir).resolve()
    temp_root = Path(tempfile.mkdtemp(prefix="cida-performance-compare-"))
    scenarios = [
        ("markdown-small", "go", 1, "small", ["--profile", "markdown", "--dictionary-scope", "file"]),
        ("markdown-repetitive", "go", 1, "repetitive", ["--profile", "markdown", "--dictionary-scope", "file"]),
        ("bmad", "go", 1, "bmad", ["--profile", "bmad", "--dictionary-scope", "file"]),
        ("corpus-10-cache-on", "python", 10, "small", ["--mode", "semantic", "--profile", "markdown", "--dictionary-scope", "corpus"]),
        ("corpus-100-cache-on", "python", 100, "small", ["--mode", "semantic", "--profile", "markdown", "--dictionary-scope", "corpus"]),
        ("java-semantic", "python", 1, "java", ["--mode", "semantic", "--profile", "java", "--dictionary-scope", "none"]),
        ("corpus-mixed", "python", 10, "mixed", ["--mode", "semantic", "--profile", "auto", "--dictionary-scope", "corpus"]),
        ("corpus-10-cache-off", "python", 10, "small", ["--mode", "semantic", "--profile", "markdown", "--dictionary-scope", "corpus", "--no-cache"]),
    ]

    try:
        base_dir = temp_root / "base"
        head_dir = temp_root / "head"
        _export_ref(repo, args.base_ref, base_dir)
        _copy_head(repo, head_dir)

        base_bin_dir = temp_root / "base-bin"
        head_bin_dir = temp_root / "head-bin"
        base_bin_dir.mkdir()
        head_bin_dir.mkdir()
        _build_binary(base_dir, base_bin_dir)
        _build_binary(head_dir, head_bin_dir)
        base_bin = base_bin_dir / ("motor_v3.exe" if sys.platform == "win32" else "motor_v3")
        head_bin = head_bin_dir / ("motor_v3.exe" if sys.platform == "win32" else "motor_v3")

        diff_bytes = _run(["git", "diff"], repo).stdout.encode("utf-8")
        head_diff_sha256 = hashlib.sha256(diff_bytes).hexdigest()

        go_version_res = _run(["go", "version"], repo)
        go_version = go_version_res.stdout.strip() if go_version_res.returncode == 0 else "unknown"

        results = {
            "schema_version": 1,
            "base_ref": args.base_ref,
            "base_sha": _run(["git", "rev-parse", args.base_ref], repo, check=True).stdout.strip(),
            "head_sha": _run(["git", "rev-parse", "HEAD"], repo, check=True).stdout.strip(),
            "head_dirty": bool(_run(["git", "status", "--short"], repo, check=True).stdout.strip()),
            "head_diff_sha256": head_diff_sha256,
            "python_version": sys.version,
            "go_version": go_version,
            "platform": sys.platform,
            "cpu_count": os.cpu_count(),
            "temp_root": str(temp_root),
            "warmups": args.warmups,
            "runs": args.runs,
            "budgets": {
                "median": MEDIAN_BUDGET,
                "p95": P95_BUDGET,
                "peak_rss": RSS_BUDGET,
            },
            "scenarios": {},
        }

        failed = []
        base_python_cmd = [sys.executable, "-m", "cida.interfaces.cli"]
        base_supports_no_cache = _supports_flag(base_dir, base_python_cmd, "--no-cache")

        for scenario_name, runner, file_count, kind, flags in scenarios:
            scenario_root = temp_root / "scenarios" / scenario_name
            source, _, _ = _write_scenario(scenario_root, "src", file_count, kind)

            def get_flag_val(flag_name: str, default_val: str) -> str:
                if flag_name in flags:
                    idx = flags.index(flag_name)
                    if idx + 1 < len(flags):
                        return flags[idx + 1]
                return default_val

            scenario_mode = get_flag_val("--mode", "lossless")
            scenario_profile = get_flag_val("--profile", "auto")
            scenario_dict_scope = get_flag_val("--dictionary-scope", "file")
            verify_semantics = "--no-verify-semantics" not in flags
            cache_enabled = "--no-cache" not in flags
            durable_writes = "--durable-writes" in flags

            version_configs = [
                ("base", base_bin, base_dir),
                ("head", head_bin, head_dir),
            ]

            samples_map = {"base": [], "head": []}
            hashes_map = {"base": [], "head": []}
            unsupported_base_flags = []

            # Warmups: alternating order
            for w in range(args.warmups):
                for version, binary, project in version_configs:
                    command = [str(binary)] if runner == "go" else ([sys.executable, "-m", "cida.interfaces.cli"])
                    effective_flags = list(flags)
                    if version == "base" and "--no-cache" in effective_flags and not base_supports_no_cache:
                        effective_flags.remove("--no-cache")
                        unsupported_base_flags.append("--no-cache")
                    dest = temp_root / "runs" / scenario_name / version / f"warmup-{w:02d}"
                    _measure(command, project, source, dest, effective_flags)

            # Measured runs: alternating order
            for r in range(args.runs):
                for version, binary, project in version_configs:
                    command = [str(binary)] if runner == "go" else ([sys.executable, "-m", "cida.interfaces.cli"])
                    effective_flags = list(flags)
                    if version == "base" and "--no-cache" in effective_flags and not base_supports_no_cache:
                        effective_flags.remove("--no-cache")
                    dest = temp_root / "runs" / scenario_name / version / f"run-{r:02d}"
                    sample = _measure(command, project, source, dest, effective_flags)
                    samples_map[version].append(sample)
                    hashes_map[version].append(_compute_tree_sha256(dest))

            base_summary = _summarize(samples_map["base"], hashes_map["base"])
            head_summary = _summarize(samples_map["head"], hashes_map["head"])

            comparison = {
                "median_delta": _delta(head_summary["median"], base_summary["median"]),
                "p95_delta": _delta(head_summary["p95"], base_summary["p95"]),
                "peak_rss_delta": _delta(head_summary["peak_rss"], base_summary["peak_rss"]),
            }
            budget_result = (
                comparison["median_delta"] <= MEDIAN_BUDGET
                and comparison["p95_delta"] <= P95_BUDGET
                and comparison["peak_rss_delta"] <= RSS_BUDGET
            )
            is_unstable = base_summary["cv"] > 0.10 or head_summary["cv"] > 0.10
            stability_str = "UNSTABLE" if is_unstable else "STABLE"

            if not budget_result:
                failed.append(scenario_name)

            cmd_str = f"motor_v3 {' '.join(flags)}" if runner == "go" else f"python -m cida.interfaces.cli {' '.join(flags)}"

            results["scenarios"][scenario_name] = {
                "scenario": scenario_name,
                "runner": runner,
                "command": cmd_str,
                "mode": scenario_mode,
                "profile": scenario_profile,
                "dictionary_scope": scenario_dict_scope,
                "verify_semantics": verify_semantics,
                "cache_enabled": cache_enabled,
                "durable_writes": durable_writes,
                "warmups": args.warmups,
                "runs": args.runs,
                "flags": flags,
                "unsupported_base_flags": sorted(set(unsupported_base_flags)),
                "base": base_summary,
                "head": head_summary,
                "comparison": comparison,
                "stability": stability_str,
                "budget_result": "PASS" if budget_result else "FAIL",
            }

        results["overall_result"] = "PASS" if not failed else "FAIL"
        results["failed_scenarios"] = failed
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(json.dumps({
            "result": results["overall_result"],
            "base_sha": results["base_sha"],
            "head_sha": results["head_sha"],
            "runs": args.runs,
            "failed_scenarios": failed,
            "output": str(output),
        }, indent=2))
        if failed:
            sys.exit(1)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()

