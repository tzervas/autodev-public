"""G5: the decision table is a lookup, and the unknown case escalates.

The acceptance cases are today's real diagnoses, each with a known correct
action:

* podman socket ``context deadline exceeded`` -> ``infra_failed``, retry
* ``assert 1 == 0`` with a named test    -> ``rejected``, do not retry
* a guard refusing a bad budget          -> ``refused``, stop, NOT a failure
* a gate whose spread exceeds its margin -> ``inconclusive``, escalate

Plus the property the whole design rests on, verified by constructing the
failure: an outcome, signal or table that the loop has never seen routes to
``stop_escalate`` rather than to a guess.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.syspath_prepend(str(SRC))
    monkeypatch.setenv("CSD_HARNESS_STATE", str(tmp_path / "harness-state"))
    monkeypatch.setenv("CSD_REPO_ROOT", str(ROOT))
    monkeypatch.delenv("CSD_DECISION_TABLE", raising=False)


def _load_lab_console(name: str):
    script = ROOT / "scripts" / "csd-lab-console"
    loader = importlib.machinery.SourceFileLoader(name, str(script))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# The table itself: closed, complete, and agreeing with the run status enum.
# --------------------------------------------------------------------------


def test_table_covers_every_run_status_and_nothing_else() -> None:
    """The eight outcome states and the table's keys are the same closed set."""
    from csd_autodev import decisions, store

    assert decisions.known_outcomes() == store.RUN_STATUSES
    assert {
        "running",
        "passed",
        "rejected",
        "inconclusive",
        "crashed",
        "infra_failed",
        "refused",
        "halted",
    } == store.RUN_STATUSES


def test_every_entry_names_a_valid_action_and_route() -> None:
    """A table entry outside the declared action/route sets is a broken table."""
    from csd_autodev import decisions

    table = decisions.load_table()
    for name, entry in table["outcomes"].items():
        assert entry["action"] in decisions.ACTIONS, name
        route = entry.get("route")
        if route:
            assert route in decisions.ROUTES, name
    assert table["default"]["action"] in decisions.ACTIONS
    assert table["default"]["route"] in decisions.ROUTES
    esc = table["escalation"]["routes"]
    assert set(esc) <= decisions.ROUTES
    assert table["escalation"]["broadcast"] is False


def test_each_state_maps_to_exactly_one_action() -> None:
    """One action per state, with no inference left for the loop."""
    from csd_autodev import decisions

    want = {
        "running": ("wait", False),
        "passed": ("proceed", False),
        "rejected": ("stop_record", False),
        "inconclusive": ("stop_escalate", True),
        "crashed": ("stop_escalate", True),
        "infra_failed": ("retry", False),
        "refused": ("stop_record", False),
        "halted": ("stop", False),
    }
    for state, (action, escalates) in want.items():
        d = decisions.decide(state)
        assert d["action"] == action, (state, d)
        assert d["escalate"] is escalates, (state, d)
        assert d["known"] is True


def test_retryable_is_carried_per_state() -> None:
    """retryable is part of the answer, not something the caller infers."""
    from csd_autodev import decisions

    assert decisions.decide("rejected")["retryable"] is False
    assert decisions.decide("refused")["retryable"] is False
    assert decisions.decide("infra_failed")["retryable"] is True
    assert decisions.decide("inconclusive")["retryable"] is True
    assert decisions.decide("halted")["retryable"] is True
    # crashed is "maybe" - null, never silently coerced to a boolean.
    assert decisions.decide("crashed")["retryable"] is None


# --------------------------------------------------------------------------
# The safety property, constructed as a failure rather than asserted.
# --------------------------------------------------------------------------


def test_unknown_outcome_routes_to_escalate() -> None:
    """An outcome the loop has never seen stops and escalates. No guess."""
    from csd_autodev import decisions

    for invented in ("weird_new_state", "SUCCEEDED_MAYBE", "", "flaky"):
        d = decisions.decide(invented)
        assert d["known"] is False, invented
        assert d["action"] == "stop_escalate", invented
        assert d["escalate"] is True, invented
        assert d["retryable"] is False, invented
        assert d["route"] in decisions.ROUTES, invented
        assert d["entry"] == "default"


