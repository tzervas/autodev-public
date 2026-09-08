"""The operator's side of the loop.

Submitting work must need nothing but write access to one directory. The
operator has no gateway credential and should not have one -- the agent holds
its own, in its own vault, which is the point of the identity split -- so a
brief is HANDED OVER and the loop decomposes it.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

CLI = Path(__file__).resolve().parents[1] / "scripts" / "autodev"
LOOP = Path(__file__).resolve().parents[1] / "scripts" / "csd-autodev-loop"


def load(script: Path, name: str) -> Any:
    loader = importlib.machinery.SourceFileLoader(name, str(script))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("AUTODEV_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("AUTODEV_LADDER", str(tmp_path / "ladder.json"))
    monkeypatch.setenv("AUTODEV_EVENT_DIR", str(tmp_path / "events"))
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "ladder.json").write_text(
        json.dumps({"current": "o/r", "rungs": [{"repo": "o/r"}, {"repo": "o/s"}]}),
        encoding="utf-8",
    )
    return load(CLI, "autodev_cli")


# --------------------------------------------------------------------------- #
# rows
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("line", "want"),
    [
        ("| G-A-1 | Do a thing. | open |", "| G-A-1 | Do a thing. | open |"),
        # Four cells: models add columns, and the extra one is part of the goal.
        (
            "| G-A-1 | Do a thing | open | `x.md` must mention it |",
            "| G-A-1 | Do a thing. `x.md` must mention it | open |",
        ),
        ("| G-A-1 | Finished. | done |", "| G-A-1 | Finished. | done |"),
        ("| not-an-id | text | open |", ""),
        ("just prose", ""),
        ("| G-A-1 | | open |", ""),
    ],
)
def test_row_normalisation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, line: str, want: str
) -> None:
    """Asked for three cells the planner produced four, and a strict parser
    would have rejected four good goals on the operator's first use."""
    mod = cli(tmp_path, monkeypatch)
    assert mod.normalise_row(line) == want


# --------------------------------------------------------------------------- #
# submit
# --------------------------------------------------------------------------- #
def test_a_decomposed_plan_is_appended_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plan the operator decomposed is not improved by a model rewriting it."""
    mod = cli(tmp_path, monkeypatch)
    goals = tmp_path / "state" / "GOALS-r.md"
    goals.write_text("| id | goal | state |\n|---|---|---|\n", encoding="utf-8")
    brief = tmp_path / "plan.md"
    brief.write_text(
        "My plan:\n"
        "| G-X-1 | Change `a.py`. | open |\n"
        "| G-X-2 | Change `b.py`. | open |\n",
        encoding="utf-8",
    )
    rc = mod.main(["submit", str(brief), "--yes"])
    assert rc == 0
    text = goals.read_text()
    assert "| G-X-1 | Change `a.py`. | open |" in text
    assert "| G-X-2 | Change `b.py`. | open |" in text


def test_a_prose_brief_is_handed_to_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No credential needed on this side: the agent decomposes it."""
    mod = cli(tmp_path, monkeypatch)
    brief = tmp_path / "idea.md"
    brief.write_text("Make the thing discoverable. It is not.\n", encoding="utf-8")
    assert mod.main(["submit", str(brief), "--yes"]) == 0

    dropped = list((tmp_path / "state" / "briefs").glob("*.md"))
    assert len(dropped) == 1
    assert "Make the thing discoverable" in dropped[0].read_text()
    # And the loop was woken rather than left to find it on the hourly net.
    kinds = [
        json.loads(p.read_text())["kind"] for p in (tmp_path / "events").glob("*.json")
    ]
    assert "continue" in kinds


