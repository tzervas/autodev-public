"""The mailbox and the role registry: durable handoffs, and roles as data.

These replace two things that were implicit. Handoffs lived in heartbeat.json --
one dict, rewritten every tick -- and roles lived in environment variables, which
made "which model is the reviewer" a question you answered by reading a shell
script, and made the property the review gate depends on unenforceable.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAIL = ROOT / "scripts" / "csd-agent-mail"
ROLES = ROOT / "config" / "roles.json"


def load_mail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("AUTODEV_MAIL_DIR", str(tmp_path / "mail"))
    loader = importlib.machinery.SourceFileLoader("csd_agent_mail", str(MAIL))
    spec = importlib.util.spec_from_loader("csd_agent_mail", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def run(tmp_path: Path, *args: str) -> tuple[int, str]:
    p = subprocess.run(
        [sys.executable, str(MAIL), *args],
        capture_output=True,
        text=True,
        env={"AUTODEV_MAIL_DIR": str(tmp_path / "mail"), "PATH": "/usr/bin:/bin"},
    )
    return p.returncode, (p.stdout or "") + (p.stderr or "")


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #
def test_the_registry_is_valid_and_declares_the_unbuilt_roles():
    """Roles that do not exist yet are DECLARED, not silently absent, so the gap
    between the four that run and the twelve in the design is visible."""
    d = json.loads(ROLES.read_text())
    roles = d["roles"]
    assert len(roles) >= 12
    built = {k for k, v in roles.items() if v.get("implemented")}
    assert {"implementer", "reviewer", "planner", "analyst"} <= built
    for name, r in roles.items():
        assert "owns" in r, name
        assert "may_change" in r, name
        assert isinstance(r["may_change"], list), name


def test_the_reviewer_is_declared_independent_of_the_implementer():
    """The one property the review gate rests on. It is enforced at call time by
    asking the gateway, and declared here so it cannot be quietly dropped."""
    d = json.loads(ROLES.read_text())
    assert d["roles"]["reviewer"]["must_differ_from"] == "implementer"
    assert d["roles"]["reviewer"]["model"] != d["roles"]["implementer"]["model"]


def test_a_role_that_owns_nothing_may_change_nothing():
    """analyst and reviewer report; they do not edit. A reviewer that can write
    is a reviewer that can fix its own objection and approve it."""
    d = json.loads(ROLES.read_text())
    for name in ("analyst", "reviewer", "ideator"):
        assert d["roles"][name]["may_change"] == [], name


def test_every_defect_route_names_a_real_role():
    """A finding routed to a role that does not exist is a finding nobody
    receives -- and routing to the wrong layer is the failure this table
    exists to prevent."""
    d = json.loads(ROLES.read_text())
    names = set(d["roles"]) | {"operator"}
    for key, target in d["defect_routing"].items():
        if key.startswith("_"):
            continue
        assert target in names, f"{key} -> {target}"


def test_every_gate_has_an_owning_role():
    """A gate nobody owns is a gate nobody fixes."""
    d = json.loads(ROLES.read_text())
    names = set(d["roles"]) | {"operator"}
    for g in d["gates"]:
        assert g["owner"] in names, g


def test_the_escalation_ladder_changes_strategy_before_giving_up():
    """Repeating an identical strategy is not persistence, it is a loop. The
    ladder must contain a step that deliberately changes something."""
    d = json.loads(ROLES.read_text())
    ladder = d["escalation_ladder"]
    assert "change_approach" in ladder
    assert "alternate_model_or_gpu" in ladder
    assert ladder.index("retry") < ladder.index("change_approach")
    assert ladder.index("change_approach") < ladder.index("alternate_model_or_gpu")


# --------------------------------------------------------------------------- #
# The mailbox
# --------------------------------------------------------------------------- #
def test_a_message_survives_until_its_recipient_claims_it(tmp_path):
    """The whole point. heartbeat.json is one dict rewritten every tick, so a
    handoff survives only if the next tick happens to copy it forward."""
    rc, _ = run(
        tmp_path,
        "send",
        "--to",
        "architect",
        "--from",
        "implementer",
        "--task",
        "G-X",
        "--request",
        "the interface is ambiguous",
    )
    assert rc == 0
    for _ in range(3):
        rc, out = run(tmp_path, "inbox", "--for", "architect", "--json")
        assert rc == 0
        assert len(json.loads(out)) == 1


def test_only_one_agent_can_claim_a_message(tmp_path):
    """Two agents claiming one handoff is two agents doing the same corrective
    work, and one of them pushing over the other."""
    run(tmp_path, "send", "--to", "architect", "--from", "implementer", "--request", "x")
    rc1, _ = run(tmp_path, "claim", "--for", "architect")
    rc2, out2 = run(tmp_path, "claim", "--for", "architect")
    assert rc1 == 0
    assert rc2 == 1
    assert json.loads(out2)["empty"] is True


def test_a_message_to_an_unknown_role_is_refused(tmp_path):
    """It would sit in the directory looking like pending work that nobody is
    addressed to read."""
    rc, out = run(
        tmp_path, "send", "--to", "nobody", "--from", "implementer", "--request", "x"
    )
    assert rc == 2
    assert "unknown role" in out


def test_the_envelope_carries_what_escalation_needs(tmp_path):
    """Handing a stuck problem on is only useful if the ATTEMPTS and EVIDENCE
    travel with it -- otherwise the next agent starts from zero and repeats
    them."""
    run(
        tmp_path,
        "send",
        "--to",
        "architect",
        "--from",
        "implementer",
        "--task",
        "G-CLASSIFY-2",
        "--request",
        "raise or default?",
        "--blocking",
        "--attempt",
        "raised ValueError",
        "--evidence",
        "max() arg is an empty sequence",
        "--expects",
        "a decision",
    )
    _, out = run(tmp_path, "inbox", "--for", "architect", "--json")
    m = json.loads(out)[0]
    assert m["attempts"] == ["raised ValueError"]
    assert m["evidence"] == ["max() arg is an empty sequence"]
    assert m["blocking"] is True
    assert m["expects"] == "a decision"


def test_closing_keeps_the_message(tmp_path):
    """Nothing is deleted, so a failed handoff is reconstructable afterwards."""
    _, out = run(
        tmp_path, "send", "--to", "architect", "--from", "implementer", "--request", "x"
    )
    mid = json.loads(out)["id"]
    run(tmp_path, "claim", "--for", "architect")
    rc, _ = run(tmp_path, "close", mid, "--state", "failed", "--note", "wrong layer")
    assert rc == 0
    _, out = run(tmp_path, "inbox", "--state", "failed", "--json")
    rows = json.loads(out)
    assert rows[0]["id"] == mid
    assert rows[0]["close_note"] == "wrong layer"
