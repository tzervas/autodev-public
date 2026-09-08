"""Getting unstuck without giving up.

The retry budget bounds ONE AGENT'S STRETCH at ONE APPROACH. It does not bound
the effort. Past the budget the loop asks a different agent what to do
differently, carries that answer back to the implementer, and continues -- and
only after repeated escalations without progress does it set the goal aside,
with the row still open for the queue to return to.

The old behaviour was to stop: emit_retry returned ok=false at the budget and
the goal sat until a person looked at it.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "csd-autodev-loop"

GOAL = "| G-STUCK-1 | Change ONLY `docs/BIG.md`. Substitute the identifiers. | open |"


def load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("AUTODEV_EVENT_DIR", str(tmp_path / "events"))
    monkeypatch.setenv("CSD_GOALS", str(tmp_path / "GOALS.md"))
    loader = importlib.machinery.SourceFileLoader("csd_autodev_loop", str(SCRIPT))
    spec = importlib.util.spec_from_loader("csd_autodev_loop", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_within_budget_it_retries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Cheap first: the same agent gets its full stretch before anyone is asked."""
    mod = load(tmp_path, monkeypatch)
    called: list[str] = []
    monkeypatch.setattr(mod, "escalate", lambda *a, **k: called.append("no") or {})
    rec = mod.retry_or_escalate(GOAL, "gate refused", 1, {})
    assert rec["stage"] == "retry"
    assert called == [], "must not ask for help while the budget remains"


def test_at_the_budget_it_asks_for_help_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The budget must not end the effort. This is the whole point."""
    mod = load(tmp_path, monkeypatch)
    goals = tmp_path / "GOALS.md"
    goals.write_text(
        "| id | goal | state |\n|---|---|---|\n"
        "| G-STUCK-1 | Change ONLY `docs/BIG.md`. | open |\n",
        encoding="utf-8",
    )
    mod.GOALS = goals
    monkeypatch.setattr(
        mod,
        "ask_for_help",
        lambda *a, **k: {
            "ok": True,
            "rc": 0,
            "guidance": "The file is too large for one reply. "
            "Change only the lines that match.",
        },
    )
    monkeypatch.setattr(mod, "post_mail", lambda *a, **k: {"ok": True})
    rec = mod.retry_or_escalate(GOAL, "apply refused: shrink", mod.approach_retries(), {})

    assert rec["stage"] == "escalate"
    assert rec["set_aside"] is False
    assert "too large for one reply" in rec["approach_note"]
    # A NEW APPROACH EARNS A FULL STRETCH.
    assert rec["implement_attempts"] == 0
    # And the loop woke itself, rather than waiting for an operator.
    kinds = [
        __import__("json").loads(p.read_text())["kind"]
        for p in (tmp_path / "events").glob("*.json")
    ]
    assert "continue" in kinds


def test_the_guidance_reaches_the_implementer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Asking for help that never reaches the brief is a log line."""
    mod = load(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "TARGET_REPO", "o/r")
    monkeypatch.setattr(mod, "repo_context", lambda *a, **k: "")
    captured: list[str] = []

    def fake_run(argv, **_k):
        if "--prompt" in argv:
            captured.append(argv[argv.index("--prompt") + 1])
        return 0, "NOTE: nope"

    monkeypatch.setattr(mod, "_run", fake_run)
    mod.implement(GOAL, {"approach_note": "Do it in sections, largest first."})
    assert captured
    assert "Do it in sections, largest first." in captured[0]
    assert "DIFFERENT approach" in captured[0]


