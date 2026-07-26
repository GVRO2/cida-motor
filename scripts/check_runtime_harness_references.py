from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
EXCLUDED_PARTS = {".git", ".github", "benchmarks", "devtools", "docs", "scripts", "tests"}
RUNTIME_ROOTS = ("cida",)
ROOT_RUNTIME_FILES = ("token_optimizer.py", "translate.py", "motor_v3.go")
FORBIDDEN_TERMS = (
    "RuntimeHarnessProbe",
    "tests.runtime_harness_probe",
    "devtools.runtime_harness_probe",
    "CidaHarness",
    "INDEPENDENT_HARNESS",
    "POST_MERGE_REMEDIATION",
    "REMEDIATION_BLOCKED_BY_HARNESS",
)


def iter_runtime_files() -> list[Path]:
    files: list[Path] = []
    for root_name in RUNTIME_ROOTS:
        runtime_root = ROOT / root_name
        if runtime_root.exists():
            files.extend(
                path
                for path in runtime_root.rglob("*.py")
                if not any(part in EXCLUDED_PARTS for part in path.relative_to(ROOT).parts)
            )
    for name in ROOT_RUNTIME_FILES:
        path = ROOT / name
        if path.exists():
            files.append(path)
    return sorted(set(files))


def main() -> int:
    violations: list[str] = []
    for path in iter_runtime_files():
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), start=1):
            for term in FORBIDDEN_TERMS:
                if term in line:
                    violations.append(f"{rel}:{line_no}: forbidden harness reference {term}")
    if violations:
        print("\n".join(violations), file=sys.stderr)
        return 1
    print(f"Runtime harness recursive audit passed: {len(iter_runtime_files())} files scanned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
