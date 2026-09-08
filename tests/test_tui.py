"""G4: textual TUI import guard (R1-A2) and headless pilot snapshot."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

LOOP_PREFIXES = (
    "csd_autodev_loop",
    "csd_lab_console",
    "csd_autodev.loop",
)


@pytest.fixture(autouse=True)
def _src_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(SRC))


class FakeClient:
    """HTTP stand-in: same shapes as csd_client.api.Client for the TUI."""

    def __init__(self, run_id: str = "run-pilot-abc123") -> None:
        self.run_id = run_id
        self.posted: list[dict[str, Any]] = []

    def list_runs(self) -> dict[str, Any]:
        return {
            "runs": [
                {
                    "run_id": self.run_id,
                    "status": "running",
                    "checkpoint_sha256": "deadbeefcafebabe0123456789abcdef",
                    "goal": "g4-pilot",
                }
            ]
        }

    def get_run(self, run_id: str) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "status": "running",
            "checkpoint_sha256": "deadbeefcafebabe0123456789abcdef",
            "goal": "g4-pilot",
        }

    def list_events(self, run_id: str, *, after: int = 0) -> dict[str, Any]:
        events = [
            {
                "seq": 1,
                "run_id": run_id,
                "kind": "tick",
                "ts": 1.0,
                "checkpoint_sha256": "deadbeefcafebabe0123456789abcdef",
                "payload": {"text": "hello"},
            },
            {
                "seq": 2,
                "run_id": run_id,
                "kind": "thought",
                "ts": 2.0,
                "checkpoint_sha256": "deadbeefcafebabe0123456789abcdef",
                "payload": {"text": "reasoning"},
            },
        ]
        return {
            "ok": True,
            "run_id": run_id,
            "events": [e for e in events if e["seq"] > after],
        }

    def post_input(self, run_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        self.posted.append({"run_id": run_id, **fields})
        return {"ok": True, "event": {"kind": "operator", "payload": fields}}


def test_tui_imports_no_loop() -> None:
    """Importing the TUI entry loads no loop-library / console server module."""
    # textual is the TUI client's dependency, not the harness's. Skipping
    # keeps a real boundary check honest rather than permanently red;
    # install textual and it runs.
    pytest.importorskip("textual")
    doomed = [
        name
        for name in list(sys.modules)
        if name == "csd_client"
        or name.startswith("csd_client.")
        or any(name == p or name.startswith(p + ".") for p in LOOP_PREFIXES)
        or name.startswith("csd_autodev")
    ]
    for name in doomed:
        sys.modules.pop(name, None)

    importlib.invalidate_caches()
    mod = importlib.import_module("csd_client.tui")
    assert mod is not None

    offenders = sorted(
        name
        for name in sys.modules
        if any(name == p or name.startswith(p + ".") for p in LOOP_PREFIXES)
    )
    assert offenders == [], f"TUI import pulled loop/console modules: {offenders}"


@pytest.mark.asyncio
async def test_tui_pilot_run_list_and_detail_snapshot() -> None:
    """Headless pilot: run list + detail show the same run_id CLI list would return."""
    from textual.widgets import RichLog

    from csd_client.tui import AutodevApp

    fake = FakeClient(run_id="run-pilot-abc123")
    cli_ids = [r["run_id"] for r in fake.list_runs()["runs"]]
    assert cli_ids == ["run-pilot-abc123"]

    app = AutodevApp(client=fake, initial_run_id=fake.run_id)  # type: ignore[arg-type]
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        # Snapshot / tree text must contain the CLI run_id.
        feed = app.query_one("#feed", RichLog)
        text = "\n".join(str(line) for line in feed.lines)
        tree = app.tree
        blob = text + "\n" + str(tree)
        assert fake.run_id in blob
        assert cli_ids[0] in blob
        # Live feed kinds from the API appear (not invented lines).
        assert "tick" in blob or "[1]" in blob
        # Composer is present (one field).
        assert app.query_one("#composer") is not None
        # Open via list selection still keeps the same id.
        await pilot.pause()
        assert app.active_run_id == fake.run_id
