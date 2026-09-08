"""Two agents, two cards.

The implementer and the helper must be served from DIFFERENT backends. That is
what makes escalation cheap: when the implementer is stuck it asks the helper
what to do differently, and if both live on one card that question either queues
behind the implementer's own work or forces a model swap on the card in use --
so the cheapest moment to get unstuck becomes the most expensive one.

Measured on this fleet before the split, `coder-deep` (implementer) and
`planner` (the escalation target at the time) were both served from
203.0.113.10:8001. Nothing said so.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "csd-gateway-complete"

TWO_CARDS = {
    "data": [
        {"model_name": "coder-deep", "litellm_params": {"api_base": "http://a:8001/v1"}},
        {"model_name": "coder-fast", "litellm_params": {"api_base": "http://b:8000/v1"}},
        {"model_name": "planner", "litellm_params": {"api_base": "http://a:8001/v1"}},
        {"model_name": "reviewer", "litellm_params": {"api_base": "http://b:8000/v1"}},
        {"model_name": "utility", "litellm_params": {"api_base": "http://b:8000/v1"}},
    ]
}


def load() -> Any:
    loader = importlib.machinery.SourceFileLoader("csd_gateway_complete", str(SCRIPT))
    spec = importlib.util.spec_from_loader("csd_gateway_complete", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def stub_gateway(mod: Any, monkeypatch: pytest.MonkeyPatch, payload: dict) -> None:
    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        mod.urllib.request,
        "urlopen",
        lambda *a, **k: Resp(json.dumps(payload).encode()),
    )


def test_the_helper_is_on_the_other_card(monkeypatch: pytest.MonkeyPatch) -> None:
    """The arrangement the design wants."""
    mod = load()
    stub_gateway(mod, monkeypatch, TWO_CARDS)
    rec = mod.backends()
    assert rec["ok"] is True
    assert rec["split"] is True
    assert rec["kinds"]["autodev"]["backend"] != rec["kinds"]["helper"]["backend"]
    assert "warning" not in rec


def test_a_collapsed_split_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """An alias repointed in the gateway must not fail silently.

    The mapping lives outside this repository, so it can change without any
    code change here, and the only symptom would be escalation being slow.
    """
    mod = load()
    monkeypatch.setenv("AUTODEV_MODEL_HELPER", "planner")
    mod = load()  # re-import so KIND_MODEL picks the override up
    stub_gateway(mod, monkeypatch, TWO_CARDS)
    rec = mod.backends()
    assert rec["split"] is False
    assert "SAME backend" in rec["warning"]
    assert "http://a:8001/v1" in rec["warning"]


def test_an_unreachable_gateway_is_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """A check that takes the loop down with it is worse than no check."""
    mod = load()

    def boom(*_a, **_k):
        raise OSError("connection refused")

    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    rec = mod.backends()
    assert rec["ok"] is False
    assert "OSError" in rec["error"]


def test_the_helper_is_not_the_implementer(monkeypatch: pytest.MonkeyPatch) -> None:
    """A helper that is a duplicate gives no second opinion, only a second bill."""
    mod = load()
    assert mod.KIND_MODEL["helper"] != mod.KIND_MODEL["autodev"]
