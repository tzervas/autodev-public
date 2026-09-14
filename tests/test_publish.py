"""csd-publish: no original may survive, and no two originals may collapse.

The script already proved the first half -- it greps the published tree for
every ORIGINAL identifier and refuses to finish if one is still there. It
proved nothing about the second half: whether the substitution TABLE itself
maps two different originals onto the same replacement. That gap is not
hypothetical, it is the repo's own history. Two real GPU host addresses were
both rewritten to `203.0.113.99`; `scripts/csd-lab-console` merged that into
`{HOST_GPU_B_IP, "203.0.113.99", "203.0.113.99"}` -- a set that reads as three
elements and holds two, so the authorization check it guards silently stopped
excluding one of the hosts it was written to name. Nothing raised, nothing
printed a warning: the tree published clean by the only check that existed.

`SUBSTITUTIONS` and `SUBSTITUTION_RULES` are built by `load_rules()` at
MODULE IMPORT time, from `config/fleet.json` or `$AUTODEV_FLEET_CONFIG` --
never from an already-imported module's globals. Every test here therefore
sets the environment variable and only THEN imports a fresh copy of the
script; `tests/conftest.py` strips `AUTODEV_*` from the environment in an
autouse fixture that runs before the test body, so it is `monkeypatch.setenv`
inside the test that makes the value stick, not anything inherited.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "csd-publish"


def load_publish(monkeypatch, fleet: Path) -> Any:
    """Point AUTODEV_FLEET_CONFIG at `fleet`, then import a fresh module.

    The env var must be set BEFORE `exec_module` runs, because `load_rules()`
    is called at module scope, not lazily -- a module imported first and
    pointed at the config second would carry the wrong (or no) rules forever.
    """
    monkeypatch.setenv("AUTODEV_FLEET_CONFIG", str(fleet))
    loader = importlib.machinery.SourceFileLoader("csd_publish", str(SCRIPT))
    spec = importlib.util.spec_from_loader("csd_publish", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def write_fleet(
    tmp_path: Path,
    substitutions: list[dict],
    forbidden: list[str] | None = None,
) -> Path:
    """A minimal private fleet config, shaped the way `load_rules()` reads it."""
    fleet = tmp_path / "fleet.json"
    fleet.write_text(
        json.dumps(
            {
                "publish": {
                    "substitutions": substitutions,
                    "forbidden": forbidden or [],
                }
            }
        ),
        encoding="utf-8",
    )
    return fleet


def make_repo(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    """A real (if minimal) git repo, so `git ls-files` inside `main()` works.

    `git add` populates the index without needing `commit` -- and commit
    needs a configured identity this sandbox has no business assuming --
    so `git ls-files` is exercised the same way a real publish would see it.
    """
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    for rel, body in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        subprocess.run(["git", "add", rel], cwd=repo, check=True)
    return repo


# --------------------------------------------------------------------------- #
# 1. Two distinct patterns sharing a replacement are refused, both named.
# --------------------------------------------------------------------------- #
def test_two_distinct_patterns_sharing_a_replacement_are_refused(
    tmp_path, monkeypatch, capsys
):
    """The exact shape of the corruption: HOST_GPU_A and HOST_GPU_B -> one IP."""
    fleet = write_fleet(
        tmp_path,
        substitutions=[
            {"pattern": "host-gpu-a", "replacement": "203.0.113.99"},
            {"pattern": "host-gpu-b", "replacement": "203.0.113.99"},
        ],
    )
    mod = load_publish(monkeypatch, fleet)

    dest = tmp_path / "dest"
    monkeypatch.setattr(
        sys, "argv", ["csd-publish", str(tmp_path / "does-not-matter"), str(dest)]
    )
    rc = mod.main()
    out = capsys.readouterr().out

    assert rc == 1
    assert "host-gpu-a" in out
    assert "host-gpu-b" in out
    assert "203.0.113.99" in out
    # Fail-closed means refusing before touching a filesystem, not after.
    assert not dest.exists()


# find_collisions() takes no config of its own -- only the rules it is
# handed -- so it is exercised directly here, independent of main()'s wiring.
def test_find_collisions_unit(monkeypatch, tmp_path):
    mod = load_publish(monkeypatch, write_fleet(tmp_path, []))
    got = mod.find_collisions(
        [
            {"pattern": "a", "replacement": "z"},
            {"pattern": "b", "replacement": "z"},
        ]
    )
    assert got == [("z", [("a", False), ("b", False)])]


def test_a_marked_rule_does_not_shield_an_unmarked_one(monkeypatch, tmp_path):
    """The exact probe that sank the first version of this guard.

    `shared_replacement` exempts a REPLACEMENT only when EVERY rule reaching
    it is marked. The first `find_collisions` skipped marked rules before
    grouping, so a correctly-marked `GPU-B` left no trace for `gpu-c` to
    collide with -- `gpu-c` was "alone" in its group and passed silently,
    even though it landed on the exact same replacement as an existing,
    real, marked pattern. That is the corruption this whole guard exists to
    catch (two originals merged into one placeholder), reached through the
    escape hatch meant to prevent it.
    """
    mod = load_publish(monkeypatch, write_fleet(tmp_path, []))
    got = mod.find_collisions(
        [
            {"pattern": "GPU-B", "replacement": "host-b", "shared_replacement": True},
            {"pattern": "gpu-c", "replacement": "host-b"},
        ]
    )
    assert got == [("host-b", [("GPU-B", True), ("gpu-c", False)])]


# --------------------------------------------------------------------------- #
# 2. The same table, fully covered by shared_replacement, is allowed.
# --------------------------------------------------------------------------- #
def test_shared_replacement_on_every_rule_exempts_the_collision(
    tmp_path, monkeypatch, capsys
):
    """Case variants of one hostname legitimately collapse -- ALL of them marked.

    Marking only one of the two patterns is exactly the probe above and must
    still be refused; the exemption only applies once every pattern reaching
    the replacement carries the flag.
    """
    fleet = write_fleet(
        tmp_path,
        substitutions=[
            {
                "pattern": "HOST-A",
                "replacement": "host.example.com",
                "shared_replacement": True,
            },
            {
                "pattern": "host-a",
                "replacement": "host.example.com",
                "shared_replacement": True,
            },
        ],
    )
    mod = load_publish(monkeypatch, fleet)
    assert mod.find_collisions(mod.SUBSTITUTION_RULES) == []

    # And the guard in main() does not block the run over it: a nonexistent
    # src still fails, but on the ordinary "not a git repo" usage error (2),
    # never on the collision refusal (1).
    monkeypatch.setattr(
        sys,
        "argv",
        ["csd-publish", str(tmp_path / "no-such-repo"), str(tmp_path / "dest")],
    )
    rc = mod.main()
    out = capsys.readouterr().out
    assert rc == 2
    assert "SANITISER FAILED" not in out
    assert "collisions" not in out


def test_one_marked_rule_out_of_two_still_refuses_via_main(tmp_path, monkeypatch, capsys):
    """The probe end-to-end: main() refuses, and names the unmarked pattern."""
    fleet = write_fleet(
        tmp_path,
        substitutions=[
            {"pattern": "GPU-B", "replacement": "host-b", "shared_replacement": True},
            {"pattern": "gpu-c", "replacement": "host-b"},
        ],
    )
    mod = load_publish(monkeypatch, fleet)
    dest = tmp_path / "dest"
    monkeypatch.setattr(
        sys, "argv", ["csd-publish", str(tmp_path / "does-not-matter"), str(dest)]
    )
    rc = mod.main()
    out = capsys.readouterr().out

    assert rc == 1
    assert "gpu-c" in out
    assert "host-b" in out
    assert not dest.exists()


# --------------------------------------------------------------------------- #
# 3. A table with no collision is unaffected -- the ordinary run succeeds.
# --------------------------------------------------------------------------- #
def test_a_table_with_no_collision_publishes_cleanly(tmp_path, monkeypatch, capsys):
    fleet = write_fleet(
        tmp_path,
        substitutions=[
            {"pattern": "10\\.9\\.9\\.1", "replacement": "203.0.113.10"},
            {"pattern": "10\\.9\\.9\\.2", "replacement": "203.0.113.11"},
        ],
        forbidden=["10\\.9\\.9\\.1", "10\\.9\\.9\\.2"],
    )
    mod = load_publish(monkeypatch, fleet)

    src = make_repo(
        tmp_path,
        "src",
        {"notes.txt": "primary 10.9.9.1, secondary 10.9.9.2\n"},
    )
    dest = tmp_path / "dest"
    monkeypatch.setattr(sys, "argv", ["csd-publish", str(src), str(dest)])
    rc = mod.main()
    out = capsys.readouterr().out

    assert rc == 0
    assert "SANITISER FAILED" not in out
    assert "verified: no original identifier survives" in out
    published = (dest / "notes.txt").read_text(encoding="utf-8")
    assert "203.0.113.10" in published and "203.0.113.11" in published
    assert "10.9.9.1" not in published and "10.9.9.2" not in published


# --------------------------------------------------------------------------- #
# 4. The existing "an original survived" refusal still works.
# --------------------------------------------------------------------------- #
def test_an_original_that_survives_is_still_refused(tmp_path, monkeypatch, capsys):
    """The collision guard must not crowd out, or stand in for, this check."""
    fleet = write_fleet(
        tmp_path,
        # No substitution matches the file's content at all, so the original
        # rides straight through into the published tree untouched.
        substitutions=[{"pattern": "10\\.9\\.9\\.1", "replacement": "203.0.113.10"}],
        forbidden=["10\\.4\\.4\\.4"],
    )
    mod = load_publish(monkeypatch, fleet)
    assert mod.find_collisions(mod.SUBSTITUTION_RULES) == []

    src = make_repo(
        tmp_path,
        "src",
        {"notes.txt": "unrewritten real host 10.4.4.4\n"},
    )
    dest = tmp_path / "dest"
    monkeypatch.setattr(sys, "argv", ["csd-publish", str(src), str(dest)])
    rc = mod.main()
    out = capsys.readouterr().out

    assert rc == 1
    assert "identifier(s) survived" in out
    assert "10.4.4.4" in out
    assert "collisions" not in out
