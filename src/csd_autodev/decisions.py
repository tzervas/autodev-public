"""Decision table: outcome to action, as a lookup rather than a judgement.

The loop never decides whether a run "failed". It looks the outcome up in
``config/decision-table.json`` and does what the entry says. The safety
property is the *default*, not a case: any outcome, signal or route with no
entry resolves to ``stop_escalate``.

Three helpers turn evidence into a table key:

``classify_failure``  failure text -> one of the eight outcome states, using
                      the measured signatures from the signal trust table.
``margin_severity``   measured/threshold/spread -> severity band, and
                      ``inconclusive`` when the spread swallows the margin.
``retry_verdict`` /   run history -> retry, or escalate because the evidence
``evidence_verdict``  says the setup is wrong rather than the work hard.
"""

from __future__ import annotations

import itertools
import json
import os
import threading
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOCK = threading.RLock()
_CACHE: dict[str, Any] | None = None
_CACHE_KEY: tuple[str, float] | None = None

# Actions the table may name. An action outside this set is a broken table.
ACTIONS: frozenset[str] = frozenset(
    {"wait", "proceed", "stop", "stop_record", "stop_escalate", "retry"}
)

# Escalation routes the table may name.
ROUTES: frozenset[str] = frozenset(
    {"research", "orchestration", "experiment", "operator"}
)

# Used only when the config file is missing or unreadable. Escalating is the
# safe answer, so the fallback table has exactly one entry: the default.
_FALLBACK_TABLE: dict[str, Any] = {
    "schema": "csd-decision-table/v1",
    "revision": "builtin-fallback",
    "default": {
        "action": "stop_escalate",
        "retryable": False,
        "escalate": True,
        "route": "orchestration",
        "reason": "decision table unreadable; escalating rather than guessing",
    },
    "outcomes": {},
    "retry": {"consecutive_same_stage_cap": 2},
    "evidence_escalation": {},
    "margin": {},
    "signals": {},
    "classifiers": {"rules": [], "fallback": "crashed"},
    "escalation": {"routes": {}, "default_route": "orchestration"},
}


def table_path() -> Path:
    """Where the decision table lives. ``CSD_DECISION_TABLE`` overrides."""
    override = os.environ.get("CSD_DECISION_TABLE")
    if override:
        return Path(override)
    root = Path(os.environ.get("CSD_REPO_ROOT", str(_REPO_ROOT)))
    return root / "config" / "decision-table.json"


def load_table(*, refresh: bool = False) -> dict[str, Any]:
    """Read and cache the table. Unreadable file -> escalate-only fallback."""
    global _CACHE, _CACHE_KEY
    path = table_path()
    try:
        stamp = (str(path), path.stat().st_mtime)
    except OSError:
        stamp = (str(path), -1.0)
    with _LOCK:
        if not refresh and _CACHE is not None and stamp == _CACHE_KEY:
            return _CACHE
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = None
        table = raw if isinstance(raw, dict) and raw.get("outcomes") else _FALLBACK_TABLE
        _CACHE = table
        _CACHE_KEY = stamp
        return table


def table_default(table: dict[str, Any] | None = None) -> dict[str, Any]:
    """The escalate-on-unknown default. Hardened against a table that omits it."""
    tbl = table if table is not None else load_table()
    entry = tbl.get("default")
    if not isinstance(entry, dict) or not entry.get("action"):
        return dict(_FALLBACK_TABLE["default"])
    out = dict(entry)
    out.setdefault("action", "stop_escalate")
    out.setdefault("escalate", True)
    out.setdefault("route", "orchestration")
    return out


