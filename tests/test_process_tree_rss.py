import subprocess
import sys
import time

from harness.process_tree_probe import ProcessTreeSampler


def test_process_tree_probe_counts_child_rss():
    child_code = "import time; data=bytearray(20_000_000); time.sleep(1.5); print(len(data))"
    parent_code = (
        "import subprocess, sys; "
        f"p=subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "p.wait()"
    )
    proc = subprocess.Popen([sys.executable, "-c", parent_code], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    sampler = ProcessTreeSampler(proc.pid)
    deadline = time.time() + 3.0
    while proc.poll() is None and time.time() < deadline:
        sampler.sample()
        time.sleep(0.01)
    proc.communicate(timeout=5)
    metrics = sampler.sample()

    assert metrics.peak_process_count >= 2
    assert metrics.child_pids_seen
    assert metrics.children_peak_rss > 5_000_000
    assert metrics.process_tree_peak_rss >= metrics.children_peak_rss