def test_unknown_outcome_still_escalates_when_the_table_is_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Delete the table: the loop escalates rather than proceeding blind."""
    from csd_autodev import decisions

    monkeypatch.setenv("CSD_DECISION_TABLE", str(tmp_path / "does-not-exist.json"))
    decisions.load_table(refresh=True)
    for state in ("passed", "infra_failed", "anything"):
        d = decisions.decide(state)
        assert d["action"] == "stop_escalate", state
        assert d["escalate"] is True, state


def test_a_table_whose_default_is_missing_still_escalates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Strip the default out of the table: the built-in default takes over."""
    from csd_autodev import decisions

    broken = tmp_path / "no-default.json"
    broken.write_text(
        json.dumps({"outcomes": {"passed": {"action": "proceed"}}}), encoding="utf-8"
    )
    monkeypatch.setenv("CSD_DECISION_TABLE", str(broken))
    decisions.load_table(refresh=True)
    d = decisions.decide("mystery")
    assert d["action"] == "stop_escalate"
    assert d["escalate"] is True


def test_an_entry_with_a_nonsense_action_degrades_to_escalate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A table that says 'yolo' does not get to invent an action."""
    from csd_autodev import decisions

    broken = tmp_path / "bad-action.json"
    broken.write_text(
        json.dumps(
            {
                "default": {"action": "stop_escalate", "escalate": True},
                "outcomes": {"passed": {"action": "yolo"}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CSD_DECISION_TABLE", str(broken))
    decisions.load_table(refresh=True)
    assert decisions.decide("passed")["action"] == "stop_escalate"


def test_unknown_signal_escalates() -> None:
    """A signal with no trust-table row may not drive an action."""
    from csd_autodev import decisions

    hit = decisions.signal_trust("some_new_metric")
    assert hit["known"] is False
    assert hit["escalate"] is True
    assert hit["action"] == "stop_escalate"


# --------------------------------------------------------------------------
# Acceptance case 1 - podman deadline -> infra_failed, retry.
# --------------------------------------------------------------------------


PODMAN_TAIL = """
Run bash scripts/gate
time="2026-09-07T11:02:14Z" level=error msg="Error: unable to connect to Podman socket:
Get \"http://d/v5.0.0/libpod/_ping\": context deadline exceeded"
##[error]Process completed with exit code 1.
"""


def test_podman_deadline_is_infra_failed_and_retries() -> None:
    from csd_autodev import decisions

    hit = decisions.classify_failure(PODMAN_TAIL)
    assert hit["outcome"] == "infra_failed"
    assert hit["rule"] == "infra-hard"

    d = decisions.decide(hit["outcome"], stage="gate")
    assert d["action"] == "retry"
    assert d["retryable"] is True
    assert d["escalate"] is False


def test_exitcode_minus_one_is_infra_even_after_earlier_assert_text() -> None:
    """MEASURED: a conventions job died with exitcode -1 AFTER cz check passed.

    Hard infra signatures are checked before the result rules, so a log that
    also carries assertion words is still classified as infrastructure.
    """
    from csd_autodev import decisions

    text = "cz check ... success\nassert ok\n##[error]The runner exitcode -1"
    hit = decisions.classify_failure(text)
    assert hit["outcome"] == "infra_failed"
    assert hit["matched"] == "exitcode -1"


# --------------------------------------------------------------------------
# Acceptance case 2 - a named failing test -> rejected, do not retry.
# --------------------------------------------------------------------------


PYTEST_TAIL = """
=================================== FAILURES ===================================
______________________ test_gate_refuses_over_budget ___________________________
    def test_gate_refuses_over_budget():
