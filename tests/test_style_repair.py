"""Style repair: the role that must never turn a formatting nit into a blocker.

This script had no tests. It is the one role whose contract is "apply the fix
and say nothing", so the way it fails is the quietest of any of them: it either
reports a repair as a blocking finding -- halting the pipeline on formatting,
which is the thing it exists to prevent -- or it reports a tool it never ran as
clean.

pre-commit is the case that makes both easy to get wrong, because it exits
non-zero for "a hook repaired a file" AND for "a hook found something it cannot
repair". Every other entry in TOOLS is a single tool whose non-zero fix-mode
exit means only the second.

`subprocess.run` and `shutil.which` are replaced, so these assert on the
DECISION rather than on which formatters happen to be installed on the host.
Requiring a real pre-commit would make the suite skip in exactly the
environment where the role matters least being tested.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "csd-style-repair"


def load() -> Any:
    loader = importlib.machinery.SourceFileLoader("csd_style_repair", str(SCRIPT))
    spec = importlib.util.spec_from_loader("csd_style_repair", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class FakeRun:
    """Replays a scripted exit code per argv[0], recording every call."""

    def __init__(self, results: dict[str, list[int]]):
        self.results = {k: list(v) for k, v in results.items()}
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        exe = cmd[0]
        if exe == "git":
            # git_dirty(): a clean tree, so `repaired` stays empty and the
            # assertions below are about exit codes rather than about which
            # files a fake formatter pretended to touch.
            return subprocess.CompletedProcess(cmd, 0, "", "")
        queue = self.results.get(exe, [0])
        rc = queue.pop(0) if queue else 0
        return subprocess.CompletedProcess(cmd, rc, f"{exe} said something", "")


def run_main(mod, monkeypatch, repo: Path, fake: FakeRun, argv: list[str]):
    monkeypatch.setattr(mod.shutil, "which", lambda exe: f"/usr/bin/{exe}")
    monkeypatch.setattr(mod.subprocess, "run", fake)
    monkeypatch.setattr("sys.argv", ["csd-style-repair", str(repo), *argv])
    code = mod.main()
    return code


def precommit_repo(tmp_path: Path) -> Path:
    (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")
    return tmp_path


def ruff_repo(tmp_path: Path) -> Path:
    """A repo that has opted into ruff's linter AND its formatter, as this one has.

    Both sections are required and that is deliberate: `configures()` asks for
    the tool's OWN section, because `[tool.ruff]` says the repo lints with ruff
    and does not say it wants `ruff format`, which is a different tool with an
    opinionated style. A fixture carrying only `[tool.ruff]` therefore runs
    nothing, which is the documented behaviour rather than a bug.
    """
    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff]\nline-length = 90\n\n[tool.ruff.format]\n\n[tool.ruff.lint]\n",
        "utf-8",
    )
    return tmp_path


# --------------------------------------------------------------------------- #
# The exit code that decides whether the pipeline stops
# --------------------------------------------------------------------------- #
def test_a_hook_that_repaired_a_file_is_not_an_unfixable_finding(
    tmp_path, monkeypatch, capsys
):
    """pre-commit exits 1 after fixing something. That is a SUCCESS.

    Reading it as a finding would file every successful repair as a blocker and
    halt the pipeline on formatting -- the exact failure this role exists to
    prevent. The second pass is what separates them: the repairs from the first
    are already applied, so a clean re-run means the first exit was repairs.
    """
    mod = load()
    fake = FakeRun({"pre-commit": [1, 0]})  # fixed, then clean
    code = run_main(mod, monkeypatch, precommit_repo(tmp_path), fake, ["--json"])

    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["ok"] is True
    assert out["unfixable"] == []
    assert [c[0] for c in fake.calls if c[0] == "pre-commit"] == [
        "pre-commit",
        "pre-commit",
    ], "the second pass is what makes the verdict, so it must actually run"


def test_a_hook_that_still_fails_after_repairing_is_a_finding(
    tmp_path, monkeypatch, capsys
):
    """Both at once: some files repaired, something left that a tool cannot fix.

    This is why the re-run decides it and not a before/after diff of the dirty
    set. Files DID change, so "did anything change" would call this a clean
    repair and drop a real finding on the floor.
    """
    mod = load()
    fake = FakeRun({"pre-commit": [1, 1]})  # fixed some, still failing
    code = run_main(mod, monkeypatch, precommit_repo(tmp_path), fake, ["--json"])

    out = json.loads(capsys.readouterr().out)
    assert code == 1
    assert out["ok"] is False
    assert [u["tool"] for u in out["unfixable"]] == ["pre-commit"]
    assert out["ran"][0]["rerun_rc"] == 1


def test_check_mode_does_not_rerun(tmp_path, monkeypatch, capsys):
    """--check asserts, it does not repair, so there is nothing to settle.

    A second pass would report the same failure twice and, worse, imply the
    first one had applied something.
    """
    mod = load()
    fake = FakeRun({"pre-commit": [1, 0]})
    code = run_main(
        mod, monkeypatch, precommit_repo(tmp_path), fake, ["--json", "--check"]
    )

    out = json.loads(capsys.readouterr().out)
    assert code == 1
    assert out["ok"] is False
    assert "rerun_rc" not in out["ran"][0]
    assert sum(1 for c in fake.calls if c[0] == "pre-commit") == 1


# --------------------------------------------------------------------------- #
# Subsumption: two definitions of correct is how a repo goes green in one place
# --------------------------------------------------------------------------- #
def test_precommit_subsumes_the_hardcoded_tools(tmp_path, monkeypatch, capsys):
    """A repo with BOTH must not have ruff run twice at two pinned versions.

    pre-commit pins its own ruff; the repo's pyproject configures whichever is
    on PATH. Running both is two definitions of correctness in one pass, and
    they disagree -- measured on this tree, where the installed ruff formats
    differently from the one CI pins.
    """
    mod = load()
    repo = ruff_repo(precommit_repo(tmp_path))
    fake = FakeRun({"pre-commit": [0]})
    code = run_main(mod, monkeypatch, repo, fake, ["--json"])

    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert [r["tool"] for r in out["ran"]] == ["pre-commit"]
    assert {s["tool"] for s in out["skipped"]} >= {"ruff-format", "ruff-lint"}
    assert all(s["why"] == "pre-commit runs it" for s in out["skipped"])
    assert not any(c[0] == "ruff" for c in fake.calls)


def test_without_a_config_the_hardcoded_tools_still_run(tmp_path, monkeypatch, capsys):
    """Subsumption is opt-in. A repo that has not adopted pre-commit is unaffected.

    The ladder points this role at repositories that have never heard of it, so
    the table stays the fallback rather than becoming a requirement.
    """
    mod = load()
    fake = FakeRun({"ruff": [0, 0]})
    code = run_main(mod, monkeypatch, ruff_repo(tmp_path), fake, ["--json"])

    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert [r["tool"] for r in out["ran"]] == ["ruff-format", "ruff-lint"]
    assert "pre-commit" in {s["tool"] for s in out["skipped"]}
    assert not any(c[0] == "pre-commit" for c in fake.calls)


# --------------------------------------------------------------------------- #
# Absent is not clean
# --------------------------------------------------------------------------- #
def test_an_uninstalled_precommit_is_skipped_not_passed(tmp_path, monkeypatch, capsys):
    """A declared tool that is not on PATH is `skipped`, and it does not subsume.

    Reporting an absent formatter as clean is how a repo drifts while its gate
    says nothing is wrong -- and if it subsumed anyway, declaring pre-commit
    without installing it would silently disable style entirely.
    """
    mod = load()
    repo = ruff_repo(precommit_repo(tmp_path))
    fake = FakeRun({"ruff": [0, 0]})
    monkeypatch.setattr(
        mod.shutil,
        "which",
        lambda exe: None if exe == "pre-commit" else f"/usr/bin/{exe}",
    )
    monkeypatch.setattr(mod.subprocess, "run", fake)
    monkeypatch.setattr("sys.argv", ["csd-style-repair", str(repo), "--json"])
    code = mod.main()

    out = json.loads(capsys.readouterr().out)
    assert code == 0
    why = {s["tool"]: s["why"] for s in out["skipped"]}
    assert why["pre-commit"] == "pre-commit not installed"
    assert [r["tool"] for r in out["ran"]] == ["ruff-format", "ruff-lint"]