def test_repeated_escalation_sets_aside_but_leaves_the_row_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Set aside is not abandoned: the queue must be able to come back."""
    mod = load(tmp_path, monkeypatch)
    goals = tmp_path / "GOALS.md"
    goals.write_text(
        "| id | goal | state |\n|---|---|---|\n"
        "| G-STUCK-1 | Change ONLY `docs/BIG.md`. | open |\n"
        "| G-OTHER-1 | Something else. | open |\n",
        encoding="utf-8",
    )
    mod.GOALS = goals
    asked: list[str] = []
    monkeypatch.setattr(
        mod, "ask_for_help", lambda goal, why, tried: asked.append(why) or {}
    )
    monkeypatch.setattr(mod, "post_mail", lambda *a, **k: {"ok": True})

    rec = mod.retry_or_escalate(
        GOAL,
        "still failing",
        mod.approach_retries(),
        {"escalations": mod.MAX_ESCALATIONS},
    )
    assert rec["set_aside"] is True
    # At the cap it asks ONCE more, and asks a DIFFERENT question: not "how do I
    # do this", which has been answered three times, but "is the goal itself
    # wrong". Anything else leaves a bad work item in the queue forever.
    assert len(asked) == 1
    assert "Assume the GOAL is wrong" in asked[0]

    text = goals.read_text()
    assert "| open |" in text.splitlines()[2], "the row must stay open"
    assert "blocked: still failing" in text
    # And the other goal is untouched.
    assert "| G-OTHER-1 | Something else. | open |" in text


def test_note_blocker_does_not_corrupt_a_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Goal cells hold backticked paths and commas; the row must survive."""
    mod = load(tmp_path, monkeypatch)
    goals = tmp_path / "GOALS.md"
    row = "| G-A-1 | Change ONLY `docs/a.md`, keeping `x | y` out of it. | open |"
    goals.write_text(f"| id | goal | state |\n|---|---|---|\n{row}\n", encoding="utf-8")
    mod.GOALS = goals
    # This row has an extra pipe inside the cell, so it is NOT a 5-cell row and
    # must be left strictly alone rather than mangled.
    assert mod.note_blocker("| G-A-1 |", "why")["ok"] is False
    assert goals.read_text().splitlines()[2] == row


def test_blocking_twice_does_not_stack_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A goal revisited and blocked again must not grow a note per attempt."""
    mod = load(tmp_path, monkeypatch)
    goals = tmp_path / "GOALS.md"
    goals.write_text(
        "| id | goal | state |\n|---|---|---|\n| G-A-1 | Do a thing. | open |\n",
        encoding="utf-8",
    )
    mod.GOALS = goals
    assert mod.note_blocker("| G-A-1 |", "first")["ok"] is True
    assert mod.note_blocker("| G-A-1 |", "second")["ok"] is False
    assert goals.read_text().count("blocked:") == 1


def test_the_escalation_count_survives_the_retries_between(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Retries sit BETWEEN escalations and must not reset the count.

    Only the escalate branch set `escalations`, so the retry ticks in between
    dropped it from the heartbeat and the next escalation read zero. Observed
    live as `escalations: 1` twice in a row -- a count that can never reach
    MAX_ESCALATIONS, so a hopeless goal is never set aside and the loop pays for
    advice forever.
    """
    mod = load(tmp_path, monkeypatch)
    rec = mod.retry_or_escalate(GOAL, "still failing", 1, {"escalations": 2})
    assert rec["stage"] == "retry"
    assert rec["escalations"] == 2


