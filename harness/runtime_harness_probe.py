from __future__ import annotations

import builtins
import importlib
import os
import pathlib
import subprocess
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.phase_contract import (
    PhaseContractResult,
    append_phase_event,
    new_session_id,
    normalize_required_phases,
    validate_required_phases,
)


FORBIDDEN_MARKERS = ("harness", "cidaharness", "attestation", "governance")


@dataclass
class HarnessProbeEvents:
    imports: list[str] = field(default_factory=list)
    file_reads: list[str] = field(default_factory=list)
    subprocesses: list[str] = field(default_factory=list)
    environment_accesses: list[str] = field(default_factory=list)
    discovered_paths: list[str] = field(default_factory=list)
    phases: list[dict[str, Any]] = field(default_factory=list)
    exceptions: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, list[Any]]:
        return {
            "imports": self.imports,
            "file_reads": self.file_reads,
            "subprocesses": self.subprocesses,
            "environment_accesses": self.environment_accesses,
            "discovered_paths": self.discovered_paths,
            "phases": self.phases,
            "exceptions": self.exceptions,
        }


@dataclass(frozen=True)
class RuntimeDependencyGraph:
    runner: str
    entrypoints: tuple[str, ...]
    runtime_files: tuple[str, ...]
    subprocess_edges: tuple[tuple[str, str], ...]
    configuration_files: tuple[str, ...]
    dependency_files: tuple[str, ...]


