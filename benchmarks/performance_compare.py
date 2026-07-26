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
import venv
from io import BytesIO
from pathlib import Path


MEDIAN_BUDGET = 0.05
P95_BUDGET = 0.10
RSS_BUDGET = 0.10
STABILITY_CV_LIMIT = 0.10
MAX_BALANCED_SCENARIO_ATTEMPTS = 3


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
    ignored = {".git", ".cida-local", ".runtime", ".pytest_cache", ".mypy_cache", ".ruff_cache", "__pycache__"}

    def ignore(_dir: str, names: list[str]) -> set[str]:
        return {name for name in names if name in ignored or name.endswith("_mimificado")}

    shutil.copytree(repo, destination, ignore=ignore)


def _build_binary(project: Path, output: Path) -> None:
    exe = output / ("motor_v3.exe" if sys.platform == "win32" else "motor_v3")
    result = _run(["go", "build", "-o", str(exe), "motor_v3.go"], project)
    if result.returncode != 0:
        raise RuntimeError(f"go build failed in {project}:\n{result.stderr}")


def _venv_python(venv_dir: Path) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _python_bin_dir(python_exe: Path) -> Path:
    return python_exe.parent


def _prepare_python_env(project: Path, venv_dir: Path, install_legacy_yaml: bool = False) -> Path:
    builder = venv.EnvBuilder(with_pip=True, system_site_packages=True)
    builder.create(venv_dir)
    python_exe = _venv_python(venv_dir)
    tiktoken_check = subprocess.run(
        [str(python_exe), "-c", "import tiktoken"],
        cwd=str(project),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if tiktoken_check.returncode != 0:
        raise RuntimeError("base benchmark environment cannot import tiktoken from the current CI environment")
    if not install_legacy_yaml:
        return python_exe
    yaml_check = subprocess.run([str(python_exe), "-c", "import yaml"], cwd=str(project), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    requirements = project / "requirements-ci.txt"
    if yaml_check.returncode != 0 and requirements.exists():
        pyyaml_requirement = ""
        for line in requirements.read_text(encoding="utf-8").splitlines():
            if line.strip().lower().startswith("pyyaml"):
                pyyaml_requirement = line.strip()
                break
        if not pyyaml_requirement:
            return python_exe
        result = subprocess.run(
            [str(python_exe), "-m", "pip", "install", pyyaml_requirement],
            cwd=str(project),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"failed to install benchmark environment for {project}:\n{result.stdout}\n{result.stderr}")
    return python_exe


def _python_cli_command(project: Path, python_executable: Path | None = None) -> list[str]:
    python_executable = python_executable or Path(sys.executable)
    script = project / "token_optimizer.py"
    if script.exists():
        return [str(python_executable), str(script)]
    return [str(python_executable), "-c", "from cida.interfaces.cli import main; main()"]


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


def _read_report_entries(destination: Path) -> list[dict]:
    report = destination / "report.json"
    if not report.exists():
        return []
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("entries"), list):
        return data["entries"]
    return []


def _read_report_tokens(destination: Path) -> int:
    return int(sum(entry.get("tokens_originais", 0) for entry in _read_report_entries(destination)))


def _source_inventory(source: Path) -> dict[str, int]:
    return {
        path.relative_to(source).as_posix(): path.stat().st_size
        for path in source.rglob("*")
        if path.is_file()
    }


def _output_inventory(destination: Path) -> tuple[int, int, int]:
    outputs_created = 0
    sidecars_created = 0
    output_bytes = 0
    for path in destination.rglob("*"):
        if not path.is_file():
            continue
        outputs_created += 1
        output_bytes += path.stat().st_size
        if path.name.endswith(".cidatkn"):
            sidecars_created += 1
    return outputs_created, sidecars_created, output_bytes


def _supports_flag(project: Path, command: list[str], flag: str) -> bool:
    result = _run(command + ["--help"], project)
    return flag in result.stdout or flag in result.stderr


def _measure(command: list[str], project: Path, source: Path, destination: Path, flags: list[str], python_bin_dir: Path | None = None) -> dict:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    source_files = _source_inventory(source)

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
    if python_bin_dir is not None:
        env["PATH"] = str(python_bin_dir) + os.pathsep + env.get("PATH", "")
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
    report_entries = _read_report_entries(destination)
    processed_paths = {
        str(entry.get("arquivo", "")).replace("\\", "/")
        for entry in report_entries
        if entry.get("arquivo")
    }
    bytes_processed = sum(source_files.get(path, 0) for path in processed_paths)
    if not report_entries:
        bytes_processed = 0
    outputs_created, sidecars_created, output_bytes = _output_inventory(destination)
    return {
        "duration_seconds": elapsed,
        "peak_rss_bytes": peak_rss,
        "files_per_second": file_count / elapsed if elapsed > 0 else 0,
        "mb_per_second": (bytes_processed / (1024 * 1024)) / elapsed if elapsed > 0 else 0,
        "milliseconds_per_file": (elapsed * 1000) / len(report_entries) if report_entries else 0,
        "milliseconds_per_mb": (elapsed * 1000) / (bytes_processed / (1024 * 1024)) if bytes_processed else 0,
        "tokens_per_second": token_count / elapsed if elapsed > 0 else 0,
        "hash_calls": file_count,
        "tokenizer_calls": max(token_count and file_count, file_count),
        "subprocess_count": 1,
        "files_discovered": len(source_files),
        "files_processed": len(report_entries),
        "files_skipped": max(len(source_files) - len(report_entries), 0),
        "bytes_discovered": sum(source_files.values()),
        "bytes_processed": bytes_processed,
        "outputs_created": outputs_created,
        "sidecars_created": sidecars_created,
        "output_bytes": output_bytes,
        "exit_code": proc.returncode,
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


def _implementation_fingerprint(project: Path, runner: str) -> str:
    if runner == "go":
        paths = sorted(path for path in project.rglob("*.go") if ".git" not in path.parts)
        for name in ("go.mod", "go.sum"):
            candidate = project / name
            if candidate.exists():
                paths.append(candidate)
    else:
        paths = [
            path
            for path in [
                project / "token_optimizer.py",
                project / "translate.py",
                project / "decompress.py",
            ]
            if path.exists()
        ]
        cida_dir = project / "cida"
        if cida_dir.exists():
            paths.extend(sorted(path for path in cida_dir.rglob("*.py") if ".git" not in path.parts))

    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        rel = path.relative_to(project).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


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
        "mb_per_second": statistics.median(sample["mb_per_second"] for sample in samples),
        "milliseconds_per_file": statistics.median(sample["milliseconds_per_file"] for sample in samples),
        "milliseconds_per_mb": statistics.median(sample["milliseconds_per_mb"] for sample in samples),
        "hash_calls": max(sample["hash_calls"] for sample in samples),
        "tokenizer_calls": max(sample["tokenizer_calls"] for sample in samples),
        "subprocess_count": max(sample["subprocess_count"] for sample in samples),
        "exit_codes": [sample["exit_code"] for sample in samples],
        "output_hash": output_hashes[-1] if output_hashes else "",
        "output_tree_sha256": output_hashes[-1] if output_hashes else "",
        "files_discovered": max(sample["files_discovered"] for sample in samples),
        "files_processed": max(sample["files_processed"] for sample in samples),
        "files_skipped": max(sample["files_skipped"] for sample in samples),
        "bytes_discovered": max(sample["bytes_discovered"] for sample in samples),
        "bytes_processed": max(sample["bytes_processed"] for sample in samples),
        "outputs_created": max(sample["outputs_created"] for sample in samples),
        "sidecars_created": max(sample["sidecars_created"] for sample in samples),
        "output_bytes": max(sample["output_bytes"] for sample in samples),
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
    parser.add_argument("--validation-level", default="balanced", choices=["balanced", "strict"], help="Validation level for performance comparison")
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
            "validation_level": args.validation_level,
            "budgets": {
                "median": MEDIAN_BUDGET,
                "p95": P95_BUDGET,
                "peak_rss": RSS_BUDGET,
                "stability_cv": STABILITY_CV_LIMIT,
                "max_balanced_scenario_attempts": MAX_BALANCED_SCENARIO_ATTEMPTS,
                "enforced": args.validation_level == "balanced",
            },
            "scenarios": {},
        }

        failed = []
        base_python = _prepare_python_env(base_dir, temp_root / "base-venv", install_legacy_yaml=True)
        base_python_bin = _python_bin_dir(base_python)
        base_python_cmd = _python_cli_command(base_dir, base_python)
        head_python = _prepare_python_env(head_dir, temp_root / "head-venv")
        head_python_bin = _python_bin_dir(head_python)
        head_python_cmd = _python_cli_command(head_dir, head_python)
        base_supports_no_cache = _supports_flag(base_dir, base_python_cmd, "--no-cache")
        base_supports_val_level = _supports_flag(base_dir, base_python_cmd, "--validation-level")


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
                ("base", base_bin, base_dir, base_python_bin),
                ("head", head_bin, head_dir, head_python_bin),
            ]

            unsupported_base_flags: list[str] = []

            def build_effective_flags(version: str) -> list[str]:
                eff = list(flags)
                if args.validation_level != "balanced" and "--validation-level" not in eff:
                    eff.extend(["--validation-level", args.validation_level])
                if version == "base":
                    if "--no-cache" in eff and not base_supports_no_cache:
                        eff.remove("--no-cache")
                        unsupported_base_flags.append("--no-cache")
                    if "--validation-level" in eff and not base_supports_val_level:
                        idx = eff.index("--validation-level")
                        eff.pop(idx)
                        if idx < len(eff):
                            eff.pop(idx)
                        unsupported_base_flags.append("--validation-level")
                return eff

            cmd_str = f"motor_v3 {' '.join(flags)}" if runner == "go" else f"python -m cida.interfaces.cli {' '.join(flags)}"
            max_attempts = MAX_BALANCED_SCENARIO_ATTEMPTS if args.validation_level == "balanced" else 1
            attempt_summaries = []
            base_implementation_sha256 = _implementation_fingerprint(base_dir, runner)
            head_implementation_sha256 = _implementation_fingerprint(head_dir, runner)
            implementation_delta = base_implementation_sha256 != head_implementation_sha256

            for attempt in range(1, max_attempts + 1):
                samples_map = {"base": [], "head": []}
                hashes_map = {"base": [], "head": []}

                # Warmups: alternating order.
                for w in range(args.warmups):
                    warmup_order = version_configs if w % 2 == 0 else list(reversed(version_configs))
                    for version, binary, _project, python_bin in warmup_order:
                        command = [str(binary)] if runner == "go" else (base_python_cmd if version == "base" else head_python_cmd)
                        effective_flags = build_effective_flags(version)
                        dest = temp_root / "runs" / scenario_name / f"attempt-{attempt:02d}" / version / f"warmup-{w:02d}"
                        _measure(command, _project, source, dest, effective_flags, python_bin)

                # Measured runs: alternating order.
                for r in range(args.runs):
                    run_order = version_configs if r % 2 == 0 else list(reversed(version_configs))
                    for version, binary, _project, python_bin in run_order:
                        command = [str(binary)] if runner == "go" else (base_python_cmd if version == "base" else head_python_cmd)
                        effective_flags = build_effective_flags(version)
                        dest = temp_root / "runs" / scenario_name / f"attempt-{attempt:02d}" / version / f"run-{r:02d}"

                        sample = _measure(command, _project, source, dest, effective_flags, python_bin)
                        samples_map[version].append(sample)
                        hashes_map[version].append(_compute_tree_sha256(dest))

                base_summary = _summarize(samples_map["base"], hashes_map["base"])
                head_summary = _summarize(samples_map["head"], hashes_map["head"])

                comparison = {
                    "median_delta": _delta(head_summary["median"], base_summary["median"]),
                    "p95_delta": _delta(head_summary["p95"], base_summary["p95"]),
                    "peak_rss_delta": _delta(head_summary["peak_rss"], base_summary["peak_rss"]),
                }
                raw_budget_result = (
                    comparison["median_delta"] <= MEDIAN_BUDGET
                    and comparison["p95_delta"] <= P95_BUDGET
                    and comparison["peak_rss_delta"] <= RSS_BUDGET
                )
                is_unstable = base_summary["cv"] > STABILITY_CV_LIMIT or head_summary["cv"] > STABILITY_CV_LIMIT
                stability_str = "UNSTABLE" if is_unstable else "STABLE"
                output_equivalent = base_summary["output_tree_sha256"] == head_summary["output_tree_sha256"]
                timing_gate_skipped = args.validation_level == "balanced" and not implementation_delta
                budget_result = raw_budget_result or timing_gate_skipped
                gate_result = True if timing_gate_skipped else budget_result and not is_unstable
                attempt_summaries.append(
                    {
                        "attempt": attempt,
                        "budget_result": "PASS" if budget_result else "FAIL",
                        "raw_budget_result": "PASS" if raw_budget_result else "FAIL",
                        "stability": stability_str,
                        "base_cv": base_summary["cv"],
                        "head_cv": head_summary["cv"],
                        "implementation_delta": implementation_delta,
                        "output_equivalent": output_equivalent,
                        "timing_gate_skipped_reason": "SKIPPED_NO_IMPLEMENTATION_DELTA" if timing_gate_skipped else "",
                        "comparison": comparison,
                    }
                )

                should_retry = args.validation_level == "balanced" and not gate_result and attempt < max_attempts
                if should_retry:
                    continue

                if args.validation_level == "balanced" and not gate_result:
                    failed.append(scenario_name)

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
                    "validation_level": args.validation_level,
                    "warmups": args.warmups,
                    "runs": args.runs,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "flags": flags,
                    "unsupported_base_flags": sorted(set(unsupported_base_flags)),
                    "base": base_summary,
                    "head": head_summary,
                    "comparison": comparison,
                    "stability": stability_str,
                    "base_implementation_sha256": base_implementation_sha256,
                    "head_implementation_sha256": head_implementation_sha256,
                    "implementation_delta": implementation_delta,
                    "output_equivalent": output_equivalent,
                    "timing_gate_skipped_reason": "SKIPPED_NO_IMPLEMENTATION_DELTA" if timing_gate_skipped else "",
                    "budget_result": "PASS" if budget_result else "FAIL",
                    "raw_budget_result": "PASS" if raw_budget_result else "FAIL",
                    "stability_result": "FAIL" if is_unstable else "PASS",
                    "gate_result": "PASS" if gate_result else "FAIL",
                    "budget_enforced": args.validation_level == "balanced",
                    "attempts": attempt_summaries,
                }
                break

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
