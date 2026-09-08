"""When a goal is about five functions, send five functions -- not the file.

A goal saying "read `scripts/csd-autodev-loop` -- the functions X, Y and Z --
and state accurately what each guard prevents" named a 114 KB file against a
24 KB context budget. The file was truncated to its first quarter, none of the
named functions were in it, and the model described all five from their NAMES.
Four were wrong and one was backwards: `shrink_check`, which refuses a write
that DELETES most of a file, was documented as preventing bloat.

Truncation is honest and the loop labels it. The defect is that a whole file is
the wrong unit when the goal is about what is inside it.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
from typing import Any

MODULE = '''"""A module."""

CONSTANT = 3


def alpha(x):
    """First."""
    return x + CONSTANT


def beta(y):
    """Second."""
    return y * 2


class Gamma:
    """Third."""

    def method(self):
        return 1
'''

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "csd-autodev-loop"


def load() -> Any:
    loader = importlib.machinery.SourceFileLoader("csd_autodev_loop", str(SCRIPT))
    spec = importlib.util.spec_from_loader("csd_autodev_loop", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_named_symbols_reads_backticked_identifiers() -> None:
    """A path and a version are not symbols; a function name is."""
    mod = load()
    goal = (
        "Change ONLY `docs/A.md`. Read the functions `alpha`, `beta()` and "
        "`shrink_check` in `src/x.py`. Do not touch `CONSTANT` or `203.0.113.1`."
    )
    got = mod.named_symbols(goal)
    assert "alpha" in got and "beta" in got and "shrink_check" in got
    assert "CONSTANT" not in got, "constants are not what the goal is asking about"
    assert not any("/" in g or "." in g for g in got), "paths are not symbols"


def test_the_named_functions_are_sent_whole(tmp_path: Path) -> None:
    """The point: their source, verbatim, so nothing is described from a name."""
    mod = load()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.py").write_text(MODULE, encoding="utf-8")
    out = mod.symbol_source(tmp_path, ["src/x.py"], ["alpha", "Gamma"], 8000)
    assert "def alpha(x):" in out
    assert "return x + CONSTANT" in out
    assert "class Gamma:" in out
    assert "def beta" not in out, "only what the goal named"
    assert "not from their names" in out


def test_nothing_named_costs_nothing(tmp_path: Path) -> None:
    """Most goals name no symbols and must not pay for this."""
    mod = load()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.py").write_text(MODULE, encoding="utf-8")
    assert mod.symbol_source(tmp_path, ["src/x.py"], [], 8000) == ""
    assert mod.symbol_source(tmp_path, ["src/x.py"], ["nosuchthing"], 8000) == ""


def test_the_budget_is_respected(tmp_path: Path) -> None:
    """A symbol that does not fit is dropped, not truncated.

    Half a function invites the same invention as half a file.
    """
    mod = load()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.py").write_text(MODULE, encoding="utf-8")
    out = mod.symbol_source(tmp_path, ["src/x.py"], ["alpha", "beta"], 40)
    assert "def beta" not in out or "def alpha" not in out
    assert "return" not in out or out.count("def ") <= 1


def test_an_unparsable_file_is_skipped(tmp_path: Path) -> None:
    """A syntax error somewhere must not take the whole prompt down."""
    mod = load()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "bad.py").write_text("def (:\n", encoding="utf-8")
    (tmp_path / "src" / "x.py").write_text(MODULE, encoding="utf-8")
    out = mod.symbol_source(tmp_path, ["src/bad.py", "src/x.py"], ["alpha"], 8000)
    assert "def alpha(x):" in out


def test_an_extensionless_script_can_be_named_in_a_goal(tmp_path: Path) -> None:
    """Every script in this repository is extensionless.

    The path matcher required a known extension, so a goal saying "read
    `scripts/autodev`" produced a context with no scripts/autodev in it. The
    model said so plainly -- "the file is not included in the provided
    repository files list" -- and the goal was invalidated for asking the
    impossible, six times over.

    The same blind spot as ruff walking only *.py, one layer up: a rule written
    around extensions in a tree that does not use them.
    """
    mod = load()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "autodev").write_text(
        "#!/usr/bin/env python3\ndef main():\n    return 0\n", encoding="utf-8"
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "A.md").write_text("# A\n", encoding="utf-8")

    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)

    ctx = mod.repo_context(tmp_path, "Read `scripts/autodev` and `docs/A.md`.", 20000)
    assert "def main():" in ctx, "the extensionless script must be quoted"
    assert "# A" in ctx, "and files WITH extensions must still work"


def test_a_path_the_repo_does_not_have_is_not_invented(tmp_path: Path) -> None:
    """The listing is the authority, so a loose pattern costs nothing."""
    mod = load()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "A.md").write_text("# A\n", encoding="utf-8")
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)

    ctx = mod.repo_context(tmp_path, "Create `docs/NEW.md` from `docs/A.md`.", 20000)
    assert "--- docs/A.md" in ctx or "# A" in ctx
    assert "--- docs/NEW.md" not in ctx, "a file that does not exist is not quoted"