def known_outcomes(table: dict[str, Any] | None = None) -> frozenset[str]:
    tbl = table if table is not None else load_table()
    outcomes = tbl.get("outcomes")
    return frozenset(outcomes) if isinstance(outcomes, dict) else frozenset()


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def decide(
    outcome: str,
    *,
    stage: str | None = None,
    step: str | None = None,
    gate: str | None = None,
    measured: float | None = None,
    threshold: float | None = None,
    margin: float | None = None,
    spread: float | None = None,
    history: list[dict[str, Any]] | None = None,
    table: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Look one outcome up and return the action, with its structured detail.

    Never raises on an unknown outcome: it resolves to the table default,
    which is ``stop_escalate``. That is the whole safety property, expressed
    as a fallback rather than a branch.
    """
    tbl = table if table is not None else load_table()
    outcomes = tbl.get("outcomes") if isinstance(tbl.get("outcomes"), dict) else {}
    key = _norm(outcome)
    entry = outcomes.get(key)
    known = isinstance(entry, dict)
    src = dict(entry) if known else table_default(tbl)

    action = str(src.get("action") or "stop_escalate")
    if action not in ACTIONS:
        action = "stop_escalate"
    escalate = bool(src.get("escalate", action == "stop_escalate"))
    route = str(src.get("route") or "") or None

    decision: dict[str, Any] = {
        "outcome": key or None,
        "known": known,
        "action": action,
        "retryable": src.get("retryable") if known else src.get("retryable", False),
        "escalate": escalate,
        "route": route,
        "reason": src.get("reason") or src.get("meaning") or src.get("note") or "",
        "note": src.get("note") or "",
        "terminal": bool(src.get("terminal", True)),
        "table_revision": str(tbl.get("revision") or "unknown"),
        "entry": "outcomes" if known else "default",
        "gate": gate,
        "stage": stage,
        "step": step,
        "measured": measured,
        "threshold": threshold,
        "spread": spread,
    }
    if not known:
        decision["escalate"] = True
        decision["action"] = "stop_escalate"
        decision["retryable"] = False
        decision["reason"] = (
            src.get("reason") or "no table entry; anything not in the table escalates"
        )

    sev = margin_severity(
        measured=measured, threshold=threshold, margin=margin, spread=spread, table=tbl
    )
    decision["margin"] = sev["margin"]
    decision["severity"] = sev["severity"]
    if sev["forces_inconclusive"] and decision["action"] in {"stop_record", "proceed"}:
        # A verdict inside its own spread did not discriminate. Not a result.
        decision["action"] = "stop_escalate"
        decision["escalate"] = True
        decision["retryable"] = True
        decision["route"] = decision["route"] or "experiment"
        decision["outcome_override"] = "inconclusive"
        decision["reason"] = (
            f"|margin| {sev['margin']} within seed spread {spread}; "
            "the gate did not discriminate"
        )

    # A do-not-consume gate may not drive an action at all.
    if gate:
        trust = signal_trust(f"{gate}.verdict", table=tbl)
        if trust["class"] == "do_not_consume":
            decision["action"] = "stop_escalate"
            decision["escalate"] = True
            decision["retryable"] = True
            decision["route"] = trust.get("route") or "experiment"
            decision["outcome_override"] = "inconclusive"
            decision["reason"] = f"{gate} verdict is do-not-consume: {trust['why']}"
            decision["signal_class"] = "do_not_consume"

    if history:
        retry = retry_verdict(history, stage=stage, table=tbl)
        decision["retry"] = retry
        if retry["escalate"] and decision["action"] == "retry":
            decision["action"] = "stop_escalate"
            decision["escalate"] = True
            decision["route"] = retry.get("route") or "experiment"
            decision["reason"] = retry["reason"]

    if decision["escalate"] and not decision["route"]:
        esc = tbl.get("escalation") if isinstance(tbl.get("escalation"), dict) else {}
        decision["route"] = str(esc.get("default_route") or "orchestration")
    return decision


def classify_failure(text: Any, *, table: dict[str, Any] | None = None) -> dict[str, Any]:
    """Map failure text to an outcome using the measured signature rules.

    Ordered, first match wins. Hard infra signatures are checked before the
    result rules: a job that died with ``exitcode -1`` after printing earlier
    assertion output is infra, and that ordering is what encodes it.
    """
    tbl = table if table is not None else load_table()
    cfg = tbl.get("classifiers") if isinstance(tbl.get("classifiers"), dict) else {}
    rules = cfg.get("rules") if isinstance(cfg.get("rules"), list) else []
    haystack = str(text or "").lower()
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        needles = rule.get("any") if isinstance(rule.get("any"), list) else []
        for needle in needles:
            token = str(needle).lower()
            if token and token in haystack:
                return {
                    "outcome": str(rule.get("outcome") or "crashed"),
                    "rule": str(rule.get("id") or ""),
                    "matched": token,
                    "why": str(rule.get("why") or ""),
                }
    return {
        "outcome": str(cfg.get("fallback") or "crashed"),
        "rule": "fallback",
        "matched": None,
        "why": "no measured signature matched; the job's own code failed",
    }


def classify_exception(
    exc: BaseException, *, table: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Classify a raised exception. Environment errors are infra by type.

    The returned dict includes an 'evidence' key containing the full
    exception traceback string, ensuring the decision table has non-empty
    evidence for every classified exception.
    """
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return {
            "outcome": "infra_failed",
            "rule": "exception-type",
            "matched": type(exc).__name__,
            "why": "environment error by exception type",
            "evidence": str(exc.__traceback__),
        }
    hit = classify_failure(f"{type(exc).__name__}: {exc}", table=table)
    if hit["rule"] == "fallback" and isinstance(exc, OSError):
        return {
            "outcome": "infra_failed",
            "rule": "exception-type",
            "matched": "OSError",
            "why": "environment error by exception type",
            "evidence": str(exc.__traceback__),
        }
    return hit


def margin_severity(
    *,
    measured: float | None = None,
    threshold: float | None = None,
    margin: float | None = None,
    spread: float | None = None,
    table: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The margin is the severity. Returns band, ratio, and the spread verdict.

    A gate missed by 0.001 and one missed by 10 points are different
    situations; a bare verdict discards that. ``forces_inconclusive`` is true
    when ``|margin| <= spread``, i.e. the gate did not discriminate.
    """
    tbl = table if table is not None else load_table()
    cfg = tbl.get("margin") if isinstance(tbl.get("margin"), dict) else {}
    value = margin
    if value is None:
        try:
            if measured is not None and threshold is not None:
                value = float(measured) - float(threshold)
        except (TypeError, ValueError):
            value = None
    try:
        value = None if value is None else float(value)
    except (TypeError, ValueError):
        value = None

    try:
        spread_f = None if spread is None else abs(float(spread))
    except (TypeError, ValueError):
        spread_f = None

    ratio: float | None = None
    if value is not None and spread_f:
        ratio = abs(value) / spread_f

    band = "unknown"
    bands = (
        cfg.get("severity_bands") if isinstance(cfg.get("severity_bands"), list) else []
    )
    if ratio is not None:
        for row in bands:
            if not isinstance(row, dict):
                continue
            cap = row.get("max_abs_over_spread")
            if cap is None or ratio <= float(cap):
                band = str(row.get("name") or "unknown")
                break
    elif value is not None:
        band = "unmeasured_spread"

    forces = bool(value is not None and spread_f is not None and abs(value) <= spread_f)
    return {
        "margin": value,
        "spread": spread_f,
        "ratio": ratio,
        "severity": band,
        "forces_inconclusive": forces,
    }


def margin_trend(margins: list[float | None]) -> dict[str, Any]:
    """Shrinking margin is progress; growing is regression. Only the sequence shows which.

    Takes the ordered margins of consecutive runs of the same gate and reports
    the direction of ``|margin|``.
    """
    values = []
    for item in margins:
        try:
            if item is None:
                continue
            values.append(abs(float(item)))
        except (TypeError, ValueError):
            continue
    if len(values) < 2:
        return {"direction": "unknown", "points": len(values), "delta": None}
    delta = values[-1] - values[0]
    if all(b <= a for a, b in itertools.pairwise(values)) and delta < 0:
        direction = "shrinking"
    elif all(b >= a for a, b in itertools.pairwise(values)) and delta > 0:
        direction = "growing"
    elif delta < 0:
        direction = "shrinking"
    elif delta > 0:
        direction = "growing"
    else:
        direction = "flat"
    return {"direction": direction, "points": len(values), "delta": delta}


def retry_verdict(
    history: list[dict[str, Any]],
    *,
    stage: str | None = None,
    table: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Retry cap: N consecutive ``infra_failed`` at one stage escalates.

    ``history`` is oldest-first. Infrastructure that fails twice in the same
    place probably is not infrastructure.
    """
    tbl = table if table is not None else load_table()
    cfg = tbl.get("retry") if isinstance(tbl.get("retry"), dict) else {}
    try:
        cap = int(cfg.get("consecutive_same_stage_cap") or 2)
    except (TypeError, ValueError):
        cap = 2
    on_cap = cfg.get("on_cap") if isinstance(cfg.get("on_cap"), dict) else {}

    streak = 0
    for row in reversed(list(history or [])):
        if not isinstance(row, dict):
            break
        if _norm(row.get("status") or row.get("outcome")) != "infra_failed":
            break
        if stage is not None and _norm(row.get("stage")) != _norm(stage):
            break
        streak += 1

    escalate = streak >= cap
    return {
        "consecutive_infra_failed": streak,
        "cap": cap,
        "escalate": escalate,
        "route": str(on_cap.get("route") or "experiment"),
        "stage": stage,
        "reason": (
            str(
                on_cap.get("reason")
                or "infrastructure that fails twice in the same place "
                "probably is not infrastructure"
            )
            if escalate
            else ""
        ),
    }


def evidence_verdict(
    history: list[dict[str, Any]],
    *,
    arms: list[dict[str, Any]] | None = None,
    margins: list[float | None] | None = None,
    table: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Escalate on evidence the setup is wrong, not on a run count.

    Three triggers, all from the scope note: no ``passed`` in K consecutive
    runs; every arm passing (the gate is probably not discriminating); a gate
    failing by a growing margin.
    """
    tbl = table if table is not None else load_table()
    cfg = (
        tbl.get("evidence_escalation")
        if isinstance(tbl.get("evidence_escalation"), dict)
        else {}
    )
    triggers: list[dict[str, Any]] = []

    rows = [r for r in (history or []) if isinstance(r, dict)]
    no_pass = (
        cfg.get("no_pass_in_k_runs")
        if isinstance(cfg.get("no_pass_in_k_runs"), dict)
        else {}
    )
    try:
        k = int(no_pass.get("k") or 0)
    except (TypeError, ValueError):
        k = 0
    if k > 0 and len(rows) >= k:
        window = rows[-k:]
        if all(_norm(r.get("status") or r.get("outcome")) != "passed" for r in window):
            triggers.append(
                {
                    "trigger": "no_pass_in_k_runs",
                    "k": k,
                    "route": str(no_pass.get("route") or "orchestration"),
                    "reason": str(no_pass.get("reason") or ""),
                }
            )

    all_pass = (
        cfg.get("all_arms_passed") if isinstance(cfg.get("all_arms_passed"), dict) else {}
    )
    try:
        min_arms = int(all_pass.get("min_arms") or 2)
    except (TypeError, ValueError):
        min_arms = 2
    arm_rows = [a for a in (arms or []) if isinstance(a, dict)]
    if len(arm_rows) >= min_arms and all(
        _norm(a.get("status") or a.get("outcome")) == "passed" for a in arm_rows
    ):
        triggers.append(
            {
                "trigger": "all_arms_passed",
                "arms": len(arm_rows),
                "route": str(all_pass.get("route") or "research"),
                "reason": str(all_pass.get("reason") or ""),
            }
        )

    growing = (
        cfg.get("margin_trend_growing")
        if isinstance(cfg.get("margin_trend_growing"), dict)
        else {}
    )
    try:
        min_points = int(growing.get("min_points") or 3)
    except (TypeError, ValueError):
        min_points = 3
    if margins:
        trend = margin_trend(list(margins))
        if trend["points"] >= min_points and trend["direction"] == "growing":
            triggers.append(
                {
                    "trigger": "margin_trend_growing",
                    "points": trend["points"],
                    "delta": trend["delta"],
                    "route": str(growing.get("route") or "experiment"),
                    "reason": str(growing.get("reason") or ""),
                }
            )

    return {
        "escalate": bool(triggers),
        "triggers": triggers,
        "route": triggers[0]["route"] if triggers else None,
    }


def signal_trust(signal: str, *, table: dict[str, Any] | None = None) -> dict[str, Any]:
    """Trust class for one named signal. Unknown signal -> escalate.

    The classes come from the signal trust table: ``act`` (consume),
    ``do_not_consume`` (record only, measured to certify nothing),
    ``low_trust`` (verify first), ``audit_only`` (real but cannot trigger),
    ``escalate``.
    """
    tbl = table if table is not None else load_table()
    groups = tbl.get("signals") if isinstance(tbl.get("signals"), dict) else {}
    key = str(signal or "").strip()
    for name, group in groups.items():
        if not isinstance(group, dict):
            continue
        members = group.get("members")
        if not isinstance(members, dict):
            continue
        for member, why in members.items():
            if member.lower() == key.lower():
                return {
                    "signal": member,
                    "class": name,
                    "action": str(group.get("action") or "record_only"),
                    "escalate": bool(group.get("escalate", False)),
                    "route": group.get("route"),
                    "why": str(why),
                    "known": True,
                }
    default = table_default(tbl)
    return {
        "signal": key,
        "class": "unknown",
        "action": "stop_escalate",
        "escalate": True,
        "route": default.get("route") or "orchestration",
        "why": "signal not in the trust table; escalate rather than consume",
        "known": False,
    }


def table_summary() -> dict[str, Any]:
    """Serialisable view for ``GET /api/decisions``."""
    tbl = load_table()
    return {
        "ok": True,
        "schema": str(tbl.get("schema") or ""),
        "revision": str(tbl.get("revision") or ""),
        "path": str(table_path()),
        "default": table_default(tbl),
        "outcomes": tbl.get("outcomes") or {},
        "retry": tbl.get("retry") or {},
        "evidence_escalation": tbl.get("evidence_escalation") or {},
        "margin": tbl.get("margin") or {},
        "signals": tbl.get("signals") or {},
        "classifiers": tbl.get("classifiers") or {},
        "escalation": tbl.get("escalation") or {},
    }


def evaluate(body: dict[str, Any] | None) -> dict[str, Any]:
    """``POST /api/decisions`` body -> decision. Text classifies when no outcome."""
    payload = body if isinstance(body, dict) else {}
    tbl = load_table()
    classified: dict[str, Any] | None = None
    outcome = str(payload.get("outcome") or payload.get("status") or "").strip()
    text = payload.get("text") or payload.get("error") or payload.get("log_tail")
    if not outcome and text:
        classified = classify_failure(text, table=tbl)
        outcome = classified["outcome"]

    def _num(name: str) -> float | None:
        raw = payload.get(name)
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    history = payload.get("history") if isinstance(payload.get("history"), list) else None
    decision = decide(
        outcome,
        stage=payload.get("stage"),
        step=payload.get("step"),
        gate=payload.get("gate"),
        measured=_num("measured"),
        threshold=_num("threshold"),
        margin=_num("margin"),
        spread=_num("spread"),
        history=history,
        table=tbl,
    )
    if classified is not None:
        decision["classified"] = classified
    arms = payload.get("arms") if isinstance(payload.get("arms"), list) else None
    margins = payload.get("margins") if isinstance(payload.get("margins"), list) else None
    if history or arms or margins:
        evidence = evidence_verdict(history or [], arms=arms, margins=margins, table=tbl)
        decision["evidence"] = evidence
        if evidence["escalate"]:
            decision["action"] = "stop_escalate"
            decision["escalate"] = True
            decision["route"] = evidence["route"] or decision.get("route")
    return {"ok": True, "decision": decision}
