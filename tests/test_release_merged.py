"""A release is cut from a branch; the checks run against a working tree.

Those are the same thing only when the tree is clean and merged, and on
2026-09-08 they were not. The agent had `.shellcheckrc` as an unpushed local
commit on a feature branch, `adopted` passed because the file was on disk, and
the rung reported READY for a release that would have shipped without it.

A gate that passes on work nobody else can see is not a gate.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "csd-release"


def load() -> Any:
    loader = importlib.machinery.SourceFileLoader("csd_release", str(SCRIPT))
    spec = importlib.util.spec_from_loader("csd_release", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def a_repo(tmp_path: Path) -> Path:
    """A repo with an `origin` that has main, so ahead/behind is meaningful."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git("init", "-q", "-b", "main", cwd=origin)
    (origin / "a.txt").write_text("one\n")
    git("add", "-A", cwd=origin)
    git("-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "one", cwd=origin)

    work = tmp_path / "work"
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True
    )
    return work


def test_a_clean_merged_tree_has_nothing_unreleased(tmp_path: Path) -> None:
    mod = load()
    assert mod.unreleased_work(a_repo(tmp_path)) == []


def test_uncommitted_changes_are_reported(tmp_path: Path) -> None:
    mod = load()
    work = a_repo(tmp_path)
    (work / "a.txt").write_text("two\n")
    got = mod.unreleased_work(work)
    assert any("uncommitted" in g for g in got), got


def test_an_unpushed_commit_is_reported(tmp_path: Path) -> None:
    """The exact failure: the file was on disk and on no branch anyone shares."""
    mod = load()
    work = a_repo(tmp_path)
    (work / ".shellcheckrc").write_text("disable=SC1090\n")
    git("add", "-A", cwd=work)
    git("-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "add", cwd=work)
    got = mod.unreleased_work(work)
    assert any("not on origin/main" in g for g in got), got


def test_being_on_a_feature_branch_is_reported(tmp_path: Path) -> None:
    mod = load()
    work = a_repo(tmp_path)
    git("checkout", "-q", "-b", "autodev/g-x", cwd=work)
    got = mod.unreleased_work(work)
    assert any("not 'main'" in g for g in got), got


def test_an_already_released_tree_is_not_released_again(
    monkeypatch: Any,
) -> None:
    """A release needs something to release.

    Nothing checked, so every pass of the gate cut another version from the same
    tree: v0.1.2, v0.1.3 and v0.1.4 all point at one commit. The ladder runs
    csd-release on every adoption tick, so an idle repository would have
    accumulated a version per tick, each telling a consumer that something
    changed.
    """
    mod = load()
    head = "1783642cbc9f07ba55e1d65eada6accdf50ce86a"
    tags = [
        {"name": "v0.1.2", "commit": {"sha": head}},
        {"name": "v0.1.1", "commit": {"sha": "6b218718f885aaaaaaaaaaaaaaaaaaaaaaaaaaaa"}},
    ]
    monkeypatch.setattr(mod, "api", lambda *_a, **_k: (200, tags))

    assert mod.already_released("o/r", "t", head) == "v0.1.2"
    # A tree nobody has tagged is releasable.
    assert mod.already_released("o/r", "t", "deadbeefdeadbeefdead") == ""


def test_a_forge_that_will_not_answer_does_not_block_a_release(
    monkeypatch: Any,
) -> None:
    """Unknown must not mean "already released", or nothing ever ships."""
    mod = load()
    monkeypatch.setattr(mod, "api", lambda *_a, **_k: (500, None))
    assert mod.already_released("o/r", "t", "deadbeef") == ""