>       assert 1 == 0
E       assert 1 == 0
tests/test_guard.py:12: AssertionError
=========================== short test summary info ============================
FAILED tests/test_guard.py::test_gate_refuses_over_budget - assert 1 == 0
"""


def test_a_superseded_ci_run_is_infra_not_a_result() -> None:
    """MEASURED 2026-09-07 on this very PR: a concurrency cancel-in-progress
    files "Has been cancelled" as a CI *failure* on the superseded sha. It is
    not a result about the code, so it must not stop the loop as one.
    """
    from csd_autodev import decisions

    hit = decisions.classify_failure("CI / Repo gate (pull_request): Has been cancelled")
    assert hit["outcome"] == "infra_failed"
    assert decisions.decide(hit["outcome"], stage="gate")["action"] == "retry"

    trust = decisions.signal_trust("ci_status_stale_after_retrigger")
    assert trust["class"] == "low_trust"
    assert trust["action"] == "verify_then_act"


def test_named_failing_test_is_rejected_and_not_retried() -> None:
    from csd_autodev import decisions

    hit = decisions.classify_failure(PYTEST_TAIL)
    assert hit["outcome"] == "rejected"
    assert hit["rule"] == "assertion-or-gate-miss"

    d = decisions.decide("rejected", gate="pytest", stage="gate")
    assert d["action"] == "stop_record"
    assert d["retryable"] is False
    assert d["escalate"] is False


# --------------------------------------------------------------------------
# Acceptance case 3 - a guard refusing a bad budget -> refused, never failure.
# --------------------------------------------------------------------------


REFUSAL_TAIL = (
    "W7p admission: refusing to start reason on gpu0 - "
    "budget exceeds free VRAM at launch (requested 21504 MiB, free 18233 MiB)"
)


def test_guard_refusal_is_refused_and_never_filed_as_failure() -> None:
    from csd_autodev import decisions

    hit = decisions.classify_failure(REFUSAL_TAIL)
    assert hit["outcome"] == "refused"
    assert hit["rule"] == "guard-refusal"

    d = decisions.decide("refused", stage="admission")
    # The guard working must not read as breakage: stop and record, no retry,
    # no escalation, and never the crashed/infra path.
    assert d["action"] == "stop_record"
    assert d["retryable"] is False
    assert d["escalate"] is False
    assert d["action"] != decisions.decide("crashed")["action"]
    assert "guard working" in d["reason"]
    assert "NEVER file under failure" in d["note"]


def test_refused_is_not_reachable_from_the_crashed_fallback() -> None:
    """Only the guard rule may produce refused; the fallback is crashed."""
    from csd_autodev import decisions

    hit = decisions.classify_failure("Traceback (most recent call last): ZeroDiv")
    assert hit["outcome"] == "crashed"
    assert hit["rule"] == "fallback"


# --------------------------------------------------------------------------
# Acceptance case 4 - spread exceeds margin -> inconclusive.
# --------------------------------------------------------------------------


def test_gate_whose_spread_exceeds_its_margin_is_inconclusive() -> None:
    """G35-shaped: a 0.00-10.94 point seed spread against a 5.00 ceiling."""
    from csd_autodev import decisions

    d = decisions.decide(
        "rejected", gate="G35", measured=4.2, threshold=5.0, spread=10.94, stage="s1"
    )
    assert d["outcome_override"] == "inconclusive"
    assert d["action"] == "stop_escalate"
    assert d["escalate"] is True
    assert d["retryable"] is True
    assert d["severity"] == "marginal"


def test_a_pass_inside_its_own_spread_is_also_inconclusive() -> None:
    """A verdict that did not discriminate is not a result, whichever way it fell."""
    from csd_autodev import decisions

    d = decisions.decide("passed", measured=5.4, threshold=5.0, spread=10.94)
    assert d["outcome_override"] == "inconclusive"
    assert d["escalate"] is True


def test_margin_is_the_severity_not_the_verdict() -> None:
    """0.001 and 10 points are different situations; a bare verdict loses that."""
    from csd_autodev import decisions

    near = decisions.margin_severity(measured=0.499, threshold=0.5, spread=0.0005)
    far = decisions.margin_severity(measured=-9.5, threshold=0.5, spread=0.0005)
    assert near["margin"] == pytest.approx(-0.001)
    assert far["margin"] == pytest.approx(-10.0)
    assert near["severity"] == "narrow"
    assert far["severity"] == "wide"
    assert far["ratio"] > near["ratio"]
    assert near["forces_inconclusive"] is False


def test_shrinking_margin_reads_as_progress_not_another_fail() -> None:
    from csd_autodev import decisions

    assert decisions.margin_trend([-0.9, -0.5, -0.2])["direction"] == "shrinking"
    assert decisions.margin_trend([-0.2, -0.5, -0.9])["direction"] == "growing"
    assert decisions.margin_trend([-0.5, -0.5])["direction"] == "flat"


# --------------------------------------------------------------------------
# Signals measured to certify nothing may not drive an action.
# --------------------------------------------------------------------------


def test_do_not_consume_gate_cannot_drive_a_decision() -> None:
    """G29 passes on a store whose MI is exactly zero. A pass certifies nothing."""
    from csd_autodev import decisions

    trust = decisions.signal_trust("G29.verdict")
    assert trust["class"] == "do_not_consume"
    assert trust["escalate"] is True

    d = decisions.decide("passed", gate="G29", stage="store")
    assert d["signal_class"] == "do_not_consume"
    assert d["action"] == "stop_escalate"
    assert d["outcome_override"] == "inconclusive"


def test_measured_infra_signature_is_in_the_act_class() -> None:
    from csd_autodev import decisions

    trust = decisions.signal_trust("runner.exitcode_minus_one")
    assert trust["class"] == "act"
    assert trust["escalate"] is False
    assert "MEASURED" in trust["why"]


# --------------------------------------------------------------------------
# Retry caps and evidence-based escalation.
# --------------------------------------------------------------------------


def test_two_consecutive_infra_failed_at_one_stage_escalates() -> None:
    from csd_autodev import decisions

    one = [{"status": "infra_failed", "stage": "gate"}]
    two = one * 2
    assert decisions.retry_verdict(one, stage="gate")["escalate"] is False
    verdict = decisions.retry_verdict(two, stage="gate")
    assert verdict["escalate"] is True
    assert verdict["consecutive_infra_failed"] == 2
    assert "not infrastructure" in verdict["reason"]

    d = decisions.decide("infra_failed", stage="gate", history=two)
    assert d["action"] == "stop_escalate"
    assert d["escalate"] is True


def test_the_streak_is_per_stage_and_must_be_consecutive() -> None:
    from csd_autodev import decisions

    mixed = [
        {"status": "infra_failed", "stage": "gate"},
        {"status": "passed", "stage": "gate"},
        {"status": "infra_failed", "stage": "gate"},
    ]
    assert decisions.retry_verdict(mixed, stage="gate")["consecutive_infra_failed"] == 1

    other_stage = [
        {"status": "infra_failed", "stage": "quant"},
        {"status": "infra_failed", "stage": "gate"},
    ]
    assert (
        decisions.retry_verdict(other_stage, stage="gate")["consecutive_infra_failed"]
        == 1
    )


def test_no_pass_in_k_runs_escalates_on_evidence_not_a_count() -> None:
    from csd_autodev import decisions

    history = [{"status": "rejected"}] * 5
    verdict = decisions.evidence_verdict(history)
    assert verdict["escalate"] is True
    assert verdict["triggers"][0]["trigger"] == "no_pass_in_k_runs"

    with_a_pass = [{"status": "rejected"}] * 4 + [{"status": "passed"}]
    assert decisions.evidence_verdict(with_a_pass)["escalate"] is False


def test_every_arm_passing_escalates_because_the_gate_is_not_discriminating() -> None:
    from csd_autodev import decisions

    arms = [
        {"arm": "control", "status": "passed"},
        {"arm": "treated", "status": "passed"},
    ]
    verdict = decisions.evidence_verdict([], arms=arms)
    assert verdict["escalate"] is True
    assert verdict["triggers"][0]["trigger"] == "all_arms_passed"
    assert verdict["route"] == "research"

    split = [{"status": "passed"}, {"status": "rejected"}]
    assert decisions.evidence_verdict([], arms=split)["escalate"] is False


def test_growing_margin_escalates_and_shrinking_does_not() -> None:
    from csd_autodev import decisions

    growing = decisions.evidence_verdict([], margins=[-0.1, -0.4, -0.9])
    assert growing["escalate"] is True
    assert growing["triggers"][0]["trigger"] == "margin_trend_growing"
    assert decisions.evidence_verdict([], margins=[-0.9, -0.4, -0.1])["escalate"] is False


# --------------------------------------------------------------------------
# Escalation contract.
# --------------------------------------------------------------------------


def test_escalation_routes_by_kind_and_never_broadcasts() -> None:
    from csd_autodev import escalations

    assert escalations.route_for("research")["to"] == "grok"
    assert escalations.route_for("literature")["to"] == "grok"
    assert escalations.route_for("orchestration")["to"] == "claude"
    assert escalations.route_for("merge")["to"] == "claude"
    assert escalations.route_for("experiment")["to"] == "runner"
    assert escalations.route_for("broken")["to"] == "runner"
    assert escalations.route_for("research")["broadcast"] is False


def test_unknown_escalation_kind_lands_on_the_default_route_and_is_flagged() -> None:
    """An unrecognised kind is never dropped; it goes to the default and says so."""
    from csd_autodev import escalations

    hit = escalations.route_for("vibes")
    assert hit["known"] is False
    assert hit["route"] == "orchestration"


def _evidence() -> dict[str, Any]:
    return {
        "kind": "experiment",
        "question": "reason eval crashed after the quant step; cannot tell why",
        "receipt": {
            "run_id": "run-1",
            "gate": "G37",
            "measured": 0.41,
            "threshold": 0.5,
        },
        "log_tail": "line1\nline2\nTraceback...\nValueError: bad shape",
    }


def test_escalation_without_receipt_or_log_tail_is_refused() -> None:
    """An escalation that arrives without them only costs a round trip."""
    from csd_autodev import escalations

    for missing in ("receipt", "log_tail"):
        body = _evidence()
        body.pop(missing)
        with pytest.raises(escalations.EscalationContractError) as exc:
            escalations.open_escalation(body)
        assert missing in str(exc.value)

    with pytest.raises(escalations.EscalationContractError):
        escalations.open_escalation({**_evidence(), "question": "  "})


def test_escalation_is_logged_with_its_resolution() -> None:
    from csd_autodev import escalations

    rec = escalations.open_escalation(_evidence())
    assert rec["status"] == "open"
    assert rec["route"] == "experiment"
    assert rec["to"] == "runner"
    assert rec["log_tail"].endswith("ValueError: bad shape")

    assert escalations.list_escalations(status="open")["escalations"]
    with pytest.raises(escalations.EscalationContractError):
        escalations.resolve_escalation(rec["escalation_id"], {"resolution": ""})

    closed = escalations.resolve_escalation(
        rec["escalation_id"],
        {
            "resolution": "quant changed the base hash; latents needed re-keying",
            "action_taken": "re-key then re-run",
            "table_amendment": "add quant-migration rule to classifiers",
        },
    )
    assert closed is not None
    assert closed["status"] == "resolved"
    assert closed["table_amendment"]
    assert not escalations.list_escalations(status="open")["escalations"]


def test_escalation_ratio_is_the_measurable_signal() -> None:
    from csd_autodev import escalations

    escalations.record_autonomous(9)
    escalations.open_escalation(_evidence())
    stats = escalations.stats()
    assert stats["autonomous"] == 9
    assert stats["escalated"] == 1
    assert stats["escalation_ratio"] == pytest.approx(0.1)
    assert stats["open"] == 1
    assert stats["table_amendments"] == 0


# --------------------------------------------------------------------------
# The loop reads all of this over the shared HTTP surface.
# --------------------------------------------------------------------------


def test_decision_and_escalation_routes_exist_and_have_parity() -> None:
    from csd_autodev.loop_actions import API_ROUTES, LOOP_ACTIONS

    for action in (
        "decisions.get",
        "decisions.evaluate",
        "escalations.create",
        "escalations.resolve",
        "escalations.stats",
    ):
        assert action in LOOP_ACTIONS
        assert action in API_ROUTES


def test_decision_events_are_declared_kinds() -> None:
    from csd_autodev import events

    assert {"decision", "escalation"} <= events.EVENT_KINDS


def test_http_surface_serves_the_table_and_the_verdicts() -> None:
    mod = _load_lab_console("csd_lab_console_decisions")

    code, table = mod._resource_routes("GET", "/api/decisions", {})
    assert code == 200
    assert table["schema"] == "csd-decision-table/v1"
    assert set(table["outcomes"]) >= {"refused", "inconclusive", "infra_failed"}

    code, out = mod._resource_routes("POST", "/api/decisions", {"text": PODMAN_TAIL})
    assert code == 200
    assert out["decision"]["classified"]["outcome"] == "infra_failed"
    assert out["decision"]["action"] == "retry"

    code, out = mod._resource_routes("POST", "/api/decisions", {"outcome": "who_knows"})
    assert code == 200
    assert out["decision"]["action"] == "stop_escalate"


def test_http_escalations_refuse_then_accept_then_resolve() -> None:
    mod = _load_lab_console("csd_lab_console_escalations")

    body = _evidence()
    body.pop("log_tail")
    code, err = mod._resource_routes("POST", "/api/escalations", body)
    assert code == 400
    assert "log_tail" in err["error"]

    code, ok = mod._resource_routes("POST", "/api/escalations", _evidence())
    assert code == 200
    esc_id = ok["escalation"]["escalation_id"]

    code, got = mod._resource_routes("GET", f"/api/escalations/{esc_id}", {})
    assert code == 200 and got["status"] == "open"

    code, _missing = mod._resource_routes("GET", "/api/escalations/esc-nope", {})
    assert code == 404

    code, closed = mod._resource_routes(
        "PATCH", f"/api/escalations/{esc_id}", {"resolution": "re-ran, green"}
    )
    assert code == 200 and closed["status"] == "resolved"

    code, stats = mod._resource_routes("GET", "/api/escalations/stats", {})
    assert code == 200 and stats["resolved"] == 1


def test_run_record_carries_the_structured_detail_not_just_a_label() -> None:
    """gate / measured / threshold / margin / stage / step / retryable persist."""
    from csd_autodev import store

    detail = {
        "gate": "G37",
        "measured": 0.952,
        "threshold": 0.99,
        "margin": -0.038,
        "stage": "quant",
        "step": "3-bit",
        "retryable": False,
        "outcome_detail": "latent cosine to fp32 below floor",
    }
    run = store.create_run({"goal": "detail", "status": "running", "source": "test"})
    rec = store.update_run(run["run_id"], {"status": "rejected", **detail})
    assert rec is not None
    for key, value in detail.items():
        assert rec[key] == value
    assert set(detail) <= store._RUN_UPDATE_KEYS