def test_a_repeated_decline_escalates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A decline is ok=true, so nothing retried and the goal stalled politely.

    G-ARCH-PIPELINE declined every tick on a "blocked until X has landed"
    condition whose prerequisite HAD landed -- the goal's wording was stale and
    nothing in the loop could say so. The first decline is free; the second goes
    to the helper, whose brief already asks it to say when the GOAL is the
    problem and how to reword it.
    """
    mod = load(tmp_path, monkeypatch)
    goals = tmp_path / "GOALS.md"
    goals.write_text(
        "| id | goal | state |\n|---|---|---|\n| G-STUCK-1 | x | open |\n",
        encoding="utf-8",
    )
    mod.GOALS = goals
    asked: list[str] = []
    monkeypatch.setattr(
        mod,
        "ask_for_help",
        lambda goal, why, tried: asked.append(why)
        or {"ok": True, "guidance": "The blocker wording is stale; drop it."},
    )
    monkeypatch.setattr(mod, "post_mail", lambda *a, **k: {"ok": True})

    rec = mod.retry_or_escalate(
        GOAL, "declined 2 times: prerequisite has not landed", mod.approach_retries(), {}
    )
    assert rec["stage"] == "escalate"
    assert asked and "declined 2 times" in asked[0]
    assert "stale" in rec["approach_note"]


def test_land_git_reports_a_no_op_so_the_tick_can_escalate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A no-op fails WITHOUT gates, so the gate hook alone never saw it.

    land_git returns before any gate runs, and the tick only escalated when
    `gates` were present -- so the one failure meaning "the model did nothing"
    was the one failure that never asked for help. The flag it sets is what the
    tick now also keys on.
    """
    mod = load(tmp_path, monkeypatch)
    dest = tmp_path / "wt"
    dest.mkdir()
    monkeypatch.setattr(mod, "dest_worktree", lambda _g: dest)
    monkeypatch.setattr(mod, "run_repo_gates", lambda *_a, **_k: {"ok": True})
    monkeypatch.setattr(mod, "branch_ever_merged", lambda *_a, **_k: False)

    def fake_git(args, cwd):
        if args[0] in {"commit", "push"}:
            raise AssertionError(f"must not {args[0]} on a no-op")
        out = {"rev-parse": "abc123", "remote": "origin", "diff": "", "add": ""}
        return {"ok": True, "stdout": out.get(args[0], ""), "rc": 0}

    monkeypatch.setattr(mod, "git_cmd", fake_git)
    rec = mod.land_git(GOAL, {"ok": True, "path": "docs/A.md"})
    assert rec["ok"] is False
    assert rec["no_op"] is True
    assert not rec.get("gates"), "a no-op never reaches the gates"


def test_the_decline_record_carries_the_escalation_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Declines sit between escalations, exactly as retries do."""
    mod = load(tmp_path, monkeypatch)
    rec = mod.retry_or_escalate(GOAL, "declined", 1, {"escalations": 2})
    assert rec["escalations"] == 2


def test_attempt_count_carries_across_ticks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Every tick is a fresh process; a per-run counter is always 1.

    That defect was written three times independently -- for the implementer,
    for apply refusals, and for gate refusals, where the field being read never
    incremented at all and the ladder could not reach escalation. One helper.
    """
    mod = load(tmp_path, monkeypatch)
    assert mod.attempt_count({}, GOAL, "git_attempts") == 1
    last = {"goal": GOAL[:120], "git_attempts": 2}
    assert mod.attempt_count(last, GOAL, "git_attempts") == 3
    # A different key on the same goal counts separately.
    assert mod.attempt_count(last, GOAL, "apply_attempts") == 1
    # A different goal resets: a budget is per goal, not per session.
    assert mod.attempt_count(last, "| G-OTHER-1 | x | open |", "git_attempts") == 1


