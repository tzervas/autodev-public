"""Per-run event stream, operator input mailbox, and in-flight call cancel.

Events are gapless and monotonic in ``seq`` (R3-A2). Halt via POST input kills
a registered child in under 2s and emits ``call.cancelled`` (R3-A1). Pending
operator text is consumed into the next phase input (R3-A3).
"""

from __future__ import annotations

import json
import os
import signal
import threading
import time
from pathlib import Path
from typing import Any

from csd_autodev.store import _safe_id, get_run, state_root, update_run

EVENT_KINDS: frozenset[str] = frozenset(
    {
        "thought",
        "input",
        "output",
        "phase",
        "tick",
        "lease",
        "operator",
        "handover",
        "call.cancelled",
        # Decision-table lookups and escalations change what a run does, so
        # they land on the feed with everything else that steered it.
        "decision",
        "escalation",
    }
)

_LOCK = threading.RLock()
_WAITERS: dict[str, threading.Event] = {}


def _events_path(run_id: str) -> Path | None:
    safe = _safe_id(run_id)
    if not safe:
        return None
    root = state_root() / "events"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{safe}.jsonl"


def _meta_path(run_id: str) -> Path | None:
    safe = _safe_id(run_id)
    if not safe:
        return None
    root = state_root() / "events"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{safe}.meta.json"


def _call_path(run_id: str) -> Path | None:
    safe = _safe_id(run_id)
    if not safe:
        return None
    root = state_root() / "calls"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{safe}.json"


def _notify(run_id: str) -> None:
    ev = _WAITERS.get(run_id)
    if ev is not None:
        ev.set()


def _read_meta(run_id: str) -> dict[str, Any]:
    path = _meta_path(run_id)
    if path is None or not path.is_file():
        return {"seq": 0, "pending_input": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"seq": 0, "pending_input": None}
    return data if isinstance(data, dict) else {"seq": 0, "pending_input": None}


def _write_meta(run_id: str, meta: dict[str, Any]) -> None:
    path = _meta_path(run_id)
    if path is None:
        raise ValueError("bad run_id")
    path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def append_event(
    run_id: str,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    checkpoint_sha256: str | None = None,
    ts: float | None = None,
) -> dict[str, Any]:
    """Append one event. ``seq`` is gapless and monotonic per run."""
    if kind not in EVENT_KINDS:
        raise ValueError(f"unknown event kind: {kind}")
    path = _events_path(run_id)
    if path is None:
        raise ValueError("bad run_id")
    with _LOCK:
        meta = _read_meta(run_id)
        seq = int(meta.get("seq") or 0) + 1
        meta["seq"] = seq
        if checkpoint_sha256 is None:
            checkpoint_sha256 = meta.get("checkpoint_sha256")
            if not checkpoint_sha256:
                rec = get_run(run_id)
                if isinstance(rec, dict) and rec.get("checkpoint_sha256"):
                    checkpoint_sha256 = rec.get("checkpoint_sha256")
        if checkpoint_sha256:
            meta["checkpoint_sha256"] = checkpoint_sha256
        event = {
            "seq": seq,
            "run_id": run_id,
            "checkpoint_sha256": checkpoint_sha256,
            "kind": kind,
            "ts": float(ts if ts is not None else time.time()),
            "payload": payload if isinstance(payload, dict) else {},
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, separators=(",", ":")) + "\n")
        _write_meta(run_id, meta)
        _notify(run_id)
        return event


def list_events(run_id: str, *, after: int = 0) -> list[dict[str, Any]]:
    """Return events with ``seq > after`` in order (no gaps in the log)."""
    path = _events_path(run_id)
    if path is None or not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    with _LOCK:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        try:
            seq = int(ev.get("seq") or 0)
        except (TypeError, ValueError):
            continue
        if seq > after:
            out.append(ev)
    return out


def wait_events(
    run_id: str, *, after: int = 0, timeout: float = 1.0
) -> list[dict[str, Any]]:
    """Block until new events appear or ``timeout`` elapses."""
    hit = list_events(run_id, after=after)
    if hit:
        return hit
    with _LOCK:
        ev = _WAITERS.get(run_id)
        if ev is None:
            ev = threading.Event()
            _WAITERS[run_id] = ev
        else:
            ev.clear()
    ev.wait(timeout=max(0.05, float(timeout)))
    return list_events(run_id, after=after)