class OriginalProjectHarness:
    PYTHON_RUNTIME_CANDIDATES = (
        "token_optimizer.py",
        "translate.py",
        "decompress.py",
    )
    CONFIGURATION_CANDIDATES = (
        "requirements-runtime.txt",
        "requirements-ci.txt",
        "requirements-dev.txt",
    )
    DEPENDENCY_CANDIDATES = (
        "go.mod",
        "go.sum",
        "resources/cl100k_base.tiktoken",
        "resources/9b5ad71b2ce5302211f9c61530b329a4922fc6a4",
        "resources/46e9c078a52e9498de8130056e047337",
    )

    def inspect(self, project_root: Path, runner: str) -> RuntimeDependencyGraph:
        root = project_root.resolve()
        runner = runner.lower()
        entrypoints = self._entrypoints(root, runner)
        runtime_files = self._runtime_files(root, runner)
        subprocess_edges = self._subprocess_edges(root, runner)
        configuration_files = self._existing(root, self.CONFIGURATION_CANDIDATES)
        dependency_files = self._existing(root, self.DEPENDENCY_CANDIDATES)
        return RuntimeDependencyGraph(
            runner=runner,
            entrypoints=entrypoints,
            runtime_files=runtime_files,
            subprocess_edges=subprocess_edges,
            configuration_files=configuration_files,
            dependency_files=dependency_files,
        )

    def _entrypoints(self, root: Path, runner: str) -> tuple[str, ...]:
        if runner == "go":
            return self._existing(root, ("motor_v3.go",))
        return self._existing(root, ("token_optimizer.py", "cida/interfaces/cli.py"))

    def _runtime_files(self, root: Path, runner: str) -> tuple[str, ...]:
        paths: list[str] = []
        if runner == "go":
            paths.extend(self._existing(root, ("motor_v3.go",)))
        paths.extend(self._existing(root, self.PYTHON_RUNTIME_CANDIDATES))
        cida_dir = root / "cida"
        if cida_dir.exists():
            paths.extend(path.relative_to(root).as_posix() for path in sorted(cida_dir.rglob("*.py")))
        return tuple(dict.fromkeys(paths))

    def _subprocess_edges(self, root: Path, runner: str) -> tuple[tuple[str, str], ...]:
        if runner != "go":
            return ()
        motor = root / "motor_v3.go"
        if not motor.exists():
            return ()
        text = motor.read_text(encoding="utf-8", errors="replace")
        edges: list[tuple[str, str]] = []
        for target in ("token_optimizer.py", "python", "python3"):
            if target in text:
                edges.append(("motor_v3.go", target))
        return tuple(edges)

    @staticmethod
    def _existing(root: Path, relpaths: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(rel for rel in relpaths if (root / rel).exists())


def _contains_forbidden(value: Any) -> bool:
    text = str(value).lower()
    return any(marker in text for marker in FORBIDDEN_MARKERS)


class RuntimeHarnessProbe(AbstractContextManager):
    def __init__(
        self,
        required_phases: tuple[str, ...] | list[str] | str | None = None,
        *,
        event_file: str | os.PathLike[str] | None = None,
        session_id: str | None = None,
    ) -> None:
        self.events = HarnessProbeEvents()
        self.required_phases = normalize_required_phases(required_phases)
        self.event_file = event_file or os.getenv("CIDA_HARNESS_EVENT_FILE")
        self.session_id = session_id or os.getenv("CIDA_HARNESS_SESSION_ID") or new_session_id()
        self.phase_result = PhaseContractResult(True, (), ())
        self._orig_import = builtins.__import__
        self._orig_import_module = importlib.import_module
        self._orig_open = builtins.open
        self._orig_path_open = pathlib.Path.open
        self._orig_popen = subprocess.Popen
        self._orig_run = subprocess.run
        self._orig_getenv = os.getenv
        self._orig_environ_get = os.environ.get
        self._orig_exists = os.path.exists
        self._orig_isdir = os.path.isdir
        self._orig_listdir = os.listdir

    def __enter__(self) -> "RuntimeHarnessProbe":
        def import_guard(name, globals=None, locals=None, fromlist=(), level=0):
            if _contains_forbidden(name):
                self.events.imports.append(str(name))
            return self._orig_import(name, globals, locals, fromlist, level)

        def import_module_guard(name, package=None):
            if _contains_forbidden(name):
                self.events.imports.append(str(name))
            return self._orig_import_module(name, package)

        def open_guard(file, *args, **kwargs):
            if _contains_forbidden(file):
                self.events.file_reads.append(str(file))
            return self._orig_open(file, *args, **kwargs)

        def path_open_guard(path_self, *args, **kwargs):
            if _contains_forbidden(path_self):
                self.events.file_reads.append(str(path_self))
            return self._orig_path_open(path_self, *args, **kwargs)

        def popen_guard(cmd, *args, **kwargs):
            if _contains_forbidden(cmd):
                self.events.subprocesses.append(str(cmd))
            self._inject_child_harness_env(kwargs)
            return self._orig_popen(cmd, *args, **kwargs)

        def run_guard(cmd, *args, **kwargs):
            if _contains_forbidden(cmd):
                self.events.subprocesses.append(str(cmd))
            self._inject_child_harness_env(kwargs)
            return self._orig_run(cmd, *args, **kwargs)

        def getenv_guard(key, default=None):
            if _contains_forbidden(key):
                self.events.environment_accesses.append(str(key))
            return self._orig_getenv(key, default)

        def environ_get_guard(key, default=None):
            if _contains_forbidden(key):
                self.events.environment_accesses.append(str(key))
            return self._orig_environ_get(key, default)

        def exists_guard(path):
            if _contains_forbidden(path):
                self.events.discovered_paths.append(str(path))
            return self._orig_exists(path)

        def isdir_guard(path):
            if _contains_forbidden(path):
                self.events.discovered_paths.append(str(path))
            return self._orig_isdir(path)

        def listdir_guard(path=None):
            if _contains_forbidden(path):
                self.events.discovered_paths.append(str(path))
            if path is None:
                return self._orig_listdir()
            return self._orig_listdir(path)

        builtins.__import__ = import_guard
        importlib.import_module = import_module_guard
        builtins.open = open_guard
        pathlib.Path.open = path_open_guard
        subprocess.Popen = popen_guard
        subprocess.run = run_guard
        os.getenv = getenv_guard
        os.environ.get = environ_get_guard
        os.path.exists = exists_guard
        os.path.isdir = isdir_guard
        os.listdir = listdir_guard
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        builtins.__import__ = self._orig_import
        importlib.import_module = self._orig_import_module
        builtins.open = self._orig_open
        pathlib.Path.open = self._orig_path_open
        subprocess.Popen = self._orig_popen
        subprocess.run = self._orig_run
        os.getenv = self._orig_getenv
        os.environ.get = self._orig_environ_get
        os.path.exists = self._orig_exists
        os.path.isdir = self._orig_isdir
        os.listdir = self._orig_listdir
        if exc_value is not None:
            self.events.exceptions.append(repr(exc_value))
        self.phase_result = validate_required_phases(self.events.phases, self.required_phases)
        return False

    def record_phase(self, phase: str, **metadata: Any) -> None:
        event = append_phase_event(
            self.event_file,
            session_id=self.session_id,
            phase=phase,
            metadata=metadata,
        )
        self.events.phases.append(event.as_dict())

    @property
    def counters(self) -> dict[str, int]:
        return {
            "harness_imports": len(self.events.imports),
            "harness_file_reads": len(self.events.file_reads),
            "harness_subprocesses": len(self.events.subprocesses),
            "harness_environment_accesses": len(self.events.environment_accesses),
            "harness_module_discovery": len(self.events.discovered_paths),
            "harness_initializations": 0,
            "harness_tokens_loaded": 0,
        }

    def _inject_child_harness_env(self, kwargs: dict[str, Any]) -> None:
        if not self.event_file and not self.required_phases:
            return
        env = dict(kwargs.get("env") or os.environ)
        if self.event_file:
            env.setdefault("CIDA_HARNESS_EVENT_FILE", str(self.event_file))
        env.setdefault("CIDA_HARNESS_SESSION_ID", self.session_id)
        if self.required_phases:
            env.setdefault("CIDA_HARNESS_REQUIRED_PHASES", ",".join(self.required_phases))
        kwargs["env"] = env
