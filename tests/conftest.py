"""Sever every test from the deployed environment.

WHY THIS EXISTS. Each script in this repo resolves its paths from the
environment at import time, and the deployed wrapper (`deploy/bin/autodev-tick`)
exports the whole set. A test that inherits that environment is not testing the
code, it is reading -- and given the right uid, WRITING -- live production
state.

That is not hypothetical. On 2026-09-08:

  * `test_beat_records_cluster_next_goal_and_need_grok` asserted
    `next_goal == "region-pretrain"` and got the goal row the service was
    actually working, because `AUTODEV_TARGET_REPO` was set (which makes
    `next_open_goal` skip steer entirely) and `CSD_GOALS` pointed at the live
    queue.
  * `test_steer_post_region_pretrain` died on `PermissionError` stat-ing
    `/home/svc-autodev/state/PHASE-1.md` through the lab console's `PHASE1`.

Both passed for a developer and failed for the service, which meant the gate
failed at BASELINE and autodev could not work this repository at all. The suite
was green in CI and red in the only place it mattered.

The rule is deny-by-default rather than a maintained list of known-dangerous
names: a variable added next month is isolated without anyone remembering to
come back here. A test that wants a setting sets it itself -- `monkeypatch`
inside the test runs after this fixture, so it wins.
"""

from __future__ import annotations

import os

import pytest

# The prefixes that address this system's own state, secrets and services.
OWNED_PREFIXES = ("CSD_", "AUTODEV_")

# Deliberately empty. If something ever has to survive, name it here WITH the
# reason -- an unexplained entry re-opens exactly the hole described above.
KEEP: frozenset[str] = frozenset()


@pytest.fixture(autouse=True)
def _isolate_from_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every deployment variable before each test."""
    for name in list(os.environ):
        if name.startswith(OWNED_PREFIXES) and name not in KEEP:
            monkeypatch.delenv(name, raising=False)
