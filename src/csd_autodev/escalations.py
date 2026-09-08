"""Escalation contract: route by kind, carry the evidence, log the resolution.

Three rules, all structural rather than advisory:

1. **Route by kind, never broadcast.** *Is this true / what does the
   literature say* goes to research; *what should we do, in what order, does
   this merge* goes to orchestration; *broken and I cannot tell why* goes to
   whoever can run the experiment. An unrecognised kind still lands somewhere
   -- the table default -- and is flagged, never dropped.
2. **An escalation without the receipt and the log tail is refused.**
   ``open_escalation`` raises ``EscalationContractError`` rather than sending
   a round trip that will only come back asking for them.
3. **Every escalation is logged with its resolution.** Each one is evidence
   the decision table is incomplete, so the table grows from real cases. The
   escalated/autonomous ratio is the measurable signal of whether autonomy is
   improving.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from csd_autodev.decisions import ROUTES, load_table
from csd_autodev.store import _safe_id, new_id, state_root

_LOCK = threading.RLock()

# Question-shape hints -> route. Only used when `kind` is not already a route.
_KIND_ALIASES: dict[str, str] = {
    "research": "research",
    "literature": "research",
    "prior_art": "research",
    "is_this_true": "research",
    "review": "research",
    "orchestration": "orchestration",
    "sequencing": "orchestration",
    "merge": "orchestration",
    "plan": "orchestration",
    "what_next": "orchestration",
    "experiment": "experiment",
    "measurement": "experiment",
    "diagnosis": "experiment",
    "broken": "experiment",
    "cannot_tell_why": "experiment",
    "operator": "operator",
    "irreversible": "operator",
    "promotion": "operator",
    "visibility": "operator",
}

TERMINAL_STATUSES: frozenset[str] = frozenset({"resolved", "withdrawn"})


class EscalationContractError(ValueError):
    """Raised when an escalation would arrive without its evidence."""


def _dir(name: str) -> Path:
    path = state_root() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _counters_path() -> Path:
    return _dir("escalations") / "_counters.json"


def route_for(kind: str, *, table: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve an escalation kind to one route. Unknown kind -> table default."""
    tbl = table if table is not None else load_table()
    cfg = tbl.get("escalation") if isinstance(tbl.get("escalation"), dict) else {}
    routes = cfg.get("routes") if isinstance(cfg.get("routes"), dict) else {}
    default_route = str(cfg.get("default_route") or "orchestration")

    key = str(kind or "").strip().lower().replace("-", "_").replace(" ", "_")
    name = key if key in ROUTES else _KIND_ALIASES.get(key, "")
    known = bool(name)
    if not known:
        name = default_route
    entry = routes.get(name) if isinstance(routes.get(name), dict) else {}
    return {
        "route": name,
        "to": str(entry.get("to") or name),
        "known": known,
        "question_shape": str(entry.get("question_shape") or ""),
        "why": str(entry.get("why") or ""),
        "broadcast": bool(cfg.get("broadcast", False)),
    }


def _require_evidence(fields: dict[str, Any], *, table: dict[str, Any]) -> None:
    cfg = table.get("escalation") if isinstance(table.get("escalation"), dict) else {}
    required = cfg.get("required_context")
    names = (
        [str(n) for n in required]
        if isinstance(required, list) and required
        else ["receipt", "log_tail"]
    )
    missing = [n for n in names if not fields.get(n)]
    if missing:
        raise EscalationContractError(
            "escalation refused: missing "
            + ", ".join(sorted(missing))
            + " - an escalation without them only costs a round trip"
        )


def _tail(text: Any, *, lines: int) -> str:
    raw = text if isinstance(text, str) else json.dumps(text, default=str)
    kept = raw.splitlines()[-max(1, lines) :]
    return "\n".join(kept)


def open_escalation(fields: dict[str, Any] | None) -> dict[str, Any]:
    """Record one escalation. Refuses without receipt + log tail.

    ``fields``: ``kind`` (question shape or route), ``question``, ``receipt``
    (the run record / measurement it turns on), ``log_tail``, and optionally
    ``run_id``, ``stage``, ``gate``, ``decision``.
    """
    body = fields if isinstance(fields, dict) else {}
    table = load_table()
    _require_evidence(body, table=table)
    question = str(body.get("question") or "").strip()
    if not question:
        raise EscalationContractError("escalation refused: empty question")

    cfg = table.get("escalation") if isinstance(table.get("escalation"), dict) else {}
    try:
        tail_lines = int(cfg.get("log_tail_lines") or 80)
    except (TypeError, ValueError):
        tail_lines = 80

    routing = route_for(str(body.get("kind") or ""), table=table)
    esc_id = _safe_id(str(body.get("escalation_id") or "")) or new_id("esc")
    rec: dict[str, Any] = {
        "escalation_id": esc_id,
        "t": float(body.get("t") or time.time()),
        "status": "open",
        "kind": str(body.get("kind") or ""),
        "route": routing["route"],
        "to": routing["to"],
        "route_known": routing["known"],
        "question": question[:2000],
        "run_id": body.get("run_id"),
        "stage": body.get("stage"),
        "step": body.get("step"),
        "gate": body.get("gate"),
        "outcome": body.get("outcome"),
        "receipt": body.get("receipt"),
        "log_tail": _tail(body.get("log_tail"), lines=tail_lines),
        "decision": body.get("decision"),
        "table_revision": str(table.get("revision") or ""),
        "resolution": None,
        "action_taken": None,
        "table_amendment": None,
        "resolved_t": None,
    }
    with _LOCK:
        path = _dir("escalations") / f"{esc_id}.json"
        path.write_text(json.dumps(rec, indent=2, default=str) + "\n", encoding="utf-8")
        _bump("escalated", 1)
    return rec


