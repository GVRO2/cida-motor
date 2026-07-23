import ast
from pathlib import Path


class ArchitectureVisitor(ast.NodeVisitor):
    def __init__(self, filename):
        self.filename = filename
        self.imports = []
        self.calls = []
        self.attribute_accesses = []

    def visit_Import(self, node):
        for alias in node.names:
            self.imports.append(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module:
            self.imports.append(node.module)
        self.generic_visit(node)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name):
            self.calls.append(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                self.calls.append(f"{node.func.value.id}.{node.func.attr}")
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if isinstance(node.value, ast.Name):
            self.attribute_accesses.append(f"{node.value.id}.{node.attr}")
        self.generic_visit(node)

def get_module_ast_info(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=filepath)
    visitor = ArchitectureVisitor(filepath)
    visitor.visit(tree)
    return visitor

def get_cida_py_files():
    cida_dir = Path(__file__).parent.parent.parent / "cida"
    return list(cida_dir.rglob("*.py"))

def filepath_to_module_name(filepath):
    rel = filepath.relative_to(filepath.parent.parent.parent)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts.pop()
    else:
        parts[-1] = parts[-1][:-3]
    return ".".join(parts)

def test_domain_layer_clean_architecture_boundaries():
    """Domain must not import application, infrastructure, interfaces, or markdown."""
    cida_dir = Path(__file__).parent.parent.parent / "cida"
    domain_files = list((cida_dir / "domain").rglob("*.py"))

    forbidden_imports = ["cida.application", "cida.infrastructure", "cida.interfaces", "cida.markdown"]
    forbidden_modules = ["os", "sys", "subprocess", "argparse", "pathlib", "tempfile", "shutil", "tiktoken", "yaml"]

    for file_path in domain_files:
        info = get_module_ast_info(file_path)
        for imp in info.imports:
            for forbidden_imp in forbidden_imports:
                assert not imp.startswith(forbidden_imp), (
                    f"Domain file {file_path.name} imports forbidden layer: {imp}"
                )
            for forbidden_mod in forbidden_modules:
                assert not (imp == forbidden_mod or imp.startswith(forbidden_mod + ".")), (
                    f"Domain file {file_path.name} imports forbidden module: {imp}"
                )

        assert "open" not in info.calls, f"Domain file {file_path.name} calls open()"
        assert "sys.exit" not in info.calls, f"Domain file {file_path.name} calls sys.exit()"
        assert "os.environ" not in info.attribute_accesses, f"Domain file {file_path.name} accesses os.environ"

def test_application_layer_clean_architecture_boundaries():
    """Application must not import concrete adapters or interfaces, sys.exit, or subprocess."""
    cida_dir = Path(__file__).parent.parent.parent / "cida"
    app_files = list((cida_dir / "application").rglob("*.py"))

    forbidden_imports = ["cida.infrastructure", "cida.interfaces"]

    for file_path in app_files:
        info = get_module_ast_info(file_path)
        for imp in info.imports:
            for forbidden_imp in forbidden_imports:
                assert not imp.startswith(forbidden_imp), (
                    f"Application file {file_path.name} imports concrete layer: {imp}"
                )

        assert "open" not in info.calls, f"Application file {file_path.name} calls open()"
        assert "sys.exit" not in info.calls, f"Application file {file_path.name} calls sys.exit()"
        assert "os.environ" not in info.attribute_accesses, f"Application file {file_path.name} accesses os.environ"

def test_infrastructure_layer_dependencies():
    """Infrastructure must not import interfaces layer."""
    cida_dir = Path(__file__).parent.parent.parent / "cida"
    infra_files = list((cida_dir / "infrastructure").rglob("*.py"))

    for file_path in infra_files:
        info = get_module_ast_info(file_path)
        for imp in info.imports:
            assert not imp.startswith("cida.interfaces"), (
                f"Infrastructure file {file_path.name} imports interfaces layer: {imp}"
            )

def test_no_sys_exit_outside_authorized_entrypoints():
    """sys.exit is prohibited outside authorized CLI interfaces and wrappers."""
    cida_dir = Path(__file__).parent.parent.parent / "cida"
    all_py_files = list(cida_dir.rglob("*.py"))

    authorized_files = {"cli.py", "decompress_cli.py"}

    for file_path in all_py_files:
        if file_path.name in authorized_files:
            continue
        info = get_module_ast_info(file_path)
        assert "sys.exit" not in info.calls, f"Unauthorized sys.exit call found in {file_path.name}"

def test_wrapper_delegation_boundaries():
    """Root wrappers must delegate directly to cida.interfaces."""
    root_dir = Path(__file__).parent.parent.parent
    wrappers = ["token_counter.py", "token_optimizer.py", "translate.py"]

    for wrapper_name in wrappers:
        wrapper_path = root_dir / wrapper_name
        if not wrapper_path.exists():
            continue
        info = get_module_ast_info(wrapper_path)
        has_cida_interface_import = any(imp.startswith("cida.interfaces") for imp in info.imports)
        assert has_cida_interface_import, f"Wrapper {wrapper_name} does not import from cida.interfaces"

def test_import_graph_cycle_detection():
    """Detects any circular dependency cycles in the cida package using Tarjan's SCC / DFS."""
    py_files = get_cida_py_files()
    module_files = {filepath_to_module_name(f): f for f in py_files}

    graph = {mod: set() for mod in module_files}

    for mod_name, file_path in module_files.items():
        info = get_module_ast_info(file_path)
        for imp in info.imports:
            # Check if imported module is inside cida package
            for target_mod in module_files:
                if imp == target_mod or imp.startswith(target_mod + "."):
                    if target_mod != mod_name:
                        graph[mod_name].add(target_mod)

    # DFS cycle detection
    visited = {}
    path = []
    cycles = []

    def dfs(node):
        visited[node] = 1
        path.append(node)

        for neighbor in graph.get(node, []):
            if visited.get(neighbor, 0) == 1:
                # Cycle found!
                cycle_start_idx = path.index(neighbor)
                cycle = path[cycle_start_idx:] + [neighbor]
                cycles.append(" -> ".join(cycle))
            elif visited.get(neighbor, 0) == 0:
                dfs(neighbor)

        path.pop()
        visited[node] = 2

    for node in graph:
        if visited.get(node, 0) == 0:
            dfs(node)

    assert not cycles, "Import cycles detected in cida package:\n" + "\n".join(cycles)

