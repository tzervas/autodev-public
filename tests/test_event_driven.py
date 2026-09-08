"""The event-driven layer: waking on emitters, and discovering state.

Each test below corresponds to a way the old timer-and-heartbeat design could
fail unattended, so the assertions are about behaviour under loss -- a lost
heartbeat, an unreachable forge, an undrained directory -- rather than about the
happy path, which was never the problem.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.machinery
import importlib.util
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOOP = ROOT / "scripts" / "csd-autodev-loop"
EVENTS = ROOT / "scripts" / "csd-forge-events"


def load_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("CSD_AUTODEV_WT", str(tmp_path / "wt"))
    monkeypatch.setenv("CSD_GROK_NEED", str(tmp_path / "need-grok.json"))
    monkeypatch.setenv("CSD_AUTODEV_STALL", str(tmp_path / "stall.json"))
    monkeypatch.setenv("CSD_GPU_PLAN", str(tmp_path / "gpu-plan.json"))
    monkeypatch.setenv("CSD_HARNESS_STATE", str(tmp_path / "harness-state"))
    monkeypatch.setenv("CSD_GOALS", str(tmp_path / "GOALS.md"))
    monkeypatch.setenv("AUTODEV_EVENT_DIR", str(tmp_path / "events"))
    loader = importlib.machinery.SourceFileLoader("csd_autodev_loop", str(LOOP))
    spec = importlib.util.spec_from_loader("csd_autodev_loop", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# Draining: what re-arms the path unit
# --------------------------------------------------------------------------- #
def test_drain_of_an_empty_directory_is_not_an_error(tmp_path, monkeypatch):
    """A manual run and the reconcile timer both produce a tick with no events.

    If an empty drain were an error the reconcile net would report failure every
    hour, and the one signal that says "a delivery was lost" would be buried in
    noise that always fires.
    """
    mod = load_loop(tmp_path, monkeypatch)
    rec = mod.drain_events()
    assert rec["ok"] is True
    assert rec["count"] == 0
    assert rec["why"] == "manual"


def test_drain_consumes_so_the_next_event_can_fire(tmp_path, monkeypatch):
    """systemd's path unit re-arms on the empty-to-nonempty edge only.

    A drain that read without deleting would let the loop run once and then never
    again -- the failure would look like "autodev stopped for no reason", which is
    the hardest kind to diagnose.
    """
    d = tmp_path / "events"
    d.mkdir()
    (d / "1-pull_request.json").write_text(
        json.dumps(
            {
                "kind": "pull_request",
                "action": "closed",
                "merged": True,
                "number": 7,
                "head": "autodev/g-val",
            }
        )
    )
    (d / "2-status.json").write_text(
        json.dumps({"kind": "status", "state": "success", "sha": "abc"})
    )

    mod = load_loop(tmp_path, monkeypatch)
    first = mod.drain_events()
    assert first["count"] == 2
    assert first["merged_pr"] == 7
    assert first["branches"] == ["autodev/g-val"]
    assert first["ci_states"] == ["success"]
    assert first["why"] == "pull_request+status"

    assert list(d.glob("*.json")) == []
    assert mod.drain_events()["count"] == 0


# --------------------------------------------------------------------------- #
# Discovery: the forge is the source of truth, the heartbeat is a cache
# --------------------------------------------------------------------------- #
def _pulls(mod, monkeypatch, rows):
    monkeypatch.setattr(mod, "forgejo_cmd", lambda *a, **k: {"ok": True, "pulls": rows})


def test_a_lost_heartbeat_does_not_open_a_second_pr(tmp_path, monkeypatch):
    """The failure this whole change exists to prevent.

    With an empty heartbeat the loop believed nothing was in flight, picked the
    next goal, and opened a second PR beside the first. Asking the forge makes
    that impossible: it cannot lose the PR, because the PR is what it stores.
    """
    mod = load_loop(tmp_path, monkeypatch)
    _pulls(
        mod,
        monkeypatch,
        [{"number": 11, "head": "autodev/g-val", "title": "tests for validate"}],
    )
    rec = mod.discover_inflight("org/repo", {})
    assert rec["number"] == 11
    assert rec["head"] == "autodev/g-val"
    assert rec["merged"] is False
    assert rec["discovery"]["adopted"] == 11


def test_the_heartbeat_is_kept_when_the_two_agree(tmp_path, monkeypatch):
    """The heartbeat carries the goal text, run id and approach history.

    Discovery must not flatten a rich record into the three fields the forge
    knows about, or every adopted tick would forget which approaches had already
    failed and would retry them.
    """
    mod = load_loop(tmp_path, monkeypatch)
    _pulls(mod, monkeypatch, [{"number": 11, "head": "autodev/g-val"}])
    last = {
        "number": 11,
        "head": "autodev/g-val",
        "goal": "| G-VAL-1 | ... |",
        "run_id": "r-99",
        "approach": "third",
    }
    rec = mod.discover_inflight("org/repo", last)
    assert rec["run_id"] == "r-99"
    assert rec["approach"] == "third"
    assert rec["discovery"]["agrees"] is True


def test_a_pr_the_forge_no_longer_lists_is_reported_stale(tmp_path, monkeypatch):
    """It merged or was closed while we were not looking.

    Discovery deliberately does not decide which: only the sync path can tell a
    merge from an abandonment, and guessing "merged" here would close a goal
    whose work never landed.
    """
    mod = load_loop(tmp_path, monkeypatch)
    _pulls(mod, monkeypatch, [])
    rec = mod.discover_inflight("org/repo", {"number": 11, "head": "autodev/g-val"})
    assert rec["discovery"]["stale_inflight"] == 11
    assert rec["number"] == 11  # left for sync_worktree to resolve


def test_an_unreachable_forge_falls_back_to_the_heartbeat(tmp_path, monkeypatch):
    """Reachability failures must not read as "nothing is open".

    That is the dangerous direction: it would start new work on top of an open
    PR every time the network hiccuped. Keeping the stale heartbeat is wrong at
    worst by one tick; the alternative is wrong by a duplicate PR.
    """
    mod = load_loop(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "forgejo_cmd", lambda *a, **k: {"ok": False, "rc": 7})
    last = {"number": 11, "head": "autodev/g-val", "goal": "g"}
    rec = mod.discover_inflight("org/repo", last)
    assert rec["number"] == 11
    assert rec["discovery"]["ok"] is False


def test_only_autodev_branches_are_adopted(tmp_path, monkeypatch):
    """A human's open PR is not autodev's to take over."""
    mod = load_loop(tmp_path, monkeypatch)
    _pulls(mod, monkeypatch, [{"number": 12, "head": "feat/operator-change"}])
    rec = mod.discover_inflight("org/repo", {})
    assert "number" not in rec or rec.get("number") is None
    assert rec["discovery"]["open"] == 0


def test_each_task_of_a_goal_gets_its_own_branch(tmp_path, monkeypatch):
    """Goals decompose into numbered tasks, each landing as its own PR.

    goal_id used to stop at the second hyphen, so G-PROVE-1 and G-PROVE-2 both
    named the branch `autodev/g-prove`: the second task would push onto the
    first task's branch. It was invisible because no goal had reached a second
    task -- the decomposition scheme this repo documents makes it certain.
    """
    mod = load_loop(tmp_path, monkeypatch)
    one = mod.goal_branch("| G-PROVE-1 | prove license_ok | open |")
    two = mod.goal_branch("| G-PROVE-2 | prove kv_gib | open |")
    assert one == "autodev/g-prove-1"
    assert two == "autodev/g-prove-2"
    assert one != two


def test_closing_one_task_leaves_its_siblings_open(tmp_path, monkeypatch):
    """The other half of the same defect: prefix matching closed whichever row
    came first, so merging task 2 would mark task 1 done and skip it."""
    goals = tmp_path / "GOALS.md"
    goals.write_text(
        "| id | goal | state |\n|---|---|---|\n"
        "| G-PROVE-1 | first | open |\n"
        "| G-PROVE-2 | second | open |\n"
    )
    mod = load_loop(tmp_path, monkeypatch)
    rec = mod.close_goal("autodev/g-prove-2")
    assert rec["closed"] == "G-PROVE-2"
    body = goals.read_text()
    assert "| G-PROVE-1 | first | open |" in body
    assert "| G-PROVE-2 | second | done |" in body


def test_a_branch_named_before_the_fix_still_closes_its_goal(tmp_path, monkeypatch):
    """PR #3 is open on `autodev/g-val` for goal G-VAL-1, pushed under the old
    rule. A fix that stranded it would leave a merged PR whose goal stays open
    and gets rebuilt forever."""
    goals = tmp_path / "GOALS.md"
    goals.write_text(
        "| id | goal | state |\n|---|---|---|\n| G-VAL-1 | validate | open |\n"
    )
    mod = load_loop(tmp_path, monkeypatch)
    assert mod.close_goal("autodev/g-val")["closed"] == "G-VAL-1"


def test_an_exact_match_beats_a_legacy_prefix_match(tmp_path, monkeypatch):
    """The legacy fallback must never shadow a correctly-named branch, even when
    the prefix row comes first in the file."""
    goals = tmp_path / "GOALS.md"
    goals.write_text(
        "| id | goal | state |\n|---|---|---|\n"
        "| G-VAL-1 | first | open |\n"
        "| G-VAL | exact | open |\n"
    )
    mod = load_loop(tmp_path, monkeypatch)
    assert mod.close_goal("autodev/g-val")["closed"] == "G-VAL"
    assert "| G-VAL-1 | first | open |" in goals.read_text()


def test_goal_for_branch_inverts_goal_branch(tmp_path, monkeypatch):
    """The two must not drift: an adopted PR recovers its goal by the same slug
    rule that named the branch."""
    goals = tmp_path / "GOALS.md"
    goals.write_text(
        "| id | goal | state |\n|---|---|---|\n"
        "| G-VAL-1 | prove validate_catalog | open |\n"
        "| G-FIT-2 | prove kv_gib | open |\n"
    )
    mod = load_loop(tmp_path, monkeypatch)
    row = "| G-FIT-2 | prove kv_gib | open |"
    assert mod.goal_branch(row) == "autodev/g-fit-2"
    assert "G-FIT-2" in mod.goal_for_branch("autodev/g-fit-2")
    assert mod.goal_for_branch("autodev/g-nope") == ""


def test_orphan_branches_are_reported_not_acted_on(tmp_path, monkeypatch):
    """A branch pushed just before a tick died is half-finished work.

    Opening a PR for it would be the loop guessing that it was ready. Reporting
    it puts it in front of the operator, which is the correct amount of
    initiative for something whose state is unknown.
    """
    mod = load_loop(tmp_path, monkeypatch)
    monkeypatch.setattr(
        mod,
        "git_cmd",
        lambda *a, **k: {
            "ok": True,
            "stdout": (
                "sha1\trefs/heads/autodev/g-val\nsha2\trefs/heads/autodev/g-orphan\n"
            ),
        },
    )
    monkeypatch.setattr(
        mod,
        "forgejo_cmd",
        lambda *a, **k: {"ok": True, "pulls": [{"number": 1, "head": "autodev/g-val"}]},
    )
    rec = mod.discover_branches(tmp_path, "org/repo")
    assert rec["orphans"] == ["autodev/g-orphan"]


# --------------------------------------------------------------------------- #
# Docs discovery
# --------------------------------------------------------------------------- #
def test_a_repos_own_conventions_reach_the_implementer(tmp_path, monkeypatch):
    """The loop knew the fleet's rules and nothing about the repo it was editing."""
    mod = load_loop(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "CONTRIBUTING.md").write_text("Run `pytest -q`. Conventional commits.")
    (repo / "README.md").write_text("# thing\n" + "x" * 4000)
    (repo / "docs" / "DESIGN.md").write_text("...")
    listing = ["CONTRIBUTING.md", "README.md", "docs/DESIGN.md", "src/x.py"]

    out = mod.discover_docs(repo, listing)
    assert "Run `pytest -q`" in out
    assert "conventions" in out
    # The README is truncated rather than skipped: the useful part is at the top.
    assert "more bytes" in out
    # The docs tree is named, not quoted -- it would eat the budget.
    assert "docs/DESIGN.md" in out


