import builtins
import importlib
import os
import pathlib
import subprocess
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any


FORBIDDEN_MARKERS = ("harness", "cidaharness", "attestation", "governance")


@dataclass
class HarnessProbeEvents:
    imports: list[str] = field(default_factory=list)
    file_reads: list[str] = field(default_factory=list)
    subprocesses: list[str] = field(default_factory=list)
    environment_accesses: list[str] = field(default_factory=list)
    discovered_paths: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, list[str]]:
        return {
            "imports": self.imports,
            "file_reads": self.file_reads,
            "subprocesses": self.subprocesses,
            "environment_accesses": self.environment_accesses,
            "discovered_paths": self.discovered_paths,
        }


def _contains_forbidden(value: Any) -> bool:
    text = str(value).lower()
    return any(marker in text for marker in FORBIDDEN_MARKERS)


class RuntimeHarnessProbe(AbstractContextManager):
    def __init__(self) -> None:
        self.events = HarnessProbeEvents()
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
            return self._orig_popen(cmd, *args, **kwargs)

        def run_guard(cmd, *args, **kwargs):
            if _contains_forbidden(cmd):
                self.events.subprocesses.append(str(cmd))
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
        return False

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
