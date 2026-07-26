import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from cida.infrastructure.tknc_context_session import ContextFilesystem, search_context
from cida.infrastructure.tokenizer import OfflineTokenizer


ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class HoldoutQuestion:
    question: str
    expected_file: str
    expected_symbol: str


def _write_holdout(root: Path) -> Path:
    source = root / "holdout-original"
    files = {
        "orion/flight_planner.py": (
            "def plan_lunar_arc():\n"
            "    return 'trajectory budget for lunar transfer windows'\n"
        ),
        "ledger/reconciliation_rules.py": (
            "class ReceiptMatcher:\n"
            "    purpose = 'matches invoices with settlement receipts and exception queues'\n"
        ),
        "ui/theme_tokens.ts": (
            "export const contrastRamp = 'accessible color contrast scale for warning panels';\n"
        ),
        "docs/operational-runbook.md": (
            "# Operational Runbook\n\n"
            "The standby rotation describes escalation owners and incident handoff windows.\n"
        ),
    }
    for rel, text in files.items():
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return source


def _build_bundle(source: Path, destination: Path) -> None:
    env = os.environ.copy()
    env["TIKTOKEN_CACHE_DIR"] = str(ROOT / "resources")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cida.interfaces.cli",
            "--src",
            str(source),
            "--dst",
            str(destination),
            "--mode",
            "semantic",
            "--dictionary-scope",
            "corpus",
            "--validation-level",
            "strict",
        ],
        cwd=str(ROOT),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_generic_search_holdout_retrieves_each_unseen_scenario(tmp_path):
    os.environ["TIKTOKEN_CACHE_DIR"] = str(ROOT / "resources")
    original = _write_holdout(tmp_path)
    bundle = tmp_path / "holdout-tknc"
    _build_bundle(original, bundle)
    tokenizer = OfflineTokenizer()
    questions = [
        HoldoutQuestion("Which module plans lunar transfer windows?", "orion/flight_planner.py.tknc", "plan_lunar_arc"),
        HoldoutQuestion("Where are invoice receipt matching exceptions handled?", "ledger/reconciliation_rules.py.tknc", "ReceiptMatcher"),
        HoldoutQuestion("Which file defines accessible warning contrast colors?", "ui/theme_tokens.ts.tknc", "contrastRamp"),
        HoldoutQuestion("Where is incident handoff standby rotation documented?", "docs/operational-runbook.md", "standby rotation"),
    ]

    for item in questions:
        fs = ContextFilesystem()
        result = search_context(bundle, item.question, fs, tokenizer, query_id=item.expected_file)

        assert result.files, item.question
        assert result.files[0] == item.expected_file
        selected_text = (bundle / result.files[0]).read_text(encoding="utf-8")
        assert item.expected_symbol.split()[0] in selected_text or item.expected_symbol in selected_text
