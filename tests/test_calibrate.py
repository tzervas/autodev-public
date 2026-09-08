"""Calibration: measurements from traffic, not judgement from three failures.

The budget numbers were picked from three measured collapses plus my estimate.
That was enough to stop them and not enough to call the numbers right.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CAL = ROOT / "scripts" / "csd-budget-calibrate"


def load() -> Any:
    loader = importlib.machinery.SourceFileLoader("csd_budget_calibrate", str(CAL))
    spec = importlib.util.spec_from_loader("csd_budget_calibrate", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def rows(n: int, kind: str = "autodev", **kw) -> list[dict]:
    base = {
        "kind": kind,
        "prompt_chars": 3000,
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "answer_chars": 1500,
        "reasoning_chars": 200,
        "finish_reason": "stop",
        "elapsed_s": 2.0,
    }
    base.update(kw)
    return [dict(base) for _ in range(n)]


def test_chars_per_token_is_measured_not_assumed():
    """The ratio is a property of the tokeniser and the API reports it on every
    call. 3.0 was a pessimistic guess."""
    m = load()
    rep = m.analyse(rows(50, prompt_chars=3500, prompt_tokens=1000), 30)
    assert rep["chars_per_token"]["p50"] == 3.5


def test_the_recommended_ratio_does_not_overshoot():
    """It must not UNDERSHOOT the token count -- an estimate that says a prompt
    is smaller than it is budgets for a prompt that does not exist."""
    m = load()
    mixed = rows(30, prompt_chars=2000, prompt_tokens=1000) + rows(
        30, prompt_chars=6000, prompt_tokens=1000
    )
    rep = m.analyse(mixed, 30)
    assert rep["chars_per_token"]["recommend"] <= rep["chars_per_token"]["p50"]


def test_the_recommendation_is_sized_to_the_large_jobs():
    """A budget sized to the median truncates half the large jobs, and the large
    jobs are the ones worth doing."""
    m = load()
    data = rows(90, completion_tokens=200) + rows(10, completion_tokens=9000)
    rep = m.analyse(data, 30)
    rec = rep["kinds"]["autodev"]["recommend_answer_tokens"]
    assert rec > rep["kinds"]["autodev"]["answer_tokens"]["p50"]
    assert rec >= 9000


def test_too_little_traffic_refuses_to_recommend():
    """A recommendation from a handful of calls is a coincidence with error
    bars, and applying it would look like evidence."""
    m = load()
    rep = m.analyse(rows(5), 30)
    assert rep["enough"] is False
    assert rep["kinds"]["autodev"]["enough"] is False


def test_truncation_and_emptiness_are_counted_separately():
    """Cut off with content and produced nothing are different failures: one is
    resumable, the other means the thinking ate the budget."""
    m = load()
    data = rows(10, finish_reason="length") + rows(10, answer_chars=0) + rows(10)
    k = m.analyse(data, 5)["kinds"]["autodev"]
    assert k["truncated"] == 10
    assert k["empty"] == 10


def test_a_partial_line_does_not_break_the_read(tmp_path):
    """The log is appended from a process that can be killed mid-write, and a
    calibrator that dies on the last line is a calibrator nobody runs."""
    m = load()
    p = tmp_path / "calls.jsonl"
    p.write_text(json.dumps(rows(1)[0]) + "\n" + '{"kind": "autod')
    assert len(m.load(p)) == 1