def test_a_repo_with_no_conventions_adds_nothing(tmp_path, monkeypatch):
    """An empty section would be indistinguishable from a repo whose rules were
    not found, and would spend context saying so."""
    mod = load_loop(tmp_path, monkeypatch)
    assert mod.discover_docs(tmp_path, ["src/x.py", "tests/test_x.py"]) == ""


# --------------------------------------------------------------------------- #
# The receiver
# --------------------------------------------------------------------------- #
@pytest.fixture()
def receiver(tmp_path):
    d = tmp_path / "events"
    d.mkdir()
    env = {
        "AUTODEV_EVENT_DIR": str(d),
        "AUTODEV_EVENT_BIND": "127.0.0.1",
        "FORGE_WEBHOOK_SECRET": "s3cret",
        "PATH": "/usr/bin:/bin",
    }
    port = 19200 + (int(time.time() * 1000) % 300)
    p = subprocess.Popen(
        [
            sys.executable,
            str(EVENTS),
            "serve",
            "--port",
            str(port),
            "--targets",
            "org/repo",
        ],
        env=env,
    )
    for _ in range(40):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    else:
        p.terminate()
        pytest.skip("receiver did not come up")
    yield port, d, env
    p.terminate()
    p.wait(timeout=5)


def _post(port, body, kind, secret="s3cret", sign=True):
    raw = json.dumps(body).encode()
    h = {"X-Forgejo-Event": kind, "Content-Type": "application/json"}
    if sign:
        h["X-Gitea-Signature"] = hmac.new(
            secret.encode(), raw, hashlib.sha256
        ).hexdigest()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/", data=raw, headers=h, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_an_unsigned_or_wrongly_signed_delivery_is_refused(receiver):
    """The port is reachable by anything on the LAN. The HMAC is the only thing
    separating the forge from anyone who can guess the URL."""
    port, d, _ = receiver
    body = {
        "repository": {"full_name": "org/repo"},
        "action": "opened",
        "pull_request": {"number": 1, "head": {"ref": "autodev/x"}},
    }
    assert _post(port, body, "pull_request", sign=False) == 401
    assert _post(port, body, "pull_request", secret="wrong") == 401
    assert list(d.glob("*.json")) == []