def test_a_set_aside_goal_goes_to_the_back_of_the_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Skipped while there is other work; returned to when there is not.

    G-LOOP-1 was correctly set aside after three escalations and picked again on
    the very next tick, which is the spin the escalation cap exists to end. The
    row stays `open` on purpose -- a state change would take it out of the queue
    for good, and the rule is that a skipped goal must eventually be returned to.
    """
    mod = load(tmp_path, monkeypatch)
    goals = tmp_path / "GOALS.md"
    goals.write_text(
        "| id | goal | state |\n|---|---|---|\n"
        "| G-BLOCKED-1 | Impossible. blocked: asked three times | open |\n"
        "| G-FRESH-1 | Something doable. | open |\n",
        encoding="utf-8",
    )
    mod.GOALS = goals
    monkeypatch.setattr(mod, "TARGET_REPO", "o/r")
    assert "G-FRESH-1" in mod.next_open_goal()

    # With nothing else open, it comes back to the blocked one rather than idling.
    goals.write_text(
        "| id | goal | state |\n|---|---|---|\n"
        "| G-BLOCKED-1 | Impossible. blocked: asked three times | open |\n"
        "| G-FRESH-1 | Something doable. | done |\n",
        encoding="utf-8",
    )
    assert "G-BLOCKED-1" in mod.next_open_goal()


def _goals(tmp_path: Path, row: str) -> Path:
    g = tmp_path / "GOALS.md"
    g.write_text(f"| id | goal | state |\n|---|---|---|\n{row}\n", encoding="utf-8")
    return g


def test_an_exhausted_goal_is_rewritten_not_parked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The ladder can conclude the GOAL is wrong, and nothing acted on it.

    G-LOOP-1 was written by the loop's own adoption pass and named a file that
    is a static configuration dictionary, not a logic module. The helper said
    so. Setting it aside left it open, blocking the repository's release gate,
    to be returned to unchanged every time the queue drained. A bad work item
    should be repaired.
    """
    mod = load(tmp_path, monkeypatch)
    mod.GOALS = _goals(tmp_path, "| G-STUCK-1 | Do the impossible thing. | open |")
    monkeypatch.setattr(
        mod,
        "ask_for_help",
        lambda *a, **k: {"ok": True, "guidance": "Add a CI check to `scripts/gate`."},
    )
    monkeypatch.setattr(mod, "post_mail", lambda *a, **k: {"ok": True})

    rec = mod.retry_or_escalate(
        GOAL,
        "still failing",
        mod.approach_retries(),
        {"escalations": mod.MAX_ESCALATIONS},
    )
    assert rec["set_aside"] is True
    assert rec["rewrite"]["action"] == "rewritten"
    text = mod.GOALS.read_text()
    assert "rewritten: Add a CI check" in text
    assert "| open |" in text, "a rewritten goal stays in the queue"
    # And it earns fresh budgets, because it is a different goal now.
    assert rec["escalations"] == 0
    assert rec["implement_attempts"] == 0


def test_a_goal_that_fails_twice_is_marked_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Rewriting round and round is the same spin wearing a third hat."""
    mod = load(tmp_path, monkeypatch)
    mod.GOALS = _goals(tmp_path, "| G-STUCK-1 | rewritten: try this instead. | open |")
    monkeypatch.setattr(
        mod, "ask_for_help", lambda *a, **k: {"ok": True, "guidance": "Cannot be done."}
    )
    monkeypatch.setattr(mod, "post_mail", lambda *a, **k: {"ok": True})

    rec = mod.retry_or_escalate(
        GOAL,
        "failed again",
        mod.approach_retries(),
        {"escalations": mod.MAX_ESCALATIONS},
    )
    assert rec["rewrite"]["action"] == "invalid"
    text = mod.GOALS.read_text()
    assert "| invalid |" in text
    assert "invalid because: Cannot be done." in text
    assert "| open |" not in text, "an invalid goal leaves the queue and the gate"


def test_a_decline_wakes_the_loop_for_the_next_goal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A decline is progress through the QUEUE, even though it is not progress
    on the goal.

    The first decline is free and emitted nothing, so with seven goals open the
    loop went quiet for the hour after declining the first one -- waiting for a
    forge event that a decline never produces.
    """
    mod = load(tmp_path, monkeypatch)
    goals = tmp_path / "GOALS.md"
    goals.write_text(
        "| id | goal | state |\n|---|---|---|\n| G-NEXT-1 | Another one. | open |\n",
        encoding="utf-8",
    )
    mod.GOALS = goals
    assert mod.emit_continue("declined")["ok"] is True
    kinds = [
        __import__("json").loads(p.read_text())["kind"]
        for p in (tmp_path / "events").glob("*.json")
    ]
    assert kinds == ["continue"]

    # And it cannot spin on an empty queue: nothing open, nothing emitted.
    goals.write_text(
        "| id | goal | state |\n|---|---|---|\n| G-NEXT-1 | Done. | done |\n",
        encoding="utf-8",
    )
    before = len(list((tmp_path / "events").glob("*.json")))
    assert mod.emit_continue("declined")["skipped"] == "no open goal left"
    assert len(list((tmp_path / "events").glob("*.json"))) == before


