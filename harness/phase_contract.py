from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


REQUIRED_PHASES: tuple[str, ...] = (
    "PRODUCTION_CLI_START",
    "PRODUCTION_CLI_COMPLETE",
    "ORIGINAL_SEARCH_START",
    "ORIGINAL_SEARCH_COMPLETE",
    "TKNC_SEARCH_START",
    "TKNC_SEARCH_COMPLETE",
    "SEARCH_INDEX_LOAD",
    "ALIAS_INDEX_LOAD",
    "SOURCE_MANIFEST_VALIDATE",
    "BUNDLE_MANIFEST_VALIDATE",
    "CONTENT_ARTIFACT_VALIDATE",
    "ALIAS_MEMBERSHIP_CHECK",
    "SIDECAR_LOAD",
    "ALIAS_RESOLUTION",
    "RECONSTRUCTION",
    "WARM_SESSION_START",
    "WARM_SESSION_QUERY",
    "WARM_SESSION_COMPLETE",
    "MULTI_CHUNK_SESSION_START",
    "MULTI_CHUNK_SESSION_COMPLETE",
    "BENCHMARK_COMPLETE",
)


@dataclass(frozen=True)
class PhaseEvent:
    session_id: str
    phase: str
    pid: int
    ppid: int
    timestamp: float
    metadata: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "phase": self.phase,
            "pid": self.pid,
            "ppid": self.ppid,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class PhaseContractResult:
    measured: bool
    missing_phases: tuple[str, ...]
    observed_phases: tuple[str, ...]


def new_session_id() -> str:
    return uuid.uuid4().hex


def normalize_required_phases(required: Iterable[str] | str | None) -> tuple[str, ...]:
    if required is None:
        return ()
    if isinstance(required, str):
        if not required:
            return ()
        try:
            parsed = json.loads(required)
            if isinstance(parsed, list):
                return tuple(str(item) for item in parsed)
        except json.JSONDecodeError:
            pass
        return tuple(item.strip() for item in required.split(",") if item.strip())
    return tuple(str(item) for item in required)


def validate_required_phases(events: Iterable[dict[str, Any] | PhaseEvent], required: Iterable[str]) -> PhaseContractResult:
    required_phases = tuple(dict.fromkeys(str(item) for item in required))
    observed = []
    for event in events:
        phase = event.phase if isinstance(event, PhaseEvent) else event.get("phase")
        if isinstance(phase, str) and phase:
            observed.append(phase)
    observed_phases = tuple(dict.fromkeys(observed))
    missing = tuple(phase for phase in required_phases if phase not in set(observed_phases))
    return PhaseContractResult(
        measured=not missing,
        missing_phases=missing,
        observed_phases=observed_phases,
    )


def append_phase_event(
    event_file: str | os.PathLike[str] | None,
    *,
    session_id: str,
    phase: str,
    metadata: dict[str, Any] | None = None,
) -> PhaseEvent:
    event = PhaseEvent(
        session_id=session_id,
        phase=phase,
        pid=os.getpid(),
        ppid=os.getppid(),
        timestamp=time.time(),
        metadata=metadata or {},
    )
    if event_file:
        path = Path(event_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.as_dict(), sort_keys=True) + "\n")
    return event


def read_phase_events(event_file: str | os.PathLike[str]) -> list[dict[str, Any]]:
    path = Path(event_file)
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        if isinstance(data, dict):
            events.append(data)
    return events