def test_a_foreign_repository_is_recorded_but_never_wakes_the_loop(receiver):
    """A correctly-signed delivery for a repo we do not work on is still not
    ours to act on."""
    port, d, _ = receiver
    assert (
        _post(
            port,
            {"repository": {"full_name": "someone/else"}, "action": "opened"},
            "pull_request",
        )
        == 202
    )
    assert list(d.glob("*.json")) == []


def test_an_unactionable_event_is_recorded_without_waking(receiver):
    """A loop woken by events it cannot act on is a timer wearing a costume."""
    port, d, _ = receiver
    assert (
        _post(
            port,
            {"repository": {"full_name": "org/repo"}, "action": "created"},
            "issue_comment",
        )
        == 202
    )
    assert list(d.glob("*.json")) == []
    log = (d / "received.jsonl").read_text()
    assert "issue_comment" in log  # recorded, so it can be explained later


def test_a_merge_writes_exactly_one_sentinel(receiver):
    port, d, _env = receiver
    assert (
        _post(
            port,
            {
                "repository": {"full_name": "org/repo"},
                "action": "closed",
                "pull_request": {
                    "number": 9,
                    "merged": True,
                    "head": {"ref": "autodev/g-val"},
                },
            },
            "pull_request",
        )
        == 200
    )
    files = list(d.glob("*.json"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text())
    assert rec["merged"] is True
    assert rec["number"] == 9
    assert rec["head"] == "autodev/g-val"


# --------------------------------------------------------------------------- #
# CI scrutiny: a gate that fires on every commit is not a gate
# --------------------------------------------------------------------------- #
def load_scrutiny():
    import importlib.machinery
    import importlib.util

    path = ROOT / "scripts" / "csd-ci-scrutiny"
    loader = importlib.machinery.SourceFileLoader("csd_ci_scrutiny", str(path))
    spec = importlib.util.spec_from_loader("csd_ci_scrutiny", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_a_manual_only_workflow_is_not_demanded_of_a_pull_request():
    """`abliterate.yml` is workflow_dispatch-only because it burns a GPU on
    command. Demanding a run from it made every PR SUSPECT no matter how green
    the tests were -- the verdict on the first honestly-green commit in a week."""
    mod = load_scrutiny()
    manual = """name: abliterate
on:
  workflow_dispatch:
    inputs:
      job: {required: true}
jobs:
  run:
    steps: []
"""
    assert mod.eligible_for_commit(manual) is False


def test_push_and_pull_request_workflows_are_still_demanded():
    """The half that catches the lie must keep working: a workflow that CAN run
    on this commit and did not is exactly the case scrutiny exists for."""
    mod = load_scrutiny()
    for body in (
        "name: plan\non:\n  push:\n  pull_request:\njobs: {}\n",
        "name: plan\non: [push, pull_request]\njobs: {}\n",
        "name: plan\non:\n  pull_request:\n    branches: [main]\njobs: {}\n",
    ):
        assert mod.eligible_for_commit(body) is True, body


def test_an_unparseable_workflow_is_treated_as_eligible():
    """Unknown must not mean absolved. A workflow whose triggers cannot be read
    stays in the demanded set, so the failure is a loud SUSPECT rather than a
    quiet pass."""
    mod = load_scrutiny()
    assert mod.eligible_for_commit("this is not yaml at all") is True


def test_a_trigger_named_inside_a_step_does_not_count():
    """`on:` is matched at the start of a line, so a script mentioning `push`
    cannot make a manual workflow look automatic."""
    mod = load_scrutiny()
    body = """name: manual
on:
  workflow_dispatch:
jobs:
  run:
    steps:
      - run: git push origin main
"""
    assert mod.eligible_for_commit(body) is False


# --------------------------------------------------------------------------- #
# Fast-forwarding a trunk is not authoring on it
# --------------------------------------------------------------------------- #
def test_a_trunk_can_be_fast_forwarded_to_its_own_remote(tmp_path, monkeypatch):
    """The failure this exists to prevent, and it was silent.

    `merge --ff-only origin/main` while standing on main was refused by the
    protected-ref guard, and sync_worktree excused ff-base from its success
    check -- so a sync that never advanced main reported ok. autodev then cut
    every branch from a main four commits behind, its PR carried the superseded
    workflows, and CI skipped every check on work that was otherwise fine.
    """
    mod = load_loop(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "current_branch", lambda _cwd: "main")
    monkeypatch.setattr(mod, "_run", lambda *_a, **_k: (0, "Fast-forward"))
    rec = mod.git_cmd(["merge", "--ff-only", "origin/main"], tmp_path)
    assert rec["ok"] is True


def test_the_exemption_is_narrow(tmp_path, monkeypatch):
    """It must not become a general licence to merge into a trunk.

    All three conditions are required: --ff-only, standing on the trunk, and
    merging that same trunk's remote-tracking ref. Anything else is the thing
    the guard was written for.
    """
    mod = load_loop(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "current_branch", lambda _cwd: "main")
    monkeypatch.setattr(mod, "_run", lambda *_a, **_k: (0, "ok"))
    for args in (
        ["merge", "--no-edit", "origin/main"],  # not a fast-forward
        ["merge", "--ff-only", "feat/something"],  # not the trunk's remote
        ["merge", "--ff-only", "origin/develop"],  # a DIFFERENT trunk
        ["rebase", "origin/main"],  # never a rebase
    ):
        rec = mod.git_cmd(args, tmp_path)
        assert rec["ok"] is False, args
        assert "refusing" in rec["error"], args


def test_pushing_a_trunk_is_still_refused(tmp_path, monkeypatch):
    """The refusal that actually protects the trunk is unconditional, and this
    change must not have loosened it."""
    mod = load_loop(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "_run", lambda *_a, **_k: (0, "ok"))
    for args in (
        ["push", "origin", "main"],
        ["push", "origin", "HEAD:main"],
        ["push", "--force", "origin", "main"],
    ):
        rec = mod.git_cmd(args, tmp_path)
        assert rec["ok"] is False, args


def test_a_closed_unmerged_pr_does_not_complete_its_goal(tmp_path, monkeypatch):
    """A gone branch is not a merge.

    Both leave nothing to sync to, so both return the worktree to the default
    branch -- but only a merge means the work landed. Treating them alike marked
    G-VAL-1 done when its PR #7 was closed unmerged, taking the goal out of the
    queue for work that was never applied.
    """
    mod = load_loop(tmp_path, monkeypatch)
    wt = tmp_path / "wt"
    wt.mkdir()
    monkeypatch.setattr(mod, "current_branch", lambda _cwd: "autodev/g-val-1")
    # The forge: PR exists, is NOT merged, and its branch is gone.
    monkeypatch.setattr(
        mod,
        "forgejo_cmd",
        lambda *a, **k: {"ok": True, "head": "autodev/g-val-1", "merged": False},
    )
    monkeypatch.setattr(
        mod,
        "git_cmd",
        lambda args, cwd: {
            "ok": True,
            "stdout": "" if args[0] == "ls-remote" else "main",
        },
    )

    rec = mod.sync_worktree(
        wt, repo="org/repo", inflight={"number": 7, "head": "autodev/g-val-1"}
    )
    assert rec["merged_since_last"] is False
    assert rec["abandoned"] is True
    assert "without merging" in rec["reason"].lower()


def test_a_merged_pr_still_completes_its_goal(tmp_path, monkeypatch):
    """delete-on-merge removes the branch on every landed PR, so the merged path
    must keep working with no branch on the forge."""
    mod = load_loop(tmp_path, monkeypatch)
    wt = tmp_path / "wt"
    wt.mkdir()
    monkeypatch.setattr(mod, "current_branch", lambda _cwd: "autodev/g-fit-1")
    monkeypatch.setattr(
        mod,
        "forgejo_cmd",
        lambda *a, **k: {"ok": True, "head": "autodev/g-fit-1", "merged": True},
    )
    monkeypatch.setattr(
        mod,
        "git_cmd",
        lambda args, cwd: {
            "ok": True,
            "stdout": "" if args[0] == "ls-remote" else "main",
        },
    )

    rec = mod.sync_worktree(
        wt, repo="org/repo", inflight={"number": 8, "head": "autodev/g-fit-1"}
    )
    assert rec["merged_since_last"] is True
    assert rec["abandoned"] is False


# --------------------------------------------------------------------------- #
# A refused push is an event, not a dead end
# --------------------------------------------------------------------------- #
def test_a_refused_push_emits_a_wake_event(tmp_path, monkeypatch):
    """The last hole in unattended operation.

    Every other transition emits -- merge, PR, push, and CI via watch-ci. A tick
    refused by the test gate emitted nothing, so a goal the model got wrong on
    the first try sat untouched until the hourly reconcile. Three bad attempts
    cost three hours of doing nothing.
    """
    monkeypatch.setenv("AUTODEV_EVENT_DIR", str(tmp_path / "events"))
    mod = load_loop(tmp_path, monkeypatch)
    rec = mod.emit_retry("| G-TRUST-1 | prove license_ok | open |", "gate", 0)
    assert rec["ok"] is True
    files = list((tmp_path / "events").glob("*.json"))
    assert len(files) == 1
    body = json.loads(files[0].read_text())
    assert body["kind"] == "retry"
    assert body["goal"] == "G-TRUST-1"
    assert body["attempt"] == 1


def test_the_self_retry_is_bounded(tmp_path, monkeypatch):
    """A self-emitted event is a self-retrigger.

    It stops at the same approach-retry budget the loop uses to decide when to
    change tactic. Past that the goal needs a person, and spinning would bury the
    evidence under identical failures.
    """
    monkeypatch.setenv("AUTODEV_EVENT_DIR", str(tmp_path / "events"))
    mod = load_loop(tmp_path, monkeypatch)
    budget = mod.approach_retries()
    rec = mod.emit_retry("| G-TRUST-1 | x | open |", "gate", budget)
    assert rec["ok"] is False
    assert "budget" in rec["stopped"]
    assert list((tmp_path / "events").glob("*.json")) == []


def test_a_retry_event_is_actionable(tmp_path, monkeypatch):
    """It must actually wake the loop, or emitting it is theatre."""
    import importlib.machinery
    import importlib.util

    loader = importlib.machinery.SourceFileLoader("csd_forge_events", str(EVENTS))
    spec = importlib.util.spec_from_loader("csd_forge_events", loader)
    assert spec is not None
    ev = importlib.util.module_from_spec(spec)
    loader.exec_module(ev)
    assert ("retry", None) in ev.WAKES


# --------------------------------------------------------------------------- #
# The review gate: two signals, not one, and it fails closed
# --------------------------------------------------------------------------- #
def test_only_an_approve_reaches_the_merge_gate(tmp_path, monkeypatch):
    """CI proves the suite is green; it cannot prove the change does what the
    goal asked. G-SCHEMA-1 merged a behaviour change with none of its required
    tests, on green CI, and closed."""
    mod = load_loop(tmp_path, monkeypatch)
    present = tmp_path / "csd-autodev-review"
    present.write_text("#!/bin/sh\n")
    monkeypatch.setattr(mod, "REVIEW_CLI", present)
    for verdict, ok in (("APPROVE", True), ("REQUEST_CHANGES", False), ("REJECT", False)):
        monkeypatch.setattr(
            mod,
            "_run",
            lambda *_a, _v=verdict, **_k: (0, json.dumps({"verdict": _v, "why": "x"})),
        )
        rec = mod.review_pr("org/repo", 1, "| G-X | goal | open |", {})
        assert rec["ok"] is ok, verdict
        assert rec["verdict"] == verdict


def test_a_broken_reviewer_does_not_merge(tmp_path, monkeypatch):
    """FAILS CLOSED. The point of the stage is to stop merging on one signal;
    treating a crashed or unreadable reviewer as assent restores exactly that."""
    mod = load_loop(tmp_path, monkeypatch)
    present = tmp_path / "csd-autodev-review"
    present.write_text("#!/bin/sh\n")
    monkeypatch.setattr(mod, "REVIEW_CLI", present)
    for rc, out in (
        (3, "traceback"),
        (0, "no json here"),
        (0, "{}"),
        (0, '{"verdict": null}'),
        (127, ""),
    ):
        monkeypatch.setattr(mod, "_run", lambda *_a, _r=rc, _o=out, **_k: (_r, _o))
        rec = mod.review_pr("org/repo", 1, "| G-X | goal | open |", {})
        assert rec["ok"] is False, (rc, out)
        assert rec["verdict"] == "REQUEST_CHANGES"


def test_a_missing_reviewer_refuses_rather_than_skipping(tmp_path, monkeypatch):
    """An uninstalled reviewer must not silently return the loop to merging on
    CI alone -- that is the exact behaviour this replaced."""
    mod = load_loop(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "REVIEW_CLI", tmp_path / "definitely-not-here")
    rec = mod.review_pr("org/repo", 1, "| G-X | goal | open |", {})
    assert rec["ok"] is False
    assert "CI alone" in rec["why"]


def test_the_reviewer_parses_only_a_real_verdict_line():
    """Silence is not assent. A reasoning model restates the options while
    thinking, so the LAST verdict line wins and no line at all is a refusal."""
    import importlib.machinery
    import importlib.util

    path = ROOT / "scripts" / "csd-autodev-review"
    loader = importlib.machinery.SourceFileLoader("csd_autodev_review", str(path))
    spec = importlib.util.spec_from_loader("csd_autodev_review", loader)
    assert spec is not None
    rv = importlib.util.module_from_spec(spec)
    loader.exec_module(rv)

    assert rv.parse_verdict("VERDICT: APPROVE")[0] == "APPROVE"
    assert rv.parse_verdict("blah\nVERDICT: REJECT - unsafe")[0] == "REJECT"
    # A model musing about the options must not decide it.
    assert (
        rv.parse_verdict(
            "I could say VERDICT: APPROVE but the tests are missing.\n"
            "VERDICT: REQUEST_CHANGES - no tests"
        )[0]
        == "REQUEST_CHANGES"
    )
    # Nothing readable is NOT an approval.
    assert rv.parse_verdict("looks fine to me")[0] == "REQUEST_CHANGES"
    assert rv.parse_verdict("")[0] == "REQUEST_CHANGES"


def test_a_reviewer_objection_reaches_the_next_implementer(tmp_path, monkeypatch):
    """Two agents in sequence are only a pipeline if the second one's objection
    reaches the first.

    Without this the implementer reruns the goal blind and produces the same
    diff, so a reviewer that is WRONG blocks the goal forever instead of being
    answered. The reviewer has already produced one measured false INCOMPLETE,
    so this is not hypothetical.
    """
    mod = load_loop(tmp_path, monkeypatch)
    captured = {}

    def fake_run(argv, **_k):
        captured["prompt"] = " ".join(str(a) for a in argv)
        return 0, "PATH: x.py\n```python\nx = 1\n```"

    monkeypatch.setattr(mod, "_run", fake_run)
    monkeypatch.setattr(mod, "dest_worktree", lambda _g: tmp_path)
    monkeypatch.setattr(mod, "repo_context", lambda *a, **k: "")
    mod.implement(
        "| G-X | do a thing | open |",
        {"review_note": "INCOMPLETE: the goal asked for tests too"},
    )
    assert "REVIEWER REJECTED" in captured["prompt"]
    assert "the goal asked for tests too" in captured["prompt"]
    # It must also be told the reviewer can be wrong, or a false objection
    # becomes an instruction to break working code.
    assert "MISTAKEN" in captured["prompt"]


def test_a_goal_whose_work_already_landed_closes_itself(tmp_path, monkeypatch):
    """G-CI-1's PR was merged outside the loop, so sync_worktree never saw it
    and never closed the goal. The next tick re-picked it, the implementer wrote
    a file identical to the tree, and it would have run forever on finished work.

    Nothing to commit means the change is already there -- by whatever route --
    and this is the only place that can tell.
    """
    goals = tmp_path / "GOALS.md"
    goals.write_text(
        "| id | goal | state |\n|---|---|---|\n| G-CI-1 | fix the guard | open |\n"
    )
    mod = load_loop(tmp_path, monkeypatch)
    assert mod.close_goal("autodev/g-ci-1")["closed"] == "G-CI-1"
    assert "| G-CI-1 | fix the guard | done |" in goals.read_text()


def test_closing_a_goal_wakes_the_loop_for_the_next(tmp_path, monkeypatch):
    """Progress is its own event, and it was the one transition that emitted
    nothing. Closing a goal does not touch the forge, so no webhook fires -- the
    loop went idle holding six open goals and waited for the hourly reconcile."""
    monkeypatch.setenv("AUTODEV_EVENT_DIR", str(tmp_path / "events"))
    goals = tmp_path / "GOALS.md"
    goals.write_text(
        "| id | goal | state |\n|---|---|---|\n"
        "| G-A | done one | done |\n| G-B | still open | open |\n"
    )
    mod = load_loop(tmp_path, monkeypatch)
    assert mod.emit_continue("test")["ok"] is True
    files = list((tmp_path / "events").glob("*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text())["kind"] == "continue"


def test_no_continue_when_the_queue_is_empty(tmp_path, monkeypatch):
    """It cannot spin: with nothing open there is nothing to wake for, and a
    tick that closes nothing emits nothing."""
    monkeypatch.setenv("AUTODEV_EVENT_DIR", str(tmp_path / "events"))
    goals = tmp_path / "GOALS.md"
    goals.write_text("| id | goal | state |\n|---|---|---|\n| G-A | all done | done |\n")
    mod = load_loop(tmp_path, monkeypatch)
    assert mod.emit_continue("test")["skipped"] == "no open goal left"
    assert list((tmp_path / "events").glob("*.json")) == []
