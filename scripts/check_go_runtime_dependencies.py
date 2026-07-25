import argparse
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GoDependencyViolation:
    line: int
    module: str
    reason: str


def parse_go_mod(source: str) -> tuple[str | None, list[GoDependencyViolation]]:
    module_name: str | None = None
    violations: list[GoDependencyViolation] = []
    in_require_block = False
    in_replace_block = False

    for line_no, raw_line in enumerate(source.splitlines(), start=1):
        line = strip_go_mod_comment(raw_line).strip()
        if not line:
            continue

        if line.startswith("module "):
            module_name = line.split()[1]
            continue

        if line == "require (":
            in_require_block = True
            continue
        if line == "replace (":
            in_replace_block = True
            continue
        if line == ")":
            in_require_block = False
            in_replace_block = False
            continue

        if line.startswith("require "):
            module = line.split()[1]
            violations.append(GoDependencyViolation(line_no, module, "require"))
            continue
        if in_require_block:
            module = line.split()[0]
            violations.append(GoDependencyViolation(line_no, module, "require"))
            continue

        if line.startswith("replace "):
            module = line.split()[1]
            if "=>" in line and not is_local_replace(line.split("=>", 1)[1].strip()):
                violations.append(GoDependencyViolation(line_no, module, "replace_external"))
            continue
        if in_replace_block:
            parts = line.split("=>", 1)
            if len(parts) == 2 and not is_local_replace(parts[1].strip()):
                violations.append(GoDependencyViolation(line_no, parts[0].split()[0], "replace_external"))
            continue

        if line.startswith("tool "):
            module = line.split()[1]
            violations.append(GoDependencyViolation(line_no, module, "tool"))

    return module_name, violations


def strip_go_mod_comment(line: str) -> str:
    return re.sub(r"\s+//.*$", "", line)


def is_local_replace(target: str) -> bool:
    first = target.split()[0]
    return first.startswith(("./", "../", "/", ".\\", "..\\")) or re.match(r"^[A-Za-z]:[\\/]", first) is not None


def go_sum_violations(source: str) -> list[GoDependencyViolation]:
    violations: list[GoDependencyViolation] = []
    for line_no, raw_line in enumerate(source.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        module = line.split()[0]
        violations.append(GoDependencyViolation(line_no, module, "go_sum"))
    return violations


def go_list_module_violations(root: Path, main_module: str) -> list[str]:
    local_state = root / ".cida-local"
    go_cache = local_state / "go-build-cache"
    go_tmp = local_state / "go-tmp"
    go_cache.mkdir(parents=True, exist_ok=True)
    go_tmp.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.setdefault("GOCACHE", str(go_cache))
    env.setdefault("GOTMPDIR", str(go_tmp))
    result = subprocess.run(
        ["go", "list", "-deps", "-f", "{{if not .Standard}}{{.ImportPath}}{{end}}", "./..."],
        cwd=root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stdout + result.stderr)

    violations: list[str] = []
    for line in result.stdout.splitlines():
        package = line.strip()
        if not package:
            continue
        if package == main_module or package.startswith(main_module + "/"):
            continue
        violations.append(package)
    return violations


def collect_go_dependency_violations(root: Path, run_go_list: bool = True) -> list[str]:
    go_mod = root / "go.mod"
    if not go_mod.exists():
        return ["go.mod:0: missing go.mod"]

    module_name, mod_violations = parse_go_mod(go_mod.read_text(encoding="utf-8"))
    violations = [
        f"go.mod:{violation.line}: forbidden Go module {violation.module} ({violation.reason})"
        for violation in mod_violations
    ]

    go_sum = root / "go.sum"
    if go_sum.exists():
        violations.extend(
            f"go.sum:{violation.line}: forbidden Go checksum {violation.module}"
            for violation in go_sum_violations(go_sum.read_text(encoding="utf-8"))
        )

    if run_go_list and module_name:
        for package in go_list_module_violations(root, module_name):
            violations.append(f"go list:0: forbidden Go dependency package {package}")

    return violations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=Path(__file__).resolve().parents[1], type=Path)
    parser.add_argument("--no-go-list", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    violations = collect_go_dependency_violations(args.root, run_go_list=not args.no_go_list)
    if violations:
        for violation in violations:
            print(violation)
        return 1
    print("GO_RUNTIME_DEPENDENCY_POLICY_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
