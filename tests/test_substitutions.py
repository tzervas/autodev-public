"""Say what to change, rather than restating the file.

"Replace 8 identifiers in a 272-line document" was asked for as a whole-file
rewrite, and the model returned a 28-line SUMMARY of the document -- three
attempts running, each caught by the shrink guard. That is the guard working and
the protocol being wrong: a model asked to re-emit a long file it must not
otherwise change will drift, and no amount of instruction reliably stops it.

Here the loop does the editing, so content the pairs do not name cannot be lost.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "csd-autodev-loop"

REPLY = """SUBSTITUTIONS: docs/EVENT-DRIVEN.md
```
203.0.113.10 => 203.0.113.10
git.example.com => git.example.com
```
"""


def load() -> Any:
    loader = importlib.machinery.SourceFileLoader("csd_autodev_loop", str(SCRIPT))
    spec = importlib.util.spec_from_loader("csd_autodev_loop", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_a_substitution_reply_parses() -> None:
    mod = load()
    got = mod.extract_json(REPLY)
    assert got["path"] == "docs/EVENT-DRIVEN.md"
    assert got["substitutions"] == [
        ("203.0.113.10", "203.0.113.10"),
        ("git.example.com", "git.example.com"),
    ]


def test_a_file_proposal_still_parses() -> None:
    """The new form must not capture the old one."""
    mod = load()
    got = mod.extract_json('PATH: src/x.py\n```python\nprint("hi")\n```\n')
    assert got == {"path": "src/x.py", "content": 'print("hi")\n'}


def test_substituting_keeps_everything_else(tmp_path: Path) -> None:
    """The whole point: 272 lines in, 272 lines out, 2 strings changed."""
    mod = load()
    (tmp_path / "docs").mkdir()
    doc = tmp_path / "docs" / "EVENT-DRIVEN.md"
    body = "\n".join(
        [f"line {i}" for i in range(135)]
        + ["the receiver is at 203.0.113.10 and the forge at git.example.com"]
        + [f"line {i}" for i in range(136)]
    )
    doc.write_text(body, encoding="utf-8")

    got = mod.apply_proposal(mod.extract_json(REPLY), tmp_path, goal="Substitute.")
    assert got["ok"] is True
    after = doc.read_text()
    assert len(after.splitlines()) == len(body.splitlines())
    assert "203.0.113.10" in after and "git.example.com" in after
    assert "203.0.113.10" not in after and "git.example.com" not in after
    assert "line 0" in after and "line 135" in after


def test_an_unmatched_pair_is_reported_not_fatal(tmp_path: Path) -> None:
    """These goals share one mapping table across many files.

    A table row that does not occur in THIS file is not a false claim, and
    refusing the whole change for it blocked a correct 7-of-8 substitution.
    What must not happen is the miss passing unnoticed, so it is reported.
    """
    mod = load()
    (tmp_path / "docs").mkdir()
    doc = tmp_path / "docs" / "EVENT-DRIVEN.md"
    doc.write_text("only 203.0.113.10 here\n", encoding="utf-8")

    got = mod.apply_proposal(mod.extract_json(REPLY), tmp_path, goal="Substitute.")
    assert got["ok"] is True
    assert doc.read_text() == "only 203.0.113.10 here\n"
    assert got["substituted"] == [{"from": "203.0.113.10", "to": "203.0.113.10", "n": 1}]


def test_when_nothing_matches_it_is_refused(tmp_path: Path) -> None:
    """No match at all means the model quoted text the file does not contain."""
    mod = load()
    (tmp_path / "docs").mkdir()
    doc = tmp_path / "docs" / "EVENT-DRIVEN.md"
    doc.write_text("nothing to see\n", encoding="utf-8")

    got = mod.apply_proposal(mod.extract_json(REPLY), tmp_path, goal="Substitute.")
    assert got["ok"] is False
    assert "no substitution matched anything" in got["error"]
    assert doc.read_text() == "nothing to see\n"


def test_substituting_into_a_missing_file_is_refused(tmp_path: Path) -> None:
    """There is nothing to substitute in, and creating one would lose the goal."""
    mod = load()
    got = mod.apply_proposal(mod.extract_json(REPLY), tmp_path, goal="Substitute.")
    assert got["ok"] is False
    assert "no such file" in got["error"]


def test_a_no_op_substitution_is_refused(tmp_path: Path) -> None:
    """Matching but changing nothing is not a change; it must not open a PR."""
    mod = load()
    (tmp_path / "docs").mkdir()
    doc = tmp_path / "docs" / "A.md"
    doc.write_text("x\n", encoding="utf-8")
    got = mod.apply_proposal(
        {"path": "docs/A.md", "substitutions": [("x", "x")]},
        tmp_path,
        goal="Substitute.",
    )
    assert got["ok"] is False
    assert "changed nothing" in got["error"]


@pytest.mark.parametrize(
    "line", ["# a comment", "", "no separator here", "   => empty left"]
)
def test_junk_lines_are_ignored_not_applied(tmp_path: Path, line: str) -> None:
    """A comment or a blank must not become a substitution pair."""
    mod = load()
    got = mod.extract_json(f"SUBSTITUTIONS: docs/A.md\n```\n{line}\na => b\n```\n")
    assert got["substitutions"] == [("a", "b")]


SUB_GOAL = (
    "| G-S-1 | Change ONLY `docs/A.md`. Replace all 8 fleet identifiers "
    "using the mapping table. | open |"
)
CODE_GOAL = (
    "| G-T-1 | In `tests/test_x.py`, add tests for `parse()`. "
    "Prove it rejects an empty input. | open |"
)


def test_a_large_existing_file_is_edited_in_place(tmp_path: Path) -> None:
    """The general rule, of which the wording list is a special case.

    A model asked to re-emit a long file it must not otherwise change will
    drift: an 83-line document came back byte-identical three times, and a
    272-line one came back as a 28-line summary. Neither goal used a word from
    the substitution list.
    """
    mod = load()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "BIG.md").write_text("\n".join(f"line {i}" for i in range(90)))
    (tmp_path / "docs" / "SMALL.md").write_text("a\nb\nc\n")

    big = "| G-1 | Rewrite the Details section of `docs/BIG.md`. | open |"
    assert mod.is_substitution_goal(big, tmp_path) is True
    small = "| G-2 | Change ONLY `docs/SMALL.md`. Reword the intro. | open |"
    assert mod.is_substitution_goal(small, tmp_path) is False


def test_creating_a_file_is_never_an_edit(tmp_path: Path) -> None:
    """Nothing to preserve, and nothing to quote a FROM side from."""
    mod = load()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "NEW.md").write_text("\n".join("x" for _ in range(200)))
    goal = "| G-3 | Create `docs/NEW.md`. It does not exist yet. | open |"
    assert mod.is_substitution_goal(goal, tmp_path) is False


def test_wording_wins_regardless_of_size(tmp_path: Path) -> None:
    """A stated substitution is one even in a three-line file."""
    mod = load()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "T.md").write_text("a\nb\n")
    goal = "| G-4 | Change ONLY `docs/T.md`. Replace all 2 identifiers. | open |"
    assert mod.is_substitution_goal(goal, tmp_path) is True


def test_a_substitution_goal_is_recognised() -> None:
    """Offering both forms and letting the model choose did not work.

    Asked to replace 8 identifiers in a 272-line document with both protocols
    available, it chose the whole-file form and returned a 28-line summary --
    every attempt. Detecting the intent is deterministic; hoping is not.
    """
    mod = load()
    assert mod.is_substitution_goal(SUB_GOAL) is True
    assert mod.is_substitution_goal(CODE_GOAL) is False


def test_the_brief_asks_for_the_form_the_goal_needs(monkeypatch) -> None:
    """A substitution goal must never be shown the whole-file form."""
    mod = load()
    monkeypatch.setattr(mod, "TARGET_REPO", "o/r")
    monkeypatch.setattr(mod, "repo_context", lambda *a, **k: "")
    seen: list[str] = []

    def fake_run(argv, **_k):
        if "--prompt" in argv:
            seen.append(argv[argv.index("--prompt") + 1])
        return 0, "NOTE: no"

    monkeypatch.setattr(mod, "_run", fake_run)

    mod.implement(SUB_GOAL)
    assert "SUBSTITUTIONS: <repo-relative path>" in seen[0]
    assert "COMPLETE new contents" not in seen[0], "the wrong form was offered"

    seen.clear()
    mod.implement(CODE_GOAL)
    assert "COMPLETE new contents" in seen[0]
    assert "SUBSTITUTIONS:" not in seen[0]


BLOCK_REPLY = """SUBSTITUTIONS: docs/ARCHITECTURE.md
```
<<<FROM
- `shrink_check` — Verifies that the change does not introduce bloat.
  It ensures the diff is minimal.
===TO
- `shrink_check` — Refuses a whole-file write that deletes most of a file.
>>>
203.0.113.10 => 203.0.113.10
```
"""


def test_a_block_replaces_a_whole_passage() -> None:
    """Editing one SECTION is neither a whole-file rewrite nor a token swap.

    Having only those two is why G-ARCH-GUARDS kept failing: asked to correct
    five paragraphs in an 83-line file, the model re-emitted the file unchanged
    three times running, because it had no way to say what it meant.
    """
    mod = load()
    got = mod.extract_json(BLOCK_REPLY)
    assert got["path"] == "docs/ARCHITECTURE.md"
    subs = dict(got["substitutions"])
    frm = next(k for k in subs if "introduce bloat" in k)
    assert "diff is minimal" in frm, "the whole passage, not just its first line"
    assert "deletes most of a file" in subs[frm]


def test_block_and_line_forms_mix() -> None:
    """One reply may need both; neither may swallow the other."""
    mod = load()
    subs = dict(mod.extract_json(BLOCK_REPLY)["substitutions"])
    assert subs.get("203.0.113.10") == "203.0.113.10"
    assert len(subs) == 2


def test_a_block_edit_keeps_the_rest_of_the_file(tmp_path: Path) -> None:
    """The guarantee that makes this safe: only the quoted passage moves."""
    mod = load()
    (tmp_path / "docs").mkdir()
    doc = tmp_path / "docs" / "ARCHITECTURE.md"
    body = (
        "# Title\n\nkeep me\n\n"
        "- `shrink_check` — Verifies that the change does not introduce bloat.\n"
        "  It ensures the diff is minimal.\n"
        "\nkeep me too, at 203.0.113.10\n"
    )
    doc.write_text(body, encoding="utf-8")

    got = mod.apply_proposal(mod.extract_json(BLOCK_REPLY), tmp_path, goal="Correct it.")
    assert got["ok"] is True
    after = doc.read_text()
    assert "deletes most of a file" in after
    assert "introduce bloat" not in after
    assert "keep me" in after and "keep me too" in after
    assert "203.0.113.10" in after


def test_an_unterminated_block_is_still_read(tmp_path: Path) -> None:
    """A reply cut off mid-block must not silently drop the pair it had.

    The FROM side is complete by then; dropping it turns a truncated reply into
    a no-op, which is the failure that is hardest to see.
    """
    mod = load()
    got = mod.extract_json(
        "SUBSTITUTIONS: docs/A.md\n```\n<<<FROM\nold\n===TO\nnew\n```\n"
    )
    assert got["substitutions"] == [("old\n", "new\n")]


ARROW_ON_ITS_OWN_LINE = """SUBSTITUTIONS: README.md
```
This command starts the loop. It reads goals from `docs/GOALS.md`,
and manages state.
=> This command requires a subcommand. Work is handed to it via
`autodev submit <file>`.

Goals are stored in `docs/GOALS.md`.
=> Goals live in the agent's state directory.
```
"""


def test_the_form_the_model_actually_produces() -> None:
    """Given a multi-line passage and a one-line grammar, the model put the
    arrow on its own line between two multi-line blocks.

    That is a sensible reading of the instruction. It parsed as nothing, the
    reply was rejected as unparseable, and the identical prompt was retried
    nine times before the goal was invalidated. Being strict about a format the
    model cannot verify only moves the failure somewhere harder to see.
    """
    mod = load()
    got = mod.extract_json(ARROW_ON_ITS_OWN_LINE)
    subs = got["substitutions"]
    assert len(subs) == 2
    assert "starts the loop" in subs[0][0]
    assert "requires a subcommand" in subs[0][1]
    assert subs[1] == (
        "Goals are stored in `docs/GOALS.md`.",
        "Goals live in the agent's state directory.",
    )


def test_the_one_line_form_still_parses() -> None:
    """Accepting a second form must not break the first."""
    mod = load()
    got = mod.extract_json("SUBSTITUTIONS: a.md\n```\nfoo => bar\nbaz => qux\n```\n")
    assert got["substitutions"] == [("foo", "bar"), ("baz", "qux")]


@pytest.mark.parametrize(
    ("reply", "expect"),
    [
        ("SUBSTITUTIONS: a.md\n```\nno pairs at all\n```\n", "pairs could not be read"),
        ("```python\nprint(1)\n```\n", "no `PATH: <file>` line"),
        ("   ", "was empty"),
    ],
)
def test_a_parse_error_says_what_looked_wrong(reply: str, expect: str) -> None:
    """ "Could not parse" told the next attempt nothing, so the next attempt
    made the same mistake -- nine times, before the goal was invalidated for
    failing nine times."""
    mod = load()
    assert expect in mod.extract_json(reply)["_parse_error"]
