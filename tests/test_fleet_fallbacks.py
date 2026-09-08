"""Eight scripts carry a fallback fleet loader. They must all work.

Each defines a `FLEET` class used when `src/` is not on the path, so the script
runs standalone. Eight copies is eight chances to write it wrong, and one WAS
wrong: csd-release's returned the caller's default, so a standalone run
resolved `forge.api` to None and failed later, somewhere else. A fallback that
cannot do the job is not a fallback; it is a delayed failure.

This pins them together. It does not care how each is written -- only that all
of them resolve the committed configuration, which is the one thing they exist
to do.
"""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = sorted(
    p
    for p in (ROOT / "scripts").iterdir()
    if p.is_file() and "class FLEET:" in p.read_text(encoding="utf-8", errors="ignore")
)


def test_every_script_that_needs_a_fallback_has_one() -> None:
    """If this drops to zero the test has stopped testing anything."""
    assert len(SCRIPTS) >= 5, [p.name for p in SCRIPTS]


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_the_fallback_resolves_the_committed_config(
    script: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With src/ unimportable, the fallback must still find fleet.example.json."""
    # Make `from csd_autodev import fleet` fail, so the fallback is what runs.
    monkeypatch.setitem(sys.modules, "csd_autodev", None)

    loader = importlib.machinery.SourceFileLoader(f"probe_{script.name}", str(script))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    # A script whose module body parses arguments exits; the class is defined
    # by then, which is all this needs.
    with contextlib.suppress(SystemExit):
        loader.exec_module(mod)

    got = mod.FLEET.get("forge.api")
    assert got, f"{script.name}: the fallback resolved forge.api to {got!r}"
    assert isinstance(got, str) and got.startswith("http"), got
    # And a key that genuinely is not there still returns the default, rather
    # than raising or inventing something.
    assert mod.FLEET.get("nope.not.here", "fallback") == "fallback"
