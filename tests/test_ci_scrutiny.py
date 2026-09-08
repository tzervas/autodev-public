"""csd-ci-scrutiny is the loop's merge guard, so its two ways of being wrong
both matter: a false CLEAN merges an unproven commit, and a false SUSPECT
deadlocks the loop against a repository it can never merge into.

The false SUSPECT is the one that actually happened. Forgejo builds a status
context from the workflow's declared `name:` -- "CI / Repo gate
(pull_request)" -- while the matcher compared the FILENAME stem, "ci". Those
differ by case here and by more than case in general, so a workflow that ran
and passed was reported as "produced NO run at all" and a green commit came
back NOT mergeable.

These drive the real `scrutinise` with a stubbed forge, because the bug was in
the agreement between two of its parts and a test that re-implements the
matching locally would have passed throughout.
"""

from __future__ import annotations

import base64
import importlib.machinery
import importlib.util
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "csd-ci-scrutiny"

CI_YML = """name: CI

"on":
  pull_request:
  push:
    branches: [main]

jobs:
  gate:
    name: Repo gate
    runs-on: [self-hosted]
    steps:
      - run: bash scripts/gate
"""

RELEASE_YML = """name: Release
"on":
  push:
    branches: [main]
jobs:
  cut:
    name: Cut
    runs-on: [self-hosted]
    steps:
      - run: true
"""


def load() -> Any:
    loader = importlib.machinery.SourceFileLoader("csd_ci_scrutiny", str(SCRIPT))
    spec = importlib.util.spec_from_loader("csd_ci_scrutiny", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def stub_forge(
    mod: Any,
    monkeypatch: pytest.MonkeyPatch,
    workflows: dict[str, str],
    statuses: list[dict[str, str]],
) -> None:
    """Answer the three calls scrutinise makes, and 404 everything else."""

    def fake_api(path: str) -> tuple[int, Any]:
        if "/statuses" in path:
            return 200, statuses
        if "/contents/.github/workflows?" in path:
            return 200, [{"name": n} for n in workflows]
        for name, body in workflows.items():
            if f"/contents/.github/workflows/{name}?" in path:
                return 200, {"content": base64.b64encode(body.encode()).decode()}
        return 404, {}

    monkeypatch.setattr(mod, "api", fake_api)


def test_workflow_name_reads_the_declared_name() -> None:
    """The context prefix comes from `name:`, never from the filename."""
    mod = load()
    assert mod.workflow_name(CI_YML) == "CI"
    assert mod.workflow_name('name: "Build and test"\n') == "Build and test"
    assert mod.workflow_name("jobs:\n  a:\n    runs-on: x\n") == ""


def test_green_run_is_clean_though_name_and_filename_differ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression: ci.yml declares `name: CI`, so "CI / ..." is its run."""
    mod = load()
    stub_forge(
        mod,
        monkeypatch,
        {"ci.yml": CI_YML},
        [{"context": "CI / Repo gate (pull_request)", "status": "success"}],
    )
    rep = mod.scrutinise("o/r", "deadbeef")
    assert rep["verdict"] == "CLEAN", rep["findings"]
    assert rep["workflows_with_no_run"] == []


def test_a_workflow_that_never_ran_is_still_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard's purpose. Loosening the match must not blind it."""
    mod = load()
    stub_forge(
        mod,
        monkeypatch,
        {"ci.yml": CI_YML, "release.yml": RELEASE_YML},
        [{"context": "CI / Repo gate (pull_request)", "status": "success"}],
    )
    rep = mod.scrutinise("o/r", "deadbeef")
    assert rep["verdict"] == "SUSPECT"
    assert rep["workflows_with_no_run"] == ["release.yml"]


def test_a_failed_check_is_a_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """A red check outranks everything else."""
    mod = load()
    stub_forge(
        mod,
        monkeypatch,
        {"ci.yml": CI_YML},
        [{"context": "CI / Repo gate (pull_request)", "status": "failure"}],
    )
    rep = mod.scrutinise("o/r", "deadbeef")
    assert rep["verdict"] == "FAIL"
    assert rep["failed"] == ["CI / Repo gate (pull_request)"]


def test_no_checks_at_all_is_suspect(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unchecked commit is not a passing one."""
    mod = load()
    stub_forge(mod, monkeypatch, {"ci.yml": CI_YML}, [])
    rep = mod.scrutinise("o/r", "deadbeef")
    assert rep["verdict"] == "SUSPECT"


def test_declared_workflows_identifiers_are_lowercased(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Case-insensitivity is the property; assert it rather than a symptom."""
    mod = load()
    stub_forge(mod, monkeypatch, {"ci.yml": CI_YML}, [])
    got = mod.declared_workflows("o/r", "deadbeef")
    assert got == {"ci.yml": {"ci.yml", "ci"}}
    assert all(i == i.lower() for i in got["ci.yml"])
