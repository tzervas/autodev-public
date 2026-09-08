"""A goal that says what it proves must be held to it.

G-README-CLI-1 said "Prove the section no longer mentions `docs/GOALS.md`". The
change was applied, the goal closed as done, and the README still said
docs/GOALS.md -- the substitution had replaced a line INSIDE a code fence and
left the wrong prose exactly where it was, so the file came out worse than
before.

The goal stated its own success condition in words, and nothing read them.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "csd-autodev-loop"

GOAL = (
    "| G-X-1 | Change ONLY `README.md`. Correct the CLI section. "
    "Prove the section no longer mentions `docs/GOALS.md`. "
    "Prove the README mentions `autodev submit`. | open |"
)


def load() -> Any:
    loader = importlib.machinery.SourceFileLoader("csd_autodev_loop", str(SCRIPT))
    spec = importlib.util.spec_from_loader("csd_autodev_loop", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_claims_are_read_out_of_the_goal() -> None:
    mod = load()
    assert mod.goal_claims(GOAL) == [
        ("absent", "docs/GOALS.md"),
        ("present", "autodev submit"),
    ]


@pytest.mark.parametrize(
    "goal",
    [
        "| G-Y-1 | Add a test for `parse()`. | open |",
        "| G-Y-1 | The README mentions `x`, so change it. | open |",
        "| G-Y-1 | Prove it works. | open |",
    ],
)
def test_a_goal_with_no_checkable_claim_is_unaffected(goal: str) -> None:
    """Narrow on purpose: only 'prove' sentences with a backticked needle.

    A claim this cannot parse is simply not checked, which is where every goal
    already was.
    """
    mod = load()
    assert mod.goal_claims(goal) == []


def test_a_change_that_breaks_its_own_claim_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact failure: applied, closed as done, claim still false."""
    mod = load()
    # A ladder repo, where a file at the root is in scope.
    monkeypatch.setattr(mod, "TARGET_REPO", "o/r")
    doc = tmp_path / "README.md"
    doc.write_text("It reads goals from `docs/GOALS.md`.\n", encoding="utf-8")

    got = mod.apply_proposal(
        {"path": "README.md", "substitutions": [("It reads", "It loads")]},
        tmp_path,
        goal=GOAL,
    )
    assert got["ok"] is False
    assert "does not do what the goal says it proves" in got["error"]
    assert "docs/GOALS.md" in got["error"]
    assert "autodev submit" in got["error"]
    # AND THE FILE IS PUT BACK. A file left in a state the goal denies is worse
    # than no change, and the next attempt should start where this one did.
    assert doc.read_text() == "It reads goals from `docs/GOALS.md`.\n"


def test_a_change_that_keeps_its_claim_is_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard must not block the work it is meant to protect."""
    mod = load()
    monkeypatch.setattr(mod, "TARGET_REPO", "o/r")
    doc = tmp_path / "README.md"
    doc.write_text("It reads goals from `docs/GOALS.md`.\n", encoding="utf-8")

    got = mod.apply_proposal(
        {
            "path": "README.md",
            "substitutions": [
                (
                    "It reads goals from `docs/GOALS.md`.",
                    "Work is handed to it with `autodev submit <file>`.",
                )
            ],
        },
        tmp_path,
        goal=GOAL,
    )
    assert got["ok"] is True
    assert "autodev submit" in doc.read_text()
    assert "docs/GOALS.md" not in doc.read_text()


def test_a_new_file_that_breaks_its_claim_is_removed(tmp_path: Path) -> None:
    """There was nothing there before; leaving a wrong file is not better."""
    mod = load()
    goal = "| G-Z-1 | Create `docs/CLI.md`. Prove it mentions `autodev submit`. | open |"
    got = mod.apply_proposal(
        {"path": "docs/CLI.md", "content": "# CLI\n\nNothing useful.\n"},
        tmp_path,
        goal=goal,
    )
    assert got["ok"] is False
    assert not (tmp_path / "docs" / "CLI.md").exists()