def get_escalation(escalation_id: str) -> dict[str, Any]:
    safe = _safe_id(escalation_id)
    if not safe:
        return {"ok": False, "error": "not found", "escalation_id": escalation_id}
    path = _dir("escalations") / f"{safe}.json"
    if not path.is_file():
        return {"ok": False, "error": "not found", "escalation_id": escalation_id}
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"ok": False, "error": "unreadable", "escalation_id": escalation_id}
    return rec if isinstance(rec, dict) else {"ok": False, "error": "unreadable"}


def resolve_escalation(
    escalation_id: str, fields: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Close one escalation with what was decided and what changed.

    ``resolution`` is required: an escalation closed without one leaves no
    evidence for the table to grow from, which is the only reason to log them.
    """
    body = fields if isinstance(fields, dict) else {}
    resolution = str(body.get("resolution") or "").strip()
    if not resolution:
        raise EscalationContractError(
            "resolve refused: empty resolution - the resolution is the evidence"
        )
    with _LOCK:
        rec = get_escalation(escalation_id)
        if rec.get("error"):
            return None
        rec["status"] = str(body.get("status") or "resolved")
        rec["resolution"] = resolution[:2000]
        rec["action_taken"] = body.get("action_taken")
        rec["table_amendment"] = body.get("table_amendment")
        rec["resolved_t"] = float(body.get("resolved_t") or time.time())
        safe = _safe_id(str(rec.get("escalation_id") or escalation_id))
        if not safe:
            return None
        path = _dir("escalations") / f"{safe}.json"
        path.write_text(json.dumps(rec, indent=2, default=str) + "\n", encoding="utf-8")
    return rec


def list_escalations(*, status: str | None = None, limit: int = 200) -> dict[str, Any]:
    root = _dir("escalations")
    rows: list[dict[str, Any]] = []
    want = str(status or "").strip().lower()
    for path in sorted(root.glob("*.json"), key=lambda p: -p.stat().st_mtime):
        if path.name.startswith("_"):
            continue
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(rec, dict):
            continue
        if want and str(rec.get("status") or "").lower() != want:
            continue
        rows.append(
            {
                "escalation_id": rec.get("escalation_id") or path.stem,
                "t": rec.get("t"),
                "status": rec.get("status"),
                "kind": rec.get("kind"),
                "route": rec.get("route"),
                "to": rec.get("to"),
                "route_known": rec.get("route_known"),
                "run_id": rec.get("run_id"),
                "gate": rec.get("gate"),
                "outcome": rec.get("outcome"),
                "question": rec.get("question"),
                "resolution": rec.get("resolution"),
                "table_amendment": rec.get("table_amendment"),
            }
        )
        if len(rows) >= max(1, limit):
            break
    return {"ok": True, "escalations": rows}


def _read_counters() -> dict[str, int]:
    path = _counters_path()
    if not path.is_file():
        return {}
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(rec, dict):
        return {}
    return {str(k): int(v) for k, v in rec.items() if isinstance(v, (int, float))}


def _bump(name: str, delta: int) -> dict[str, int]:
    with _LOCK:
        counters = _read_counters()
        counters[name] = int(counters.get(name, 0)) + int(delta)
        _counters_path().write_text(
            json.dumps(counters, indent=2) + "\n", encoding="utf-8"
        )
        return counters


def record_autonomous(n: int = 1) -> dict[str, int]:
    """Count one action the loop took from the table without escalating."""
    return _bump("autonomous", max(0, int(n)))


def stats() -> dict[str, Any]:
    """Escalated-to-autonomous ratio, and how many escalations amended the table."""
    counters = _read_counters()
    rows = list_escalations(limit=1000)["escalations"]
    resolved = [r for r in rows if str(r.get("status") or "") in TERMINAL_STATUSES]
    amended = [r for r in resolved if r.get("table_amendment")]
    autonomous = int(counters.get("autonomous", 0))
    escalated = int(counters.get("escalated", len(rows)))
    total = autonomous + escalated
    return {
        "ok": True,
        "autonomous": autonomous,
        "escalated": escalated,
        "open": len(rows) - len(resolved),
        "resolved": len(resolved),
        "table_amendments": len(amended),
        "escalation_ratio": (escalated / total) if total else None,
        "unresolved_ratio": ((len(rows) - len(resolved)) / len(rows)) if rows else None,
    }
