from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ProcessTreeMetrics:
    parent_peak_rss: int = 0
    children_peak_rss: int = 0
    process_tree_peak_rss: int = 0
    peak_process_count: int = 0
    child_pids_seen: tuple[int, ...] = ()


@dataclass
class ProcessTreeSampler:
    root_pid: int
    _parent_peak_rss: int = 0
    _children_peak_rss: int = 0
    _process_tree_peak_rss: int = 0
    _peak_process_count: int = 0
    _child_pids_seen: set[int] = field(default_factory=set)

    def sample(self) -> ProcessTreeMetrics:
        pids = tuple(dict.fromkeys((self.root_pid, *self._descendants(self.root_pid))))
        parent_rss = _process_rss_bytes(self.root_pid)
        child_values = [_process_rss_bytes(pid) for pid in pids if pid != self.root_pid]
        children_rss = sum(child_values)
        tree_rss = parent_rss + children_rss
        self._parent_peak_rss = max(self._parent_peak_rss, parent_rss)
        self._children_peak_rss = max(self._children_peak_rss, children_rss)
        self._process_tree_peak_rss = max(self._process_tree_peak_rss, tree_rss)
        self._peak_process_count = max(self._peak_process_count, len(pids))
        self._child_pids_seen.update(pid for pid in pids if pid != self.root_pid)
        return self.metrics()

    def metrics(self) -> ProcessTreeMetrics:
        return ProcessTreeMetrics(
            parent_peak_rss=self._parent_peak_rss,
            children_peak_rss=self._children_peak_rss,
            process_tree_peak_rss=self._process_tree_peak_rss,
            peak_process_count=self._peak_process_count,
            child_pids_seen=tuple(sorted(self._child_pids_seen)),
        )

    @staticmethod
    def _descendants(pid: int) -> tuple[int, ...]:
        children_by_parent = _children_by_parent()
        descendants: list[int] = []
        stack = list(children_by_parent.get(pid, ()))
        while stack:
            child = stack.pop()
            if child in descendants:
                continue
            descendants.append(child)
            stack.extend(children_by_parent.get(child, ()))
        return tuple(descendants)


def _children_by_parent() -> dict[int, tuple[int, ...]]:
    if sys.platform == "win32":
        return _windows_children_by_parent()
    return _proc_children_by_parent()


def _proc_children_by_parent() -> dict[int, tuple[int, ...]]:
    result: dict[int, list[int]] = {}
    proc = Path("/proc")
    if not proc.exists():
        return {}
    for child_dir in proc.iterdir():
        if not child_dir.name.isdigit():
            continue
        try:
            stat = (child_dir / "stat").read_text(encoding="utf-8", errors="replace")
            # /proc/<pid>/stat contains the command in parentheses; ppid is the
            # second field after the final closing paren.
            tail = stat.rsplit(")", 1)[1].split()
            ppid = int(tail[1])
            result.setdefault(ppid, []).append(int(child_dir.name))
        except (OSError, ValueError, IndexError):
            continue
    return {ppid: tuple(children) for ppid, children in result.items()}


def _process_rss_bytes(pid: int) -> int:
    if sys.platform == "win32":
        return _windows_process_rss_bytes(pid)
    status = Path(f"/proc/{pid}/status")
    try:
        for line in status.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) * 1024
            if line.startswith("VmRSS:"):
                fallback = int(line.split()[1]) * 1024
        return fallback
    except (OSError, ValueError, UnboundLocalError):
        return 0


def _windows_process_rss_bytes(pid: int) -> int:
    try:
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


def _windows_children_by_parent() -> dict[int, tuple[int, ...]]:
    try:
        TH32CS_SNAPPROCESS = 0x00000002
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", ctypes.wintypes.DWORD),
                ("cntUsage", ctypes.wintypes.DWORD),
                ("th32ProcessID", ctypes.wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.wintypes.ULONG)),
                ("th32ModuleID", ctypes.wintypes.DWORD),
                ("cntThreads", ctypes.wintypes.DWORD),
                ("th32ParentProcessID", ctypes.wintypes.DWORD),
                ("pcPriClassBase", ctypes.wintypes.LONG),
                ("dwFlags", ctypes.wintypes.DWORD),
                ("szExeFile", ctypes.c_char * 260),
            ]

        snapshot = ctypes.windll.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot == INVALID_HANDLE_VALUE:
            return {}
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        result: dict[int, list[int]] = {}
        has_entry = ctypes.windll.kernel32.Process32First(snapshot, ctypes.byref(entry))
        while has_entry:
            result.setdefault(int(entry.th32ParentProcessID), []).append(int(entry.th32ProcessID))
            has_entry = ctypes.windll.kernel32.Process32Next(snapshot, ctypes.byref(entry))
        ctypes.windll.kernel32.CloseHandle(snapshot)
        return {ppid: tuple(children) for ppid, children in result.items()}
    except Exception:
        return {}