def test_a_clashing_id_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two goals sharing an id share a branch, and the second overwrites."""
    mod = cli(tmp_path, monkeypatch)
    goals = tmp_path / "state" / "GOALS-r.md"
    goals.write_text("| G-X-1 | Already here. | open |\n", encoding="utf-8")
    brief = tmp_path / "plan.md"
    brief.write_text("| G-X-1 | Change `a.py`. | open |\n", encoding="utf-8")
    assert mod.main(["submit", str(brief), "--yes"]) == 1
    assert "Change `a.py`" not in goals.read_text(), "nothing may be appended"


def test_target_refuses_a_repo_that_is_not_a_rung(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ladder is the source of truth; the CLI must not invent a rung."""
    mod = cli(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as e:
        mod.main(["target", "o/nope"])
    assert "not a rung" in str(e.value)
    assert mod.main(["target", "o/s"]) == 0
    assert json.loads((tmp_path / "ladder.json").read_text())["current"] == "o/s"


def test_pause_and_resume_write_the_steer_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = cli(tmp_path, monkeypatch)
    assert mod.main(["pause", "reviewing #12"]) == 0
    steer = json.loads((tmp_path / "state" / "steer.json").read_text())
    assert steer["pause"] is True and steer["note"] == "reviewing #12"
    assert mod.main(["resume"]) == 0
    assert json.loads((tmp_path / "state" / "steer.json").read_text())["pause"] is False


# --------------------------------------------------------------------------- #
# the loop's side
# --------------------------------------------------------------------------- #
def test_the_loop_decomposes_a_dropped_brief(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half: what the CLI drops, the loop picks up."""
    monkeypatch.setenv("CSD_GOALS", str(tmp_path / "GOALS.md"))
    monkeypatch.setenv("AUTODEV_BRIEFS", str(tmp_path / "briefs"))
    monkeypatch.setenv("AUTODEV_EVENT_DIR", str(tmp_path / "events"))
    mod = load(LOOP, "csd_autodev_loop")
    monkeypatch.setattr(mod, "TARGET_REPO", "o/r")
    (tmp_path / "GOALS.md").write_text("| G-OLD-1 | Existing. | open |\n", "utf-8")
    (tmp_path / "briefs").mkdir()
    (tmp_path / "briefs" / "a.md").write_text("Do something useful.\n", "utf-8")

    monkeypatch.setattr(
        mod,
        "_run",
        lambda *_a, **_k: (
            0,
            "| G-NEW-1 | Change `a.py`. | open |\n| G-OLD-1 | dupe | open |\n",
        ),
    )
    rec = mod.intake_briefs()
    assert rec["goals_added"] == 1, "an id already queued must not be re-added"
    text = (tmp_path / "GOALS.md").read_text()
    assert "| G-NEW-1 | Change `a.py`. | open |" in text
    assert text.count("G-OLD-1") == 1
    # Filed away, so the next tick does not decompose it again.
    assert not list((tmp_path / "briefs").glob("*.md"))
    assert (tmp_path / "briefs" / "done" / "a.md").is_file()


def test_a_brief_that_cannot_be_decomposed_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting it would lose the only copy of the operator's intent."""
    monkeypatch.setenv("CSD_GOALS", str(tmp_path / "GOALS.md"))
    monkeypatch.setenv("AUTODEV_BRIEFS", str(tmp_path / "briefs"))
    mod = load(LOOP, "csd_autodev_loop")
    monkeypatch.setattr(mod, "TARGET_REPO", "o/r")
    (tmp_path / "briefs").mkdir()
    (tmp_path / "briefs" / "a.md").write_text("...\n", "utf-8")
    monkeypatch.setattr(mod, "_run", lambda *_a, **_k: (0, "I cannot do that."))

    rec = mod.intake_briefs()
    assert rec["goals_added"] == 0
    assert (tmp_path / "briefs" / "a.md").is_file(), "the brief must survive"


def test_every_path_comes_from_one_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """STATE read the fleet config and EVENTS did not.

    So `autodev submit` on a real deployment wrote the brief to the right
    directory and then tried to wake the loop in /var/lib/autodev/events -- the
    documentation-safe default, which does not exist there. It reported the
    failure honestly and fell back to the hourly net, which is the correct
    behaviour for a wrong answer, and still the wrong answer.
    """
    for var in ("AUTODEV_STATE", "AUTODEV_EVENT_DIR", "AUTODEV_LADDER"):
        monkeypatch.delenv(var, raising=False)
    mod = load(CLI, "autodev_cli_paths")
    # Both resolve through the same helper, so neither can drift from the other.
    state = mod._cfg("paths.state", "AUTODEV_STATE", "/unused")
    events = mod._cfg("events.dir", "AUTODEV_EVENT_DIR", "/unused")
    assert (state, events) == (mod.STATE, mod.EVENTS)


def test_the_environment_still_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A unit pins a value by setting it; the config is the default, not a law."""
    monkeypatch.setenv("AUTODEV_EVENT_DIR", str(tmp_path / "ev"))
    mod = load(CLI, "autodev_cli_env")
    want = tmp_path / "ev"
    assert want == mod.EVENTS


def test_a_missing_config_falls_back_rather_than_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI must run on a machine with no harness installed."""
    import sys

    monkeypatch.setitem(sys.modules, "csd_autodev", None)
    for var in ("AUTODEV_STATE", "AUTODEV_EVENT_DIR"):
        monkeypatch.delenv(var, raising=False)
    mod = load(CLI, "autodev_cli_nofleet")
    assert str(mod.STATE).endswith("/state")
    assert str(mod.EVENTS).endswith("/events")


def test_watch_follows_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Watch" means watch. It used to need -f, so the bare command printed a
    header and exited, which reads as "there is nothing here" whether or not
    that is true."""
    mod = cli(tmp_path, monkeypatch)
    seen: list[list[str]] = []

    class FakeProc:
        stdout = iter(['{"ok": true, "goal": "| G-A-1 | x | open |"}\n'])

        def terminate(self) -> None:
            return None

    monkeypatch.setattr(mod, "can_read_journal", lambda: True)
    monkeypatch.setattr(
        mod.subprocess, "Popen", lambda argv, **_k: seen.append(argv) or FakeProc()
    )
    mod.main(["watch", "-n", "5"])
    assert "-f" in seen[0], "watch must follow unless told otherwise"

    seen.clear()
    mod.main(["watch", "--once", "-n", "5"])
    assert "-f" not in seen[0], "--once must not follow"


def test_watch_explains_an_empty_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """journalctl exits 0 and prints NOTHING for a user outside the group.

    That is indistinguishable from "the loop has not ticked", so watch printed
    its header and exited and the operator could not tell which had happened.
    """
    mod = cli(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "can_read_journal", lambda: False)
    rc = mod.main(["watch", "--once"])
    out = capsys.readouterr().out
    assert rc == 1, "an unreadable journal is not success"
    assert "systemd-journal" in out
    assert "usermod -aG" in out, "say how to fix it, not just what is wrong"


def test_watch_distinguishes_no_ticks_from_no_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Two reasons for an empty screen; they need different advice."""
    mod = cli(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "can_read_journal", lambda: True)

    class Empty:
        stdout = iter([])

        def terminate(self) -> None:
            return None

    monkeypatch.setattr(mod.subprocess, "Popen", lambda *_a, **_k: Empty())
    assert mod.main(["watch", "--once"]) == 1
    out = capsys.readouterr().out
    assert "No ticks in the journal yet" in out
    assert "autodev run" in out
    assert "usermod" not in out, "do not accuse a correctly configured account"


def test_the_group_check_looks_at_the_primary_gid_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`sg systemd-journal -c ...` makes it the PRIMARY group, not a
    supplementary one, so checking only getgroups() told a correctly-configured
    operator they were not in the group."""
    mod = cli(tmp_path, monkeypatch)
    import grp

    class FakeGroup:
        gr_gid = 4242

    monkeypatch.setattr(grp, "getgrnam", lambda _n: FakeGroup())
    monkeypatch.setattr(mod.os, "geteuid", lambda: 1000)

    monkeypatch.setattr(mod.os, "getgid", lambda: 4242)
    monkeypatch.setattr(mod.os, "getgroups", lambda: [100])
    assert mod.can_read_journal() is True, "primary gid counts"

    monkeypatch.setattr(mod.os, "getgid", lambda: 100)
    monkeypatch.setattr(mod.os, "getgroups", lambda: [4242])
    assert mod.can_read_journal() is True, "supplementary gid counts"

    monkeypatch.setattr(mod.os, "getgroups", lambda: [100])
    assert mod.can_read_journal() is False


def test_an_unknown_group_does_not_accuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a system with no such group, say nothing rather than blame the user."""
    mod = cli(tmp_path, monkeypatch)
    import grp

    monkeypatch.setattr(mod.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(grp, "getgrnam", lambda _n: (_ for _ in ()).throw(KeyError()))
    assert mod.can_read_journal() is True
