import importlib
import os
import subprocess
import sys

from harness.phase_contract import (
    PhaseEvent,
    append_phase_event,
    new_session_id,
    normalize_required_phases,
    read_phase_events,
    validate_required_phases,
)
from harness.runtime_harness_probe import HarnessProbeEvents, OriginalProjectHarness, RuntimeHarnessProbe


def test_phase_contract_normalizes_sources_and_validates_events(tmp_path):
    assert normalize_required_phases(None) == ()
    assert normalize_required_phases("") == ()
    assert normalize_required_phases('["A", "B"]') == ("A", "B")
    assert normalize_required_phases("A, B,,") == ("A", "B")
    assert normalize_required_phases(["A", 2]) == ("A", "2")
    assert len(new_session_id()) == 32

    event_path = tmp_path / "events" / "phases.jsonl"
    first = append_phase_event(event_path, session_id="s", phase="A", metadata={"n": 1})
    second = append_phase_event(None, session_id="s", phase="B")
    with event_path.open("a", encoding="utf-8") as handle:
        handle.write("\n")

    assert first.as_dict()["metadata"] == {"n": 1}
    assert second.phase == "B"
    assert read_phase_events(event_path)[0]["phase"] == "A"
    assert read_phase_events(tmp_path / "missing.jsonl") == []

    result = validate_required_phases([first, {"phase": "B"}, {"phase": ""}], ["A", "B", "C"])
    assert result.measured is False
    assert result.observed_phases == ("A", "B")
    assert result.missing_phases == ("C",)

    phase_event = PhaseEvent("s", "C", 1, 0, 1.0, {})
    assert validate_required_phases([phase_event], ["C"]).measured is True


def test_runtime_harness_probe_records_hooks_and_child_environment(tmp_path):
    event_path = tmp_path / "phase-events.jsonl"
    forbidden_file = tmp_path / "harness-marker.txt"
    forbidden_dir = tmp_path / "harness-dir"
    forbidden_dir.mkdir()

    with RuntimeHarnessProbe(required_phases=["A", "B"], event_file=event_path, session_id="session-1") as probe:
        importlib.import_module("harness.phase_contract")
        with open(forbidden_file, "w", encoding="utf-8") as handle:
            handle.write("x")
        forbidden_file.open("r", encoding="utf-8").close()
        os.getenv("CIDA_HARNESS_EVENT_FILE")
        os.environ.get("CIDA_HARNESS_SESSION_ID")
        os.path.exists(forbidden_file)
        os.path.isdir(forbidden_dir)
        os.listdir(forbidden_dir)
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                "import os; assert os.environ['CIDA_HARNESS_SESSION_ID'] == 'session-1'",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        probe.record_phase("A")

    assert child.returncode == 0
    assert probe.phase_result.measured is False
    assert probe.phase_result.missing_phases == ("B",)
    assert probe.counters["harness_imports"] >= 1
    assert probe.counters["harness_file_reads"] >= 2
    assert probe.counters["harness_environment_accesses"] >= 2
    assert probe.counters["harness_module_discovery"] >= 3
    assert read_phase_events(event_path)[0]["phase"] == "A"


def test_runtime_harness_probe_records_exceptions_and_restores_hooks():
    try:
        with RuntimeHarnessProbe() as probe:
            raise RuntimeError("probe failure")
    except RuntimeError:
        pass

    assert "RuntimeError" in probe.events.exceptions[0]
    assert probe.phase_result.measured is True


def test_probe_events_and_original_project_python_graph(tmp_path):
    events = HarnessProbeEvents(imports=["harness"])
    assert events.as_dict()["imports"] == ["harness"]

    (tmp_path / "token_optimizer.py").write_text("print('x')", encoding="utf-8")
    cida_dir = tmp_path / "cida"
    cida_dir.mkdir()
    (cida_dir / "module.py").write_text("", encoding="utf-8")
    graph = OriginalProjectHarness().inspect(tmp_path, "python")

    assert graph.runner == "python"
    assert "token_optimizer.py" in graph.entrypoints
    assert "cida/module.py" in graph.runtime_files
    assert graph.subprocess_edges == ()