def test_a_diagnosis_is_not_written_into_the_goal_cell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The helper answers with a REASON far more often than with a goal.

    Writing the reason into the goal cell replaced seven goals with sentences
    like "The goal is unachievable because...", destroying the original intent.
    Whoever reads the row next needs to know what was being asked, not only
    that it failed.
    """
    mod = load(tmp_path, monkeypatch)
    mod.GOALS = _goals(tmp_path, "| G-STUCK-1 | Change ONLY `a.md`. | open |")
    monkeypatch.setattr(
        mod,
        "ask_for_help",
        lambda *a, **k: {
            "ok": True,
            "guidance": ("The goal is unachievable because the file does not exist."),
        },
    )
    monkeypatch.setattr(mod, "post_mail", lambda *a, **k: {"ok": True})

    rec = mod.retry_or_escalate(
        GOAL, "failing", mod.approach_retries(), {"escalations": mod.MAX_ESCALATIONS}
    )
    assert rec["rewrite"]["action"] == "invalid"
    text = mod.GOALS.read_text()
    assert "Change ONLY `a.md`." in text, "the original goal must survive"
    assert "invalid because: The goal is unachievable" in text
    assert "rewritten:" not in text


def test_an_actual_corrected_goal_is_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The other side: a real replacement goal must still be taken."""
    mod = load(tmp_path, monkeypatch)
    mod.GOALS = _goals(tmp_path, "| G-STUCK-1 | Change ONLY `a.md`. | open |")
    monkeypatch.setattr(
        mod,
        "ask_for_help",
        lambda *a, **k: {
            "ok": True,
            "guidance": (
                "Change ONLY `docs/cli.md`, reading the commands from `scripts/autodev`."
            ),
        },
    )
    monkeypatch.setattr(mod, "post_mail", lambda *a, **k: {"ok": True})

    rec = mod.retry_or_escalate(
        GOAL, "failing", mod.approach_retries(), {"escalations": mod.MAX_ESCALATIONS}
    )
    assert rec["rewrite"]["action"] == "rewritten"
    text = mod.GOALS.read_text()
    assert "rewritten: Change ONLY `docs/cli.md`" in text
    assert "| open |" in text


def test_a_silent_helper_does_not_invalidate_the_goal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A model that times out is not evidence that the work is impossible."""
    mod = load(tmp_path, monkeypatch)
    mod.GOALS = _goals(tmp_path, "| G-STUCK-1 | Change ONLY `a.md`. | open |")
    monkeypatch.setattr(
        mod, "ask_for_help", lambda *a, **k: {"ok": False, "guidance": ""}
    )
    monkeypatch.setattr(mod, "post_mail", lambda *a, **k: {"ok": True})

    mod.retry_or_escalate(
        GOAL, "failing", mod.approach_retries(), {"escalations": mod.MAX_ESCALATIONS}
    )
    text = mod.GOALS.read_text()
    assert "| open |" in text, "the goal must survive a silent helper"
    assert "invalid" not in text


def test_a_retry_carries_the_reason_the_last_attempt_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A retry that sends the same prompt cannot succeed.

    The single most expensive lesson of the session: an unparseable reply was
    retried nine times, byte for byte, and the goal was then invalidated for
    having failed nine times. The reason now travels with the retry exactly as
    escalation guidance does -- and unlike escalation it costs nothing and
    happens on the FIRST failure.
    """
    mod = load(tmp_path, monkeypatch)
    rec = mod.retry_or_escalate(GOAL, "apply refused: the fence never closes", 1, {})
    assert rec["stage"] == "retry"
    assert "the fence never closes" in rec["approach_note"]
    assert "previous attempt was rejected" in rec["approach_note"]
