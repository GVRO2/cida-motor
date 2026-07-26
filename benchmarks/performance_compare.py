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

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.benchmark_policy_auditor import load_benchmark_policy, summarize_samples  # noqa: E402
from harness.process_tree_probe import ProcessTreeSampler  # noqa: E402
from harness.runtime_harness_probe import OriginalProjectHarness  # noqa: E402

POLICY = load_benchmark_policy()
MEDIAN_BUDGET = POLICY.median_budget
P95_BUDGET = POLICY.p95_budget
RSS_BUDGET = POLICY.rss_budget
STABILITY_CV_LIMIT = POLICY.stability_cv_limit
MAX_STABILITY_COLLECTION_ROUNDS = 8


def _go_executable() -> str:
    return os.environ.get("CIDA_GO_BIN") or "go"


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
    result = _run([_go_executable(), "build", "-o", str(exe), "motor_v3.go"], project)
    if result.returncode != 0:
        raise RuntimeError(f"go build failed in {project}:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")


def _venv_python(venv_dir: Path) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _python_bin_dir(python_exe: Path) -> Path:
    return python_exe.parent


def _prepare_python_env(project: Path, venv_dir: Path, install_legacy_yaml: bool = False) -> Path:
    builder = venv.EnvBuilder(with_pip=True, system_site_packages=False)
    builder.create(venv_dir)
    python_exe = _venv_python(venv_dir)
    requirements = project / "requirements-runtime.txt"
    if requirements.exists():
        result = _pip_install(python_exe, project, ["-r", str(requirements)])
        if result.returncode != 0:
            raise RuntimeError(f"failed to install benchmark runtime dependencies for {project}:\n{result.stdout}\n{result.stderr}")
    tiktoken_check = subprocess.run(
        [str(python_exe), "-c", "import tiktoken"],
        cwd=str(project),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if tiktoken_check.returncode != 0:
        raise RuntimeError(f"benchmark environment cannot import tiktoken for {project}:\n{tiktoken_check.stderr.decode('utf-8', errors='replace')}")
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
        result = _pip_install(python_exe, project, [pyyaml_requirement])
        if result.returncode != 0:
            raise RuntimeError(f"failed to install benchmark environment for {project}:\n{result.stdout}\n{result.stderr}")
    return python_exe


def _pip_install(python_exe: Path, project: Path, install_args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(python_exe), "-m", "pip", "install", "--use-feature=truststore", *install_args],
        cwd=str(project),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _requirements_sha256(project: Path) -> str:
    digest = hashlib.sha256()
    for name in ("requirements-runtime.txt", "requirements-ci.txt", "requirements-dev.txt"):
        path = project / name
        if not path.exists():
            continue
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _python_env_evidence(project: Path, python_exe: Path) -> dict:
    def run_python(code: str) -> str:
        result = subprocess.run(
            [str(python_exe), "-c", code],
            cwd=str(project),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else f"unavailable: {result.stderr.strip()}"

    pip_version = subprocess.run(
        [str(python_exe), "-m", "pip", "--version"],
        cwd=str(project),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    freeze = subprocess.run(
        [str(python_exe), "-m", "pip", "freeze"],
        cwd=str(project),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return {
        "python_executable": str(python_exe),
        "python_version": run_python("import sys; print(sys.version.replace('\\n', ' '))"),
        "pip_version": pip_version.stdout.strip() if pip_version.returncode == 0 else pip_version.stderr.strip(),
        "pip_freeze": freeze.stdout.splitlines() if freeze.returncode == 0 else [],
        "requirements_sha256": _requirements_sha256(project),
        "tiktoken_version": run_python("import tiktoken; print(getattr(tiktoken, '__version__', 'unknown'))"),
        "pyyaml_version": run_python("import yaml; print(getattr(yaml, '__version__', 'missing'))"),
        "system_site_packages": False,
    }


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
        content = "# Mixed\n\n" + ("repeated_identifier_for_dictionary " * 800) + "\n"
    elif kind == "bmad":
        content = "# BMAD Workflow\n\n<!-- stepsCompleted: 1 -->\n\n" + "\n".join(f"- step {i}" for i in range(8000))
        paths = [source / "workflow.md"]
    elif kind == "repetitive":
        content = "# Repetitive\n\n" + ("supercalifragilisticexpialidocious " * 1500) + "\n"
        paths = [source / "repetitive.md"]
    else:
        row_count = 5000 if file_count == 1 else 1200 if file_count <= 10 else 120
        rows = "\n".join(f"| {i} | {i + 1} |" for i in range(row_count))
        content = "# Small\n\nShort table.\n\n| A | B |\n| - | - |\n" + rows + "\n"
        paths = [source / f"doc-{i:03d}.md" for i in range(file_count)]

    for path in paths:
        if path.suffix == ".java":
            methods = "\n".join(
                f"    public int value{i}() {{ int total = {i}; total += 1; return total; }}"
                for i in range(160)
            )
            data = "public class App {\n" + methods + "\n}\n"
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
    sampler = ProcessTreeSampler(proc.pid)
    while proc.poll() is None:
        sampler.sample()
        time.sleep(0.005)
    stdout, stderr = proc.communicate()
    elapsed = time.perf_counter() - start
    sampler.sample()
    process_metrics = sampler.metrics()
    peak_rss = process_metrics.process_tree_peak_rss
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
        "parent_peak_rss": process_metrics.parent_peak_rss,
        "children_peak_rss": process_metrics.children_peak_rss,
        "process_tree_peak_rss": process_metrics.process_tree_peak_rss,
        "peak_process_count": process_metrics.peak_process_count,
        "child_pids_seen": list(process_metrics.child_pids_seen),
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
    duration_summary = summarize_samples(durations)
    stddev = statistics.pstdev(durations) if len(durations) > 1 else 0.0

    return {
        "raw_samples": samples,
        "raw_durations": durations,
        "sample_count": len(samples),
        "median": duration_summary["median"],
        "p95": duration_summary["p95"],
        "minimum": min(durations),
        "maximum": max(durations),
        "standard_deviation": stddev,
        "cv": duration_summary["cv"],
        "raw_median": duration_summary["raw_median"],
        "raw_p95": duration_summary["raw_p95"],
        "raw_cv": duration_summary["raw_cv"],
        "duration_cap": duration_summary["duration_cap"],
        "duration_outliers_capped": int(duration_summary["duration_outliers_capped"]),
        "peak_rss": max(rss_values),
        "parent_peak_rss": max(sample["parent_peak_rss"] for sample in samples),
        "children_peak_rss": max(sample["children_peak_rss"] for sample in samples),
        "process_tree_peak_rss": max(sample["process_tree_peak_rss"] for sample in samples),
        "peak_process_count": max(sample["peak_process_count"] for sample in samples),
        "child_pids_seen": sorted({pid for sample in samples for pid in sample["child_pids_seen"]}),
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
    parser.add_argument("--runs", type=int, default=POLICY.balanced_runs)
    parser.add_argument("--warmups", type=int, default=POLICY.balanced_warmups)
    parser.add_argument("--validation-level", default="balanced", choices=["balanced", "strict"], help="Validation level for performance comparison")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()


    repo = Path(args.head_dir).resolve()
    temp_root = Path(tempfile.mkdtemp(prefix="cida-performance-compare-"))
    scenarios = [
        ("markdown-small", "go", 1, "small", ["--profile", "markdown", "--dictionary-scope", "file"]),
        ("markdown-repetitive", "go", 1, "repetitive", ["--profile", "markdown", "--dictionary-scope", "file"]),
        ("bmad", "go", 1, "bmad", ["--profile", "bmad", "--dictionary-scope", "file"]),
        ("corpus-10-cache-on", "python", 10, "small", ["--mode", "semantic", "--profile", "markdown", "--dictionary-scope", "corpus", "--workers", "1"]),
        ("corpus-100-cache-on", "python", 100, "small", ["--mode", "semantic", "--profile", "markdown", "--dictionary-scope", "corpus", "--workers", "1"]),
        ("java-semantic", "python", 1, "java", ["--mode", "semantic", "--profile", "java", "--dictionary-scope", "none", "--workers", "1"]),
        ("corpus-mixed", "python", 10, "mixed", ["--mode", "semantic", "--profile", "auto", "--dictionary-scope", "corpus", "--workers", "1"]),
        ("corpus-10-cache-off", "python", 10, "small", ["--mode", "semantic", "--profile", "markdown", "--dictionary-scope", "corpus", "--workers", "1", "--no-cache"]),
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

        go_version_res = _run([_go_executable(), "version"], repo)
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
            "policy_sha256": POLICY.sha256,
            "budgets": {
                "median": MEDIAN_BUDGET,
                "p95": P95_BUDGET,
                "peak_rss": RSS_BUDGET,
                "stability_cv": STABILITY_CV_LIMIT,
                "allow_timing_skip": POLICY.allow_timing_skip,
                "allow_parent_only_rss": POLICY.allow_parent_only_rss,
                "allow_system_site_packages": POLICY.allow_system_site_packages,
                "enforced": True,
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
        results["python_environments"] = {
            "base": _python_env_evidence(base_dir, base_python),
            "head": _python_env_evidence(head_dir, head_python),
        }
        inspector = OriginalProjectHarness()
        results["runtime_dependency_graphs"] = {
            "base_go": inspector.inspect(base_dir, "go").__dict__,
            "head_go": inspector.inspect(head_dir, "go").__dict__,
            "base_python": inspector.inspect(base_dir, "python").__dict__,
            "head_python": inspector.inspect(head_dir, "python").__dict__,
        }
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
            round_limit = MAX_STABILITY_COLLECTION_ROUNDS
            attempt_summaries = []
            base_implementation_sha256 = _implementation_fingerprint(base_dir, runner)
            head_implementation_sha256 = _implementation_fingerprint(head_dir, runner)
            implementation_delta = base_implementation_sha256 != head_implementation_sha256

            samples_map = {"base": [], "head": []}
            hashes_map = {"base": [], "head": []}
            round_no = 1

            while True:
                # Warmups: alternating order.
                if round_no == 1:
                    for w in range(args.warmups):
                        warmup_order = version_configs if w % 2 == 0 else list(reversed(version_configs))
                        for version, binary, _project, python_bin in warmup_order:
                            command = [str(binary)] if runner == "go" else (base_python_cmd if version == "base" else head_python_cmd)
                            effective_flags = build_effective_flags(version)
                            dest = temp_root / "runs" / scenario_name / f"round-{round_no:02d}" / version / f"warmup-{w:02d}"
                            _measure(command, _project, source, dest, effective_flags, python_bin)

                # Measured runs: alternating order.
                for r in range(args.runs):
                    run_order = version_configs if r % 2 == 0 else list(reversed(version_configs))
                    for version, binary, _project, python_bin in run_order:
                        command = [str(binary)] if runner == "go" else (base_python_cmd if version == "base" else head_python_cmd)
                        effective_flags = build_effective_flags(version)
                        dest = temp_root / "runs" / scenario_name / f"round-{round_no:02d}" / version / f"run-{r:02d}"

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
                budget_result = raw_budget_result
                gate_result = budget_result and not is_unstable
                attempt_summaries.append(
                    {
                        "attempt": round_no,
                        "budget_result": "PASS" if budget_result else "FAIL",
                        "raw_budget_result": "PASS" if raw_budget_result else "FAIL",
                        "stability": stability_str,
                        "base_cv": base_summary["cv"],
                        "head_cv": head_summary["cv"],
                        "implementation_delta": implementation_delta,
                        "output_equivalent": output_equivalent,
                        "comparison": comparison,
                    }
                )
                print(
                    json.dumps(
                        {
                            "scenario": scenario_name,
                            "round": round_no,
                            "stability": stability_str,
                            "budget_result": "PASS" if budget_result else "FAIL",
                            "base_cv": base_summary["cv"],
                            "head_cv": head_summary["cv"],
                            "comparison": comparison,
                        },
                        sort_keys=True,
                    ),
                    file=sys.stderr,
                    flush=True,
                )

                should_extend_for_instability = is_unstable and round_no < round_limit
                if should_extend_for_instability:
                    round_no += 1
                    continue

                if not gate_result:
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
                    "attempt": round_no,
                    "measurement_round_limit": round_limit,
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
                    "budget_result": "PASS" if budget_result else "FAIL",
                    "raw_budget_result": "PASS" if raw_budget_result else "FAIL",
                    "stability_result": "FAIL" if is_unstable else "PASS",
                    "gate_result": "PASS" if gate_result else "FAIL",
                    "budget_enforced": True,
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
