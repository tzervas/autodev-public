"""Parsing a whole-file proposal, and refusing to delete a file by accident.

Both defects here landed a real PR. G-SANITISE-RUNBOOK asked the model to
substitute identifiers in docs/RUNBOOK.md; the reply was a complete markdown
document, the parser cut it at the document's own first ```bash fence, and the
loop wrote the 11 lines that survived over a 250-line file. Documentation is not
covered by the test suite, so the gate went green and a PR opened proposing the
deletion of 239 lines.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "csd-autodev-loop"

# The shape that broke it: a markdown file whose content contains fences.
RUNBOOK_REPLY = """PATH: docs/RUNBOOK.md
```markdown
# Autodev runbook

## Everyday commands

```bash
sudo systemctl start autodev-tick.service
```

See `EVENT-DRIVEN.md` for the design.

## Reading a tick

```json
{"ok": true}
```

That is the whole document.
```
"""


def load() -> Any:
    loader = importlib.machinery.SourceFileLoader("csd_autodev_loop", str(SCRIPT))
    spec = importlib.util.spec_from_loader("csd_autodev_loop", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_nested_fences_do_not_truncate_the_file() -> None:
    """The outer fence closes at the END, not at the document's first block."""
    mod = load()
    got = mod.extract_json(RUNBOOK_REPLY)
    assert got.get("path") == "docs/RUNBOOK.md"
    body = got["content"]
    assert "That is the whole document." in body, "cut at an inner fence"
    assert "```bash" in body and "```json" in body, "inner fences must survive"
    assert body.count("```") == 4


def test_a_simple_block_still_parses() -> None:
    """The common case must not regress while fixing the nested one."""
    mod = load()
    got = mod.extract_json('PATH: src/x.py\n```python\nprint("hi")\n```\n')
    assert got == {"path": "src/x.py", "content": 'print("hi")\n'}


def test_an_unclosed_fence_is_a_parse_error_not_half_a_file() -> None:
    """A cut-off reply must not be written: half a file is worse than none."""
    mod = load()
    got = mod.extract_json("PATH: docs/X.md\n```markdown\n# Title\n\nsome text\n")
    assert "content" not in got
    assert "cut off" in got["_parse_error"]


def test_a_bare_opener_stops_at_its_first_balanced_close() -> None:
    """The one case the depth rule cannot resolve, and why it errs this way.

    Depth is tracked using the asymmetry that an OPENING fence may carry an info
    string and a closing fence is bare. When the content's own inner fence is
    also bare there is no signal left, so the first balanced close wins and
    anything after it is dropped.

    That is the deliberate choice: a model adding a sentence of prose after the
    block is routine, and treating the last fence as the close would swallow it
    into the file every time. The residual risk -- content holding an unlabelled
    ``` block -- is what shrink_check is for.
    """
    mod = load()
    body = mod.fenced_block("```\nkept\n```\nprose the model added\n")
    assert body == "kept\n"


def test_an_unbalanced_depth_falls_back_to_the_last_bare_fence() -> None:
    """More openers than closers: close at the outermost bare fence there is.

    The reply is malformed either way, so the goal is to keep as much of the
    file as the text supports while still ending at a fence -- never to run to
    the end of the reply and sweep the model's trailing prose into the file.
    """
    mod = load()
    body = mod.fenced_block("```markdown\na\n```bash\nb\n```json\nc\n```\nend\n")
    assert body is not None
    assert "```bash" in body and "```json" in body
    assert "end" not in body


def test_a_write_that_drops_most_of_a_file_is_refused(tmp_path: Path) -> None:
    """The independent guard: the parser will be wrong again some day."""
    mod = load()
    target = tmp_path / "docs"
    target.mkdir()
    (target / "RUNBOOK.md").write_text("\n".join(f"line {i}" for i in range(250)))
    got = mod.apply_proposal(
        {"path": "docs/RUNBOOK.md", "content": "# Autodev runbook\n"},
        tmp_path,
        goal="Change ONLY `docs/RUNBOOK.md`. Replace all 17 fleet identifiers.",
    )
    assert got["ok"] is False
    assert "refusing to shrink" in got["error"]
    assert got["shrink"]["before"] == 250
    # And the file on disk is untouched.
    assert len((target / "RUNBOOK.md").read_text().splitlines()) == 250


def test_a_deletion_the_goal_asks_for_is_allowed(tmp_path: Path) -> None:
    """The guard must not block intended work -- that would be a new deadlock."""
    mod = load()
    (tmp_path / "docs").mkdir()
    big = tmp_path / "docs" / "OLD.md"
    big.write_text("\n".join(f"line {i}" for i in range(250)))
    got = mod.apply_proposal(
        {"path": "docs/OLD.md", "content": "# Replaced\n"},
        tmp_path,
        goal="Truncate `docs/OLD.md` down to a pointer at the new document.",
    )
    assert got["ok"] is True
    assert big.read_text() == "# Replaced\n"


def test_a_new_file_and_a_small_file_are_not_shrink_checked(tmp_path: Path) -> None:
    """No baseline to compare, and short files shrink for ordinary reasons."""
    mod = load()
    (tmp_path / "docs").mkdir()
    got = mod.apply_proposal(
        {"path": "docs/NEW.md", "content": "# New\n"}, tmp_path, goal="Add a doc."
    )
    assert got["ok"] is True

    small = tmp_path / "docs" / "SMALL.md"
    small.write_text("\n".join(f"line {i}" for i in range(10)))
    got = mod.apply_proposal(
        {"path": "docs/SMALL.md", "content": "# Small\n"}, tmp_path, goal="Rework it."
    )
    assert got["ok"] is True


@pytest.mark.parametrize("frac", [0.5, 0.8, 1.0, 1.5])
def test_a_write_that_keeps_enough_is_allowed(tmp_path: Path, frac: float) -> None:
    """At or above the keep fraction the write goes through."""
    mod = load()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "D.md").write_text("\n".join("x" for _ in range(100)))
    n = int(100 * frac)
    got = mod.apply_proposal(
        {"path": "docs/D.md", "content": "\n".join("y" for _ in range(n))},
        tmp_path,
        goal="Edit it.",
    )
    assert got["ok"] is True
