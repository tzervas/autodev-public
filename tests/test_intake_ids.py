"""A taken goal id is not a reason to refuse the goal.

The adoption pass reported a real finding -- shellcheck unconfigured -- the
planner proposed `G-SHELLCHECK-1`, and that id was already in the file from an
earlier round. The row was rejected, adoption queued nothing, and the queue
stayed empty with the finding outstanding. Three retries produced the same id,
because nothing about a retry makes a different one more likely.

Renumbering is mechanical and the tool can do it. That is the difference between
a rule that protects the queue and a rule that blocks it.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "csd-autodev-intake"

EXISTING = (
    "| id | goal | state |\n|---|---|---|\n"
    "| G-SHELLCHECK-1 | done long ago | done |\n"
    "| G-SHELLCHECK-2 | in progress | open |\n"
)


def load() -> Any:
    loader = importlib.machinery.SourceFileLoader("csd_autodev_intake", str(SCRIPT))
    spec = importlib.util.spec_from_loader("csd_autodev_intake", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    ("proposed", "want"),
    [
        ("G-SHELLCHECK-1", "G-SHELLCHECK-3"),
        ("G-SHELLCHECK-2", "G-SHELLCHECK-3"),
        ("G-FRESH-1", "G-FRESH-1"),
        ("G-NOSUFFIX", "G-NOSUFFIX"),
    ],
)
def test_free_id(proposed: str, want: str) -> None:
    """A free id is returned unchanged; a taken one gets the next number."""
    mod = load()
    assert mod.free_id(proposed, EXISTING) == want


def test_a_free_id_is_never_renumbered() -> None:
    """Bumping an id that was fine would break the goal's own references."""
    mod = load()
    assert mod.free_id("G-NEW-1", EXISTING) == "G-NEW-1"


def test_the_collision_check_still_exists() -> None:
    """Renumbering happens before `check`, which must still catch a duplicate.

    If the caller ever stops renumbering, the queue must not silently accept two
    goals sharing an id -- they share a branch, and the second overwrites.
    """
    mod = load()
    errs = mod.check("| G-SHELLCHECK-1 | x | open |", ["a.py"], EXISTING)
    assert any("already exists" in e for e in errs)


LISTING = ["README.md", "scripts/gate", "pyproject.toml"]


def _errs(mod: Any, row: str) -> list[str]:
    return [e for e in mod.check(row, LISTING, "") if "names no file" in e]


def test_a_goal_may_create_a_file_that_does_not_exist_yet() -> None:
    """Requiring an existing name pushed a real finding onto the wrong file.

    The adoption finding said "fix: declare .shellcheckrc". That path does not
    exist, so the planner reached for the nearest existing file mentioning
    shellcheck -- .grok/skills/csd-autodev/SKILL.md -- and the goal would have
    edited documentation instead of adding configuration.
    """
    mod = load()
    row = (
        "| G-A-1 | Change ONLY `.shellcheckrc`. Create it to declare the shell "
        "lint rules, proving `bash scripts/gate` still passes. | open |"
    )
    assert _errs(mod, row) == []


def test_an_invented_file_is_still_refused() -> None:
    """The rule exists because goals invented files; it must still do that."""
    mod = load()
    row = (
        "| G-C-1 | Change ONLY `nonexistent.py`. Fix the bug, proving the "
        "suite passes. | open |"
    )
    assert _errs(mod, row) == ["names no file that exists in the repository"]


def test_an_ordinary_edit_still_names_a_real_file() -> None:
    mod = load()
    row = (
        "| G-B-1 | Change ONLY `README.md`. Correct the section, proving it no "
        "longer mentions `docs/GOALS.md`. | open |"
    )
    assert _errs(mod, row) == []


INVALID_ROWS = (
    "| id | goal | state |\n|---|---|---|\n"
    "| G-CLI-1 | Change ONLY src/csd_client/cli.py. Add `cmd_submit` function "
    "with signature `def cmd_submit(args)` so the operator can hand a brief to "
    "the loop, proving the command appears in --help. blocked: declined 3 times "
    "| invalid |\n"
    "| G-DONE-1 | Change ONLY `docs/HOW-TO.md`. Replace all 10 fleet "
    "identifiers using the mapping table. | done |\n"
)


def test_a_goal_that_repeats_an_invalidated_one_is_refused() -> None:
    """The loop rediscovers its own dead ends.

    Nothing compared a new proposal against goals already marked invalid, so the
    intent-gap pass queued the same "add cmd_submit to src/csd_client/cli.py"
    four times over -- each escalated three times and invalidated, each costing
    a dozen model calls to reach a conclusion already written on the row above.
    """
    mod = load()
    repeat = (
        "Change ONLY src/csd_client/cli.py. Add `cmd_submit` function with "
        "signature `def cmd_submit(args)` so the operator can hand a brief to "
        "the loop, proving the command appears in --help."
    )
    assert mod.dead_end(repeat, INVALID_ROWS) == "G-CLI-1"


def test_a_different_goal_is_allowed() -> None:
    """The check must not stop the queue refilling."""
    mod = load()
    new = "Change ONLY `docs/RUNBOOK.md`. Add a section on stuck worktrees."
    assert mod.dead_end(new, INVALID_ROWS) == ""


def test_resembling_a_DONE_goal_is_not_a_dead_end() -> None:
    """A goal like a finished one is often the legitimate next step.

    "Replace the identifiers in this other file" is the same sentence with a
    different noun, and blocking those would stop the sanitisation programme
    after its first goal.
    """
    mod = load()
    like_done = (
        "Change ONLY `docs/STATE.md`. Replace all 4 fleet identifiers using "
        "the mapping table."
    )
    assert mod.dead_end(like_done, INVALID_ROWS) == ""


def test_the_failure_notes_are_not_part_of_the_comparison() -> None:
    """A stored row carries the loop's annotations; a proposal does not.

    Comparing a bare ask against an ask plus three hundred characters of
    failure notes scores low for the wrong reason.
    """
    mod = load()
    bare = (
        "Change ONLY src/csd_client/cli.py. Add `cmd_submit` function with "
        "signature `def cmd_submit(args)` so the operator can hand a brief to "
        "the loop, proving the command appears in --help."
    )
    annotated = bare + " blocked: declined 3 times invalid because: unachievable"
    assert mod.dead_end(annotated, INVALID_ROWS) == "G-CLI-1"


def test_check_reports_the_dead_end() -> None:
    """The refusal has to say which goal it repeats, or it cannot be acted on."""
    mod = load()
    row = (
        "| G-NEW-1 | Change ONLY src/csd_client/cli.py. Add `cmd_submit` "
        "function with signature `def cmd_submit(args)` so the operator can "
        "hand a brief to the loop, proving the command appears in --help. "
        "| open |"
    )
    errs = mod.check(row, ["src/csd_client/cli.py"], INVALID_ROWS)
    assert any("repeats G-CLI-1" in e for e in errs), errs
