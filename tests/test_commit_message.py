"""A commit should say what happened.

Every autodev commit read `feat(memory): G-X-1 autodev slice`. The scope was
hardcoded to the CSD programme -- "agents" for CogSynDelta, "memory" for
everything else -- so nine documentation changes in this repository were
committed as features of a memory subsystem that does not exist here. The type
was wrong, the scope was wrong, and the subject said "slice", which describes
the pipeline rather than the change.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "csd-autodev-loop"


def load() -> Any:
    loader = importlib.machinery.SourceFileLoader("csd_autodev_loop", str(SCRIPT))
    spec = importlib.util.spec_from_loader("csd_autodev_loop", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    ("path", "subject"),
    [
        ("docs/RUNBOOK.md", "docs: G-X-1"),
        ("docs/goals/nl-router.md", "docs(goals): G-X-1"),
        ("tests/test_x.py", "test: G-X-1"),
        (".github/workflows/ci.yml", "ci(workflows): G-X-1"),
        (".forgejo/workflows/ci.yml", "ci(workflows): G-X-1"),
        ("src/csd_autodev/queue.py", "feat(csd-autodev): G-X-1"),
        ("README.md", "docs: G-X-1"),
        ("pyproject.toml", "build: G-X-1"),
        ("scripts/csd-adopt", "feat(scripts): G-X-1"),
    ],
)
def test_the_subject_describes_the_change(path: str, subject: str) -> None:
    """Type from the kind of file, scope from the component it belongs to."""
    mod = load()
    goal = f"| G-X-1 | Do the thing in `{path}`. | open |"
    assert mod.commit_message(goal, path).splitlines()[0] == subject


def test_a_redundant_scope_is_dropped() -> None:
    """`docs(docs):` and `test(tests):` say the same thing twice."""
    mod = load()
    for path in ("docs/A.md", "tests/test_a.py"):
        subject = mod.commit_message("| G-X-1 | x | open |", path).splitlines()[0]
        assert "(" not in subject, subject


def test_the_body_carries_the_goal() -> None:
    """The log should say what happened without opening the diff."""
    mod = load()
    goal = (
        "| G-SANITISE-RUNBOOK | Change ONLY `docs/RUNBOOK.md`. Replace all 17 "
        "fleet identifiers with the example values from the mapping table "
        "above. Keep every existing section. | open |"
    )
    msg = mod.commit_message(goal, "docs/RUNBOOK.md")
    lines = msg.splitlines()
    assert lines[0] == "docs: G-SANITISE-RUNBOOK"
    assert lines[1] == ""
    assert "Replace all 17" in msg
    assert "| open |" not in msg, "the row's state cell is not part of the message"
    assert all(len(line) <= 72 for line in lines[2:]), "body must be wrapped"


def test_a_goal_that_is_not_a_row_still_works() -> None:
    """Steered goals arrive as bare text, not as a table row."""
    mod = load()
    msg = mod.commit_message("region-pretrain", "src/x/y.py")
    assert msg.splitlines()[0].startswith("feat(x): ")
