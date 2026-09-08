"""Dynamic budget: sizing a call from the prompt actually being sent.

Static per-role settings failed three components this session -- reviewer,
planner and coder-deep -- each fixed by hand, per model, after it broke. The
mechanism was identical every time: thinking is charged against the same budget
as the answer, so a long prompt plus deep reasoning leaves no room for the reply.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GW = ROOT / "scripts" / "csd-gateway-complete"


def load(profile: Path | None = None) -> Any:
    """Import the gateway with its learned profile pinned.

    PROFILE_PATH is resolved at import, and these tests assert the COLD-START
    budgets. Without pinning, the module picks up whatever the autotuner has
    written on this host and the assertions become a statement about that
    host's history -- which is how the "autodev runs at low effort" test came to
    fail for the service account and pass for everybody else.
    """
    loader = importlib.machinery.SourceFileLoader("csd_gateway_complete", str(GW))
    spec = importlib.util.spec_from_loader("csd_gateway_complete", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    if profile is not None:
        mod.PROFILE_PATH = str(profile)
    elif "AUTODEV_BUDGET_PROFILE" not in os.environ:
        # Only the DEFAULT is neutralised. A test that sets the variable
        # deliberately -- to write a profile and read it back -- still gets it.
        mod.PROFILE_PATH = os.devnull
    return mod


def test_review_never_pays_for_deep_reasoning():
    """Reviewing is comparison: the diff and the goal are both in front of it.

    At effort=high it timed out on every real diff AND produced worse verdicts
    than at none, so paying for thinking there bought a slower wrong answer.
    """
    m = load()
    for size in (400, 4_000, 40_000):
        assert m.plan_budget("review", "x" * size)["reasoning_effort"] == "none"


def test_a_long_prompt_degrades_the_effort_rather_than_the_answer():
    """The answer is what the caller needs; the thinking is what it can afford
    to lose. Degrading the other way returns a truncated file."""
    m = load()
    small = m.plan_budget("autodev", "x" * 10_000)
    huge = m.plan_budget("autodev", "x" * 150_000)
    assert small["reasoning_effort"] == "low"
    assert huge["reasoning_effort"] == "none"


def test_a_call_that_cannot_fit_its_answer_is_refused():
    """Doomed before it is made. Below half the needed room the model returns a
    truncated file, and apply_proposal will happily write it."""
    m = load()
    assert m.plan_budget("autodev", "x" * 230_000)["doomed"] is True
    assert m.plan_budget("autodev", "x" * 10_000)["doomed"] is False


def test_the_budget_never_exceeds_the_room_left():
    """The whole failure in one assertion: prompt plus answer must fit the
    window, or finish_reason=length arrives with empty content."""
    m = load()
    for kind in ("review", "plan", "autodev", "utility"):
        for size in (1_000, 50_000, 120_000):
            b = m.plan_budget(kind, "x" * size)
            assert b["max_tokens"] <= max(b["room"], 512), (kind, size)


def test_recovery_degrades_and_does_not_retry_a_real_failure():
    """A timeout is a budget that was too ambitious, not an outage -- the
    gateway answers a trivial prompt in three seconds. But a model that is not
    resident (3) is real, and retrying it burns the budget to report the same
    thing twice."""
    m = load()
    calls: list = []

    def fake(kind, prompt, timeout, effort=None):
        calls.append(effort)
        # Fails at the first effort, succeeds at the degraded one.
        return (4, "TimeoutError") if len(calls) < 2 else (0, "answer")

    m.complete = fake
    rc, _text = m.complete_with_recovery("autodev", "p", 60)
    assert rc == 0
    # Two, not three: autodev's default IS low, so the ladder is [low, none] --
    # retrying low after low had failed was the bug traffic caught.
    assert len(calls) == 2

    calls.clear()
    m.complete = lambda *a, **k: (calls.append(k.get("effort")), (3, "not resident"))[1]
    rc, _ = m.complete_with_recovery("autodev", "p", 60)
    assert rc == 3
    assert len(calls) == 1  # not retried


# --------------------------------------------------------------------------- #
# Continuation: long context and deep reasoning without either eating the other
# --------------------------------------------------------------------------- #
def test_a_truncated_answer_is_continued_not_discarded():
    """Output cut off mid-flight is not wasted work. This is what makes an
    artifact larger than one budget possible at all."""
    m = load()
    calls: list[str] = []

    def fake(kind, prompt, timeout, effort=None):
        calls.append(prompt)
        if len(calls) == 1:
            return 9, "def a():\n    return 1\ndef b():"
        return 0, "\n    return 2\n"

    m.complete = fake
    rc, text = m.complete_continued("autodev", "write two functions", 60)
    assert rc == 0
    assert "return 1" in text and "return 2" in text
    assert "CUT OFF" in calls[1]


def test_a_repeated_overlap_is_not_duplicated():
    """Models restate the last line before carrying on. Concatenating naively
    doubles it -- in Python that is a syntax error or a silently doubled
    statement."""
    m = load()
    assert m._dedupe_join("aaa\nkeep this line\n", "keep this line\nand more\n") == (
        "aaa\nkeep this line\nand more\n"
    )


def test_continuation_stops_when_it_stops_progressing():
    """An unattended loop that does not check for progress burns a GPU until
    somebody notices -- the exact failure this system exists to remove."""
    m = load()
    n = {"i": 0}

    def stuck(kind, prompt, timeout, effort=None):
        n["i"] += 1
        return 9, "same"

    m.complete = stuck
    rc, _ = m.complete_continued("autodev", "p", 60)
    # It must give up quickly, not run to the continuation cap.
    assert n["i"] <= 3
    assert rc in (0, 9)


def test_continuation_is_bounded():
    """Even while genuinely progressing, it cannot run forever."""
    m = load()
    n = {"i": 0}

    def always_more(kind, prompt, timeout, effort=None):
        n["i"] += 1
        return 9, f"chunk{n['i']} "

    m.complete = always_more
    rc, _ = m.complete_continued("autodev", "p", 60)
    assert n["i"] <= m.MAX_CONTINUATIONS + 1
    assert rc == 9


def test_a_prompt_too_large_splits_instead_of_refusing():
    """Refusing was honest, but a refusal is not a result and the largest tasks
    are the ones worth doing. Phase one thinks; phase two writes."""
    m = load()
    kinds: list[str] = []

    def fake(kind, prompt, timeout, effort=None):
        kinds.append(kind)
        return 0, "PLAN: do the thing" if kind == "plan" else "the artifact"

    m.complete = fake
    rc, text = m.complete_with_recovery("autodev", "x" * 230_000, 60)
    assert rc == 0
    assert kinds[0] == "plan"  # thought about first
    assert "artifact" in text


def test_the_ladder_does_not_retry_the_effort_that_just_failed():
    """Caught by traffic, not by reasoning about it.

    `plan` defaults to low, so the ladder [None, low, none] retried low straight
    after low had failed -- and the gateway's cache returned the identical
    failure in 0.04s. A wasted round that reads like a real attempt in the log,
    and on a cache miss it is a wasted minute instead.
    """
    m = load()
    efforts: list = []

    def fake(kind, prompt, timeout, effort=None):
        efforts.append(effort or m.KIND_BUDGET[kind]["effort"])
        return 6, "budget gone"

    m.complete_continued = fake
    m.complete_with_recovery("plan", "p", 60)
    assert len(efforts) == len(set(efforts)), efforts
    # plan defaults to none, so there is nothing weaker to fall back to.
    assert efforts == ["none"]


# --------------------------------------------------------------------------- #
# The autotuner: what worked becomes what happens next
# --------------------------------------------------------------------------- #
def test_a_failure_demotes_the_effort_immediately(tmp_path, monkeypatch):
    """`plan` produced this failure NINE times before a person read the log.

    The retry recovered each one, so nothing looked broken -- and each recovery
    cost 54 seconds. Waiting for a calibration run to notice repeats the same
    failure every call in between.
    """
    monkeypatch.setenv("AUTODEV_BUDGET_PROFILE", str(tmp_path / "p.json"))
    m = load()
    m.demote("autodev", "rc=6 at the profile's own setting")
    prof = json.loads((tmp_path / "p.json").read_text())
    assert prof["kinds"]["autodev"]["effort"] == "none"  # low -> none
    assert prof["kinds"]["autodev"]["demoted_from"] == "low"
    assert "rc=6" in prof["kinds"]["autodev"]["demoted_why"]


def test_demotion_stops_at_the_weakest_setting(tmp_path, monkeypatch):
    """There is nothing below none, and a tuner that keeps 'adjusting' past the
    end of its range writes nonsense into its own state."""
    monkeypatch.setenv("AUTODEV_BUDGET_PROFILE", str(tmp_path / "p.json"))
    m = load()
    for _ in range(4):
        m.demote("review", "x")  # review already starts at none
    # It writes NOTHING rather than inventing a weaker setting -- so the file may
    # not exist at all, and that is the pass condition.
    if (tmp_path / "p.json").exists():
        prof = json.loads((tmp_path / "p.json").read_text())
        assert prof.get("kinds", {}).get("review", {}).get("effort") in (
            None,
            "none",
        )
    assert m.plan_budget("review", "x" * 5000)["reasoning_effort"] == "none"


def test_the_profile_overrides_the_cold_start(tmp_path, monkeypatch):
    """KIND_BUDGET is the estimate; the profile is what traffic measured. The
    measurement wins, or nothing was learned."""
    monkeypatch.setenv("AUTODEV_BUDGET_PROFILE", str(tmp_path / "p.json"))
    (tmp_path / "p.json").write_text(
        json.dumps(
            {
                "chars_per_token": 4.0,
                "kinds": {"autodev": {"effort": "none", "answer_tokens": 3000}},
            }
        )
    )
    m = load()
    b = m.plan_budget("autodev", "x" * 8000)
    assert b["reasoning_effort"] == "none"
    assert b["prompt_tokens"] == 2000  # 8000 / 4.0, not / 3.48
    assert b["max_tokens"] == 3000  # the learned answer size


def test_an_unreadable_profile_falls_back_silently(tmp_path, monkeypatch):
    """An autotuner that breaks the caller when its own state file is corrupt is
    worse than no autotuner."""
    monkeypatch.setenv("AUTODEV_BUDGET_PROFILE", str(tmp_path / "p.json"))
    (tmp_path / "p.json").write_text("{not json")
    m = load()
    b = m.plan_budget("autodev", "x" * 8000)
    assert b["reasoning_effort"] == m.KIND_BUDGET["autodev"]["effort"]
