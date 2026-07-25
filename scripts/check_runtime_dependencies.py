import ast
import sys
from pathlib import Path


ALLOWED_RUNTIME_EXTERNALS = {
    "tiktoken",
}

RUNTIME_PATHS = [
    Path("cida"),
    Path("markdown"),
    Path("md_minifier.py"),
    Path("motor_v2.py"),
    Path("token_counter.py"),
    Path("token_optimizer.py"),
    Path("translate.py"),
]

LOCAL_MODULES = {
    "cida",
    "markdown",
    "md_minifier",
    "motor_v2",
    "token_counter",
    "token_optimizer",
    "translate",
}

def runtime_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for runtime_path in RUNTIME_PATHS:
        full_path = root / runtime_path
        if full_path.is_dir():
            files.extend(sorted(full_path.rglob("*.py")))
        elif full_path.exists():
            files.append(full_path)
    return files


def module_root(module_name: str) -> str:
    return module_name.split(".", 1)[0]


def is_allowed_module(module_name: str) -> bool:
    root_name = module_root(module_name)
    if root_name in LOCAL_MODULES:
        return True
    if root_name in ALLOWED_RUNTIME_EXTERNALS:
        return True
    if root_name in sys.stdlib_module_names:
        return True
    return False


def collect_violations(path: Path, source: str) -> list[tuple[int, str]]:
    violations: list[tuple[int, str]] = []
    tree = ast.parse(source, filename=str(path))
    importlib_aliases = {"importlib"}
    import_module_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "importlib":
                    importlib_aliases.add(alias.asname or alias.name)
                if not is_allowed_module(alias.name):
                    violations.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            if node.module == "importlib":
                for alias in node.names:
                    if alias.name == "import_module":
                        import_module_aliases.add(alias.asname or alias.name)
            if node.module and not is_allowed_module(node.module):
                violations.append((node.lineno, node.module))
        elif isinstance(node, ast.Call):
            module_name = literal_dynamic_import_name(node, importlib_aliases, import_module_aliases)
            if module_name and not is_allowed_module(module_name):
                violations.append((getattr(node, "lineno", 0), module_name))
    return violations


def literal_dynamic_import_name(
    node: ast.Call,
    importlib_aliases: set[str],
    import_module_aliases: set[str],
) -> str | None:
    if not node.args:
        return None
    first_arg = node.args[0]
    if not isinstance(first_arg, ast.Constant) or not isinstance(first_arg.value, str):
        return None

    func = node.func
    if isinstance(func, ast.Name):
        if func.id == "__import__" or func.id in import_module_aliases:
            return first_arg.value
    elif (
        isinstance(func, ast.Attribute)
        and func.attr == "import_module"
        and isinstance(func.value, ast.Name)
        and func.value.id in importlib_aliases
    ):
        return first_arg.value
    return None


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    violations: list[str] = []
    for path in runtime_files(root):
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            source = path.read_text(encoding="utf-8-sig")
        for line_no, module_name in collect_violations(path, source):
            rel = path.relative_to(root).as_posix()
            violations.append(f"{rel}:{line_no}: forbidden runtime import {module_name}")

    if violations:
        for violation in violations:
            print(violation)
        return 1
    print("RUNTIME_DEPENDENCY_POLICY_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
