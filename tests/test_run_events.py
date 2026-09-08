"""G3: event stream (R3-A2), in-flight cancel (R3-A1), override→phase (R3-A3)."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


@pytest.fixture(autouse=True)
def _src_on_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.syspath_prepend(str(SRC))
    monkeypatch.setenv("CSD_HARNESS_STATE", str(tmp_path / "harness-state"))


def test_event_seq_gapless_and_resume(tmp_path: Path) -> None:
    """Strictly incrementing seq; resume after disconnect misses nothing (R3-A2)."""
    from csd_autodev import events, store

    run = store.create_run({"goal": "g3-seq", "status": "running", "source": "test"})
    run_id = run["run_id"]
    kinds = ["tick", "phase", "lease", "thought", "input", "output"]
    for kind in kinds:
        events.append_event(run_id, kind, {"text": kind})

    all_ev = events.list_events(run_id, after=0)
    seqs = [int(e["seq"]) for e in all_ev]
    assert seqs == list(range(1, len(kinds) + 1))
    assert {e["kind"] for e in all_ev} >= {"tick", "phase", "lease"}

    # Deliberate "disconnect": client kept last seq, then resumes.
    last = seqs[2]
    resumed = events.list_events(run_id, after=last)
    assert [int(e["seq"]) for e in resumed] == seqs[3:]
    assert resumed[0]["seq"] == last + 1


def test_halt_cancels_sleep_child_under_2s(tmp_path: Path) -> None:
    """Halt kills a long subprocess stand-in in <2s and emits call.cancelled (R3-A1)."""
    from csd_autodev import events, store

    run = store.create_run({"goal": "g3-cancel", "status": "running", "source": "test"})
    run_id = run["run_id"]
    proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    events.register_call(run_id, int(proc.pid), argv=["sleep", "60"])
    assert events.get_call(run_id) is not None

    t0 = time.time()
    out = events.post_input(run_id, {"text": "/halt"})
    dt = time.time() - t0

    # Child gone quickly.
    deadline = time.time() + 2.0
    while time.time() < deadline and proc.poll() is None:
        time.sleep(0.05)
    assert proc.poll() is not None, "child still alive after halt"
    assert dt < 2.0
    assert out.get("cancel", {}).get("alive") is False

    kinds = [e["kind"] for e in events.list_events(run_id, after=0)]
    assert "operator" in kinds
    assert "call.cancelled" in kinds
    cancelled = [
        e for e in events.list_events(run_id, after=0) if e["kind"] == "call.cancelled"
    ]
    assert cancelled[-1]["payload"]["alive"] is False
    assert float(cancelled[-1]["payload"]["dt"]) < 2.0


def test_override_reaches_next_phase_input(tmp_path: Path) -> None:
    """Pending operator text is consumed into the next phase, not deferred (R3-A3)."""
    from csd_autodev import events, store

    run = store.create_run({"goal": "g3-override", "status": "running", "source": "test"})
    run_id = run["run_id"]
    token = "UNIQUE-OVERRIDE-TOKEN-9f3a"
    events.append_event(run_id, "phase", {"name": "phase-a", "state": "start"})
    events.post_input(run_id, {"text": token})
    # Still mid-phase-a; override sits pending.
    assert events.pending_override(run_id) == token

    # Next phase input assembly (what the loop does via consume_override API).
    consumed = events.consume_override(run_id)
    assert consumed["text"] == token
    assert events.pending_override(run_id) is None

    phase_input = f"model prompt\nOperator override (live feed):\n{token}\n"
    events.append_event(run_id, "input", {"text": phase_input, "phase": "phase-b"})
    events.append_event(run_id, "phase", {"name": "phase-b", "state": "start"})

    feed = events.list_events(run_id, after=0)
    assert any(e["kind"] == "operator" and token in str(e["payload"]) for e in feed)
    inp = [e for e in feed if e["kind"] == "input"][-1]
    assert token in str(inp["payload"].get("text"))


def test_handover_does_not_cancel_call(tmp_path: Path) -> None:
    from csd_autodev import events, store

    run = store.create_run({"goal": "g3-handover", "status": "running"})
    run_id = run["run_id"]
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    events.register_call(run_id, int(proc.pid))
    events.post_input(run_id, {"text": "/take-control"})
    time.sleep(0.2)
    assert proc.poll() is None
    kinds = [e["kind"] for e in events.list_events(run_id, after=0)]
    assert "handover" in kinds
    assert "call.cancelled" not in kinds
    os.killpg(proc.pid, signal.SIGTERM)
    proc.wait(timeout=2)


def test_resource_routes_events_and_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lab console shared routes publish events and accept operator input."""
    # Import console after env is set so store paths resolve under tmp_path.
    import importlib.machinery
    import importlib.util

    script = ROOT / "scripts" / "csd-lab-console"
    loader = importlib.machinery.SourceFileLoader("csd_lab_console_g3", str(script))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    from csd_autodev import store

    run = store.create_run({"goal": "g3-routes", "status": "running"})
    run_id = run["run_id"]
    code, rec = mod._resource_routes(
        "POST",
        f"/api/runs/{run_id}/events",
        {"kind": "thought", "payload": {"text": "hi"}},
    )
    assert code == 200
    assert rec["event"]["seq"] == 1
    code, rec = mod._resource_routes(
        "POST", f"/api/runs/{run_id}/input", {"text": "steer me"}
    )
    assert code == 200
    assert rec["event"]["kind"] == "operator"
    code, rec = mod._resource_routes("POST", f"/api/runs/{run_id}/override/consume", {})
    assert code == 200
    assert rec["text"] == "steer me"
    code, rec = mod._resource_routes("GET", f"/api/runs/{run_id}/events", {"after": "0"})
    assert code == 200
    assert len(rec["events"]) >= 2


def test_sse_http_resume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Minimal SSE server using events.format_sse; resume after last seq."""
    from csd_autodev import events, store
    from csd_client.api import Client

    run = store.create_run({"goal": "g3-sse", "status": "running"})
    run_id = run["run_id"]
    for kind in ("tick", "phase", "lease"):
        events.append_event(run_id, kind, {"text": kind})

    class H(BaseHTTPRequestHandler):
        def log_message(self, *_a: object) -> None:
            return

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if not parsed.path.endswith("/events"):
                self.send_error(404)
                return
            q = parse_qs(parsed.query)
            after = int((q.get("after") or ["0"])[0])
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for ev in events.list_events(run_id, after=after):
                self.wfile.write(events.format_sse(ev))
            # End stream so the client iterator finishes (test-only).

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        client = Client(base=f"http://127.0.0.1:{port}")
        got = list(client.iter_events(run_id, after=0, timeout=5.0))
        assert [e["seq"] for e in got] == [1, 2, 3]
        # Resume after disconnect from last seq — no gaps.
        got2 = list(client.iter_events(run_id, after=2, timeout=5.0))
        assert [e["seq"] for e in got2] == [3]
        assert got2[0]["kind"] == "lease"
    finally:
        httpd.shutdown()
