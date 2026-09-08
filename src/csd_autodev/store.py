"""File-backed harness resource store: runs, agents, leases.

Run identity is a stable server key (run_id) with optional checkpoint_sha256
when known (R14-A2 later). Sessions stay on the existing lab session paths;
this store does not own them.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_LOCK = threading.RLock()


def state_root() -> Path:
    return Path(os.environ.get("CSD_HARNESS_STATE", "/fleet-data/cabal/harness-state"))


def safe_id(raw: str) -> str | None:
    text = str(raw or "").strip()
    if not text or not _ID_RE.match(text):
        return None
    if ".." in text or "/" in text or "\\" in text:
        return None
    return text


# Back-compat alias for in-package callers.
_safe_id = safe_id


def _kind_dir(kind: str) -> Path:
    path = state_root() / kind
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read(kind: str, ident: str) -> dict[str, Any] | None:
    safe = _safe_id(ident)
    if not safe:
        return None
    path = _kind_dir(kind) / f"{safe}.json"
    if not path.is_file():
        return None
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return rec if isinstance(rec, dict) else None


def _write(kind: str, ident: str, rec: dict[str, Any]) -> dict[str, Any]:
    safe = _safe_id(ident)
    if not safe:
        raise ValueError("bad id")
    path = _kind_dir(kind) / f"{safe}.json"
    path.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    return rec


def _list(kind: str, *, id_field: str) -> list[dict[str, Any]]:
    root = _kind_dir(kind)
    items: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), key=lambda p: -p.stat().st_mtime):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(rec, dict):
            continue
        items.append(
            {
                id_field: rec.get(id_field) or path.stem,
                "t": rec.get("t") or path.stat().st_mtime,
                "status": rec.get("status"),
                "checkpoint_sha256": rec.get("checkpoint_sha256"),
                "goal": rec.get("goal"),
                "retryable": rec.get("retryable"),
                "summary": {
                    k: rec.get(k)
                    for k in (
                        "goal",
                        "status",
                        "ok",
                        "host",
                        "gpu",
                        "gate",
                        "margin",
                        "stage",
                        "retryable",
                    )
                    if k in rec
                },
            }
        )
    return items[:500]


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def list_runs() -> dict[str, Any]:
    with _LOCK:
        return {"runs": _list("runs", id_field="run_id")}


def get_run(run_id: str) -> dict[str, Any]:
    with _LOCK:
        rec = _read("runs", run_id)
        if rec is None:
            return {"ok": False, "error": "not found", "run_id": run_id}
        return rec


# Closed outcome set. The old {running, succeeded, failed, halted} conflated
# outcomes that demand opposite responses; each name below maps to exactly one
# action in config/decision-table.json. Aliases succeeded→passed and
# failed→crashed are accepted on the wire but never stored.
RUN_STATUSES = frozenset(
    {
        "running",
        "passed",
        "rejected",
        "inconclusive",
        "crashed",
        "infra_failed",
        "refused",
        "halted",
    }
)

RUN_STATUS_ALIASES = {
    "succeeded": "passed",
    "failed": "crashed",
}

# retryable defaults: inconclusive/infra_failed/halted yes; rejected/refused no;
# crashed maybe (null); running/passed unset.
_RETRYABLE_DEFAULT: dict[str, bool | None] = {
    "rejected": False,
    "inconclusive": True,
    "crashed": None,
    "infra_failed": True,
    "refused": False,
    "halted": True,
}

# ok defaults when omitted on a status change. refused is NOT ok, but it is
# also not a failure — the decision table, not this flag, carries that.
_OK_DEFAULT: dict[str, bool | None] = {
    "running": None,
    "passed": True,
    "rejected": False,
    "inconclusive": False,
    "crashed": False,
    "infra_failed": False,
    "refused": False,
    "halted": None,
}

# Fields create/update may set. Never includes run_id. The detail keys are
# what make an outcome structured rather than a bare label: the gate by
# number, the measured value against its threshold, the margin, and where.
_RUN_UPDATE_KEYS = frozenset(
    {
        "status",
        "ok",
        "goal",
        "checkpoint_sha256",
        "gate",
        "measured",
        "threshold",
        "margin",
        "spread",
        "stage",
        "step",
        "retryable",
        "outcome_detail",
    }
)


def normalize_run_status(raw: str) -> str | None:
    """Map alias → canonical status. None if unknown."""
    text = str(raw or "").strip()
    if not text:
        return None
    if text in RUN_STATUS_ALIASES:
        return RUN_STATUS_ALIASES[text]
    if text in RUN_STATUSES:
        return text
    return None


def _apply_status_fields(rec: dict[str, Any], fields: dict[str, Any]) -> None:
    """Merge allowed keys; normalize status; derive ok/retryable when omitted."""
    patch = {k: fields[k] for k in _RUN_UPDATE_KEYS if k in fields}
    if "status" in patch:
        canonical = normalize_run_status(str(patch["status"]))
        if canonical is None:
            raise ValueError(f"unknown status: {patch['status']}")
        patch["status"] = canonical
        if "ok" not in patch:
            patch["ok"] = _OK_DEFAULT.get(canonical)
        if "retryable" not in patch and canonical in _RETRYABLE_DEFAULT:
            patch["retryable"] = _RETRYABLE_DEFAULT[canonical]
    for key, val in patch.items():
        rec[key] = val


def create_run(fields: dict[str, Any]) -> dict[str, Any]:
    """Create a run record. Stable run_id; optional checkpoint_sha256."""
    with _LOCK:
        run_id = _safe_id(str(fields.get("run_id") or "")) or new_id("run")
        status_raw = fields.get("status")
        if status_raw is None or str(status_raw).strip() == "":
            status = "recorded"
        else:
            status = normalize_run_status(str(status_raw)) or str(status_raw)
        rec: dict[str, Any] = {
            "run_id": run_id,
            "t": float(fields.get("t") or time.time()),
            "status": status,
            "goal": fields.get("goal"),
            "ok": fields.get("ok"),
            "checkpoint_sha256": fields.get("checkpoint_sha256"),
            "source": str(fields.get("source") or "api"),
        }
        for key in (
            "gate",
            "measured",
            "threshold",
            "margin",
            "spread",
            "stage",
            "step",
            "retryable",
            "outcome_detail",
        ):
            if key in fields and fields[key] is not None:
                rec[key] = fields[key]
        for key in ("repo", "sha", "number", "head", "out", "error"):
            if key in fields and fields[key] is not None:
                rec[key] = fields[key]
        return _write("runs", run_id, rec)


def update_run(run_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    """Merge allowed keys into an existing run. None if missing. Preserves run_id.

    Raises ValueError on unknown status (after alias mapping).
    """
    with _LOCK:
        rec = _read("runs", run_id)
        if rec is None:
            return None
        ident = str(rec.get("run_id") or run_id)
        _apply_status_fields(rec, fields)
        rec["run_id"] = ident
        return _write("runs", ident, rec)


def list_agents() -> dict[str, Any]:
    with _LOCK:
        return {"agents": _list("agents", id_field="agent_id")}


def get_agent(agent_id: str) -> dict[str, Any]:
    with _LOCK:
        rec = _read("agents", agent_id)
        if rec is None:
            return {"ok": False, "error": "not found", "agent_id": agent_id}
        return rec


def create_agent(fields: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        agent_id = _safe_id(str(fields.get("agent_id") or "")) or new_id("agent")
        rec = {
            "agent_id": agent_id,
            "t": float(fields.get("t") or time.time()),
            "role": fields.get("role"),
            "model_ref": fields.get("model_ref"),
            "host": fields.get("host"),
            "gpu": fields.get("gpu"),
            "vram_mib": fields.get("vram_mib"),
            "ram_mib": fields.get("ram_mib"),
            "ctx_tokens": fields.get("ctx_tokens"),
            "lease_id": fields.get("lease_id"),
            "status": str(fields.get("status") or "recorded"),
        }
        return _write("agents", agent_id, rec)


def list_leases() -> dict[str, Any]:
    with _LOCK:
        return {"leases": _list("leases", id_field="lease_id")}


def get_lease(lease_id: str) -> dict[str, Any]:
    with _LOCK:
        rec = _read("leases", lease_id)
        if rec is None:
            return {"ok": False, "error": "not found", "lease_id": lease_id}
        return rec


def create_lease(fields: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        lease_id = _safe_id(str(fields.get("lease_id") or "")) or new_id("lease")
        rec = {
            "lease_id": lease_id,
            "t": float(fields.get("t") or time.time()),
            "host": fields.get("host"),
            "gpu": fields.get("gpu"),
            "vram_mib": fields.get("vram_mib"),
            "ram_mib": fields.get("ram_mib"),
            "ctx_tokens": fields.get("ctx_tokens"),
            "status": str(fields.get("status") or "active"),
        }
        return _write("leases", lease_id, rec)