def _classify_input(body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Map operator POST body → (event kind, payload)."""
    kind_raw = str(body.get("kind") or "").strip().lower()
    text = str(body.get("text") or body.get("input") or body.get("note") or "")
    stripped = text.strip()
    lower = stripped.lower()

    if kind_raw in {"halt", "cancel"} or lower in {
        "halt",
        "/halt",
        "cancel",
        "/cancel",
    }:
        return "operator", {"text": stripped or "halt", "verb": "halt"}
    if (
        kind_raw == "handover"
        or lower.startswith("/take-control")
        or lower.startswith("/hand-back")
    ):
        verb = "take-control" if "take" in lower else "hand-back"
        if kind_raw == "handover" and body.get("verb"):
            verb = str(body.get("verb"))
        elif lower.startswith("/take-control"):
            verb = "take-control"
        elif lower.startswith("/hand-back"):
            verb = "hand-back"
        return "handover", {"text": stripped, "verb": verb}
    if kind_raw == "operator" or kind_raw == "":
        verb = str(body.get("verb") or "").strip().lower()
        if verb == "halt":
            return "operator", {"text": stripped or "halt", "verb": "halt"}
        return "operator", {"text": stripped, "verb": verb or "steer"}
    if kind_raw in EVENT_KINDS:
        return kind_raw, {
            "text": stripped,
            **{k: v for k, v in body.items() if k != "kind"},
        }
    return "operator", {"text": stripped, "verb": "steer"}


def post_input(run_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Operator field: append event; halt cancels in-flight call (R3-A1/A3)."""
    body = body if isinstance(body, dict) else {}
    kind, payload = _classify_input(body)
    checkpoint = body.get("checkpoint_sha256")
    with _LOCK:
        meta = _read_meta(run_id)
        text = str(payload.get("text") or "")
        verb = str(payload.get("verb") or "")
        is_halt = verb == "halt" or (kind == "operator" and payload.get("verb") == "halt")
        if kind == "operator" and verb != "halt" and text:
            meta["pending_input"] = text
            _write_meta(run_id, meta)
        elif kind == "handover":
            # Take-control / hand-back stay on the feed; do not restart the run.
            meta["control"] = verb
            _write_meta(run_id, meta)

    event = append_event(
        run_id,
        kind,
        payload,
        checkpoint_sha256=str(checkpoint) if checkpoint else None,
    )
    cancel_rec: dict[str, Any] | None = None
    if is_halt or (kind == "operator" and payload.get("verb") == "halt"):
        cancel_rec = cancel_call(run_id)
        # halted is an outcome state, not just a cancelled child.
        update_run(run_id, {"status": "halted"})
    out: dict[str, Any] = {"ok": True, "event": event}
    if cancel_rec is not None:
        out["cancel"] = cancel_rec
    return out


def consume_override(run_id: str) -> dict[str, Any]:
    """Pop pending operator text for the next phase input (R3-A3)."""
    with _LOCK:
        meta = _read_meta(run_id)
        text = meta.get("pending_input")
        meta["pending_input"] = None
        _write_meta(run_id, meta)
    return {"ok": True, "text": text, "run_id": run_id}


def pending_override(run_id: str) -> str | None:
    with _LOCK:
        text = _read_meta(run_id).get("pending_input")
    return str(text) if text else None


def register_call(
    run_id: str, pid: int, *, argv: list[str] | None = None
) -> dict[str, Any]:
    """Record an in-flight child so halt can kill it from the API process."""
    path = _call_path(run_id)
    if path is None:
        raise ValueError("bad run_id")
    try:
        pgid = os.getpgid(int(pid))
    except OSError:
        pgid = int(pid)
    rec = {
        "run_id": run_id,
        "pid": int(pid),
        "pgid": int(pgid),
        "t": time.time(),
        "argv": list(argv or [])[:20],
    }
    with _LOCK:
        path.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    return rec


def clear_call(run_id: str) -> dict[str, Any]:
    path = _call_path(run_id)
    if path is None:
        return {"ok": False, "error": "bad run_id"}
    with _LOCK:
        if path.is_file():
            path.unlink()
    return {"ok": True, "run_id": run_id}


def get_call(run_id: str) -> dict[str, Any] | None:
    path = _call_path(run_id)
    if path is None or not path.is_file():
        return None
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return rec if isinstance(rec, dict) else None


def _pid_alive(pid: int) -> bool:
    """True only if the pid is a live (non-zombie) process."""
    if pid <= 0:
        return False
    proc = Path(f"/proc/{pid}")
    if not proc.exists():
        return False
    try:
        # stat field 3 is state; Z = zombie (reap pending) — not runnable.
        stat = (proc / "stat").read_text(encoding="utf-8")
        # comm may contain spaces/parens; state is after the last ") ".
        state = stat.rsplit(")", 1)[-1].lstrip().split()[0]
        if state == "Z":
            return False
    except (OSError, IndexError):
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    else:
        return True


def cancel_call(run_id: str) -> dict[str, Any]:
    """SIGTERM then SIGKILL the registered child; emit ``call.cancelled``."""
    t0 = time.time()
    rec = get_call(run_id)
    if rec is None:
        # Still emit cancelled so the feed records the halt attempt.
        ev = append_event(
            run_id,
            "call.cancelled",
            {"reason": "halt", "pid": None, "alive": False, "dt": 0.0},
        )
        return {
            "ok": True,
            "cancelled": True,
            "pid": None,
            "alive": False,
            "event": ev,
            "dt": 0.0,
        }

    pid = int(rec.get("pid") or 0)
    pgid = int(rec.get("pgid") or pid)
    # Prefer process-group kill (loop starts calls in a new session).
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if not _pid_alive(pid):
            break
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            break
        except OSError:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                break
            except OSError:
                pass
        # Brief wait before escalating; total well under the 2s R3-A1 budget.
        deadline = time.time() + 0.5
        while time.time() < deadline and _pid_alive(pid):
            time.sleep(0.05)

    alive = _pid_alive(pid)
    dt = time.time() - t0
    clear_call(run_id)
    ev = append_event(
        run_id,
        "call.cancelled",
        {
            "reason": "halt",
            "pid": pid,
            "pgid": pgid,
            "alive": alive,
            "dt": dt,
        },
    )
    return {
        "ok": not alive,
        "cancelled": True,
        "pid": pid,
        "alive": alive,
        "dt": dt,
        "event": ev,
    }


def format_sse(event: dict[str, Any]) -> bytes:
    """One SSE frame: event name = kind, data = full JSON record."""
    kind = str(event.get("kind") or "message")
    data = json.dumps(event, separators=(",", ":"))
    return f"event: {kind}\ndata: {data}\nid: {event.get('seq')}\n\n".encode()
