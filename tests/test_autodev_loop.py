"""Autodev loop routes region-pretrain onto CogSynDelta, not memory-gate."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "csd-autodev-loop"


def load_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Import csd-autodev-loop against tmp_path, never the deployed state.

    Production loop always uses HTTP for steer/runs. Tests inject file-backed
    doubles so unit tests do not need a live lab server.
    """
    # Point the file-backed globals somewhere writable and empty rather than
    # leaving them on their /fleet-data defaults, which may exist on this host.
    monkeypatch.setenv("CSD_STEER", str(tmp_path / "csd-steer.json"))
    monkeypatch.setenv("CSD_AUTODEV_OUT", str(tmp_path / "autodev-out"))
    monkeypatch.setenv("CSD_AUTODEV_HEARTBEAT", str(tmp_path / "heartbeat.json"))
    monkeypatch.setenv("CSD_GOALS", str(tmp_path / "GOALS.md"))
    monkeypatch.setenv("CSD_PHASE1_BOARD", str(tmp_path / "PHASE-1.md"))
    monkeypatch.setenv("CSD_AUTODEV_WT", str(tmp_path / "memory-gate-wt-p1-09"))
    monkeypatch.setenv("CSD_GROK_NEED", str(tmp_path / "csd-need-grok.json"))
    monkeypatch.setenv("CSD_AUTODEV_STALL", str(tmp_path / "autodev-stall.json"))
    monkeypatch.setenv("CSD_GPU_PLAN", str(tmp_path / "gpu-plan.json"))
    monkeypatch.setenv("CSD_HARNESS_STATE", str(tmp_path / "harness-state"))
    loader = importlib.machinery.SourceFileLoader("csd_autodev_loop", str(SCRIPT))
    spec = importlib.util.spec_from_loader("csd_autodev_loop", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)

    def fetch_steer() -> dict:
        return mod._read_json(mod.STEER)

    def push_steer(fields: dict) -> dict:
        cur = mod._read_json(mod.STEER)
        cur.update({k: v for k, v in fields.items() if v is not None})
        mod.STEER.parent.mkdir(parents=True, exist_ok=True)
        mod.STEER.write_text(
            __import__("json").dumps(cur, indent=2) + "\n", encoding="utf-8"
        )
        return cur

    def record_run(fields: dict) -> dict:
        run_id = str(fields.get("run_id") or f"test-run-{int(mod.time.time())}")
        return {"run_id": run_id, **fields}

    def publish_event(
        run_id: str, kind: str, payload: dict | None = None, **_k: object
    ) -> dict:
        return {
            "ok": True,
            "event": {"run_id": run_id, "kind": kind, "payload": payload or {}},
        }

    def consume_override(run_id: str) -> str | None:
        return None

    def register_call(run_id: str, pid: int, **_k: object) -> dict:
        return {"run_id": run_id, "pid": pid}

    def clear_call(run_id: str) -> dict:
        return {"ok": True, "run_id": run_id}

    monkeypatch.setattr(mod, "fetch_steer", fetch_steer)
    monkeypatch.setattr(mod, "push_steer", push_steer)
    monkeypatch.setattr(mod, "record_run", record_run)
    monkeypatch.setattr(mod, "publish_event", publish_event)
    monkeypatch.setattr(mod, "consume_override", consume_override)
    monkeypatch.setattr(mod, "register_call", register_call)
    monkeypatch.setattr(mod, "clear_call", clear_call)
    return mod


def test_dest_worktree_region_pretrain_is_cogsyndelta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Steer region-pretrain must land in this repo, never memory-gate."""
    mod = load_loop(tmp_path, monkeypatch)
    dest = mod.dest_worktree("region-pretrain")
    assert dest != mod.MG_DEFAULT
    assert "memory-gate" not in str(dest)
    assert "CogSynDelta" in str(dest) or dest == mod.ROOT
    assert mod.apply_worktree_name("region-pretrain") == "region-pretrain"
    assert mod.is_region_pretrain("P1-region-pretrain") is True
    assert mod.is_region_pretrain("P1-09") is False


def test_dest_worktree_p1_09_stays_memory_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-09 still targets the memory-gate worktree."""
    mod = load_loop(tmp_path, monkeypatch)
    dest = mod.dest_worktree("P1-09")
    assert dest == mod.MG_WT
    assert dest != mod.ROOT
    assert mod.apply_worktree_name("P1-09") == "p1-09"


def test_next_open_goal_reads_steer_region_pretrain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CSD_STEER next_goal region-pretrain wins over GOALS.md."""
    mod = load_loop(tmp_path, monkeypatch)
    steer = tmp_path / "csd-steer.json"
    steer.write_text(
        '{"pause": false, "next_goal": "region-pretrain"}',
        encoding="utf-8",
    )
    mod.STEER = steer
    assert mod.next_open_goal() == "region-pretrain"
    assert mod.is_region_pretrain(mod.next_open_goal()) is True


def test_cap_ping_drops_transcripts_and_stays_under_2kib(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ping JSON is identity autodev, no dumps, at most 2048 bytes."""
    mod = load_loop(tmp_path, monkeypatch)
    rec = mod.cap_ping(
        {
            "need": True,
            "question": "x" * 400,
            "evidence": "y" * 800,
            "transcript": "secret chat",
            "stdout": "pytest dump",
            "tried": ["a"] * 40,
            "paths": ["src/x.py"] * 40,
        }
    )
    assert rec["identity"] == "autodev"
    assert "transcript" not in rec
    assert "stdout" not in rec
    raw = __import__("json").dumps(rec, separators=(",", ":")).encode()
    assert len(raw) <= 2048


def test_need_grok_flag_default_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CSD_NEED_GROK defaults off; stalls must not set need=true."""
    monkeypatch.delenv("CSD_NEED_GROK", raising=False)
    monkeypatch.delenv("CSD_NEED_GROK_ENABLED", raising=False)
    mod = load_loop(tmp_path, monkeypatch)
    mod.GROK_NEED = tmp_path / "csd-need-grok.json"
    mod.STALL = tmp_path / "autodev-stall.json"
    assert mod.need_grok_enabled() is False
    rec = mod.write_need_grok(
        question="CI red twice?",
        evidence="blocker: pytest",
        goal="P1-09",
        blocker="required pytest red",
    )
    assert rec["need"] is False
    assert rec["enabled"] is False
    ping = mod.maybe_ping_stall("P1-09", "x", "y", "z")
    assert ping["need"] is False
    assert ping.get("enabled") is False


def test_write_need_grok_fingerprint_does_not_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same stall fingerprint must not rewrite the mailbox (no tick mail)."""
    monkeypatch.setenv("CSD_NEED_GROK", "1")
    mod = load_loop(tmp_path, monkeypatch)
    mod.GROK_NEED = tmp_path / "csd-need-grok.json"
    first = mod.write_need_grok(
        question="CI red twice?",
        evidence="blocker: pytest; paths: tests/t.py; tried: wait",
        goal="P1-09",
        blocker="required pytest red",
        paths=["tests/t.py"],
        tried=["wait"],
    )
    blob1 = mod.GROK_NEED.read_bytes()
    second = mod.write_need_grok(
        question="CI red twice?",
        evidence="blocker: pytest; paths: tests/t.py; tried: wait",
        goal="P1-09",
        blocker="required pytest red",
        paths=["tests/t.py"],
        tried=["wait"],
    )
    blob2 = mod.GROK_NEED.read_bytes()
    assert first["need"] is True
    assert second["fp"] == first["fp"]
    assert blob2 == blob1


def test_maybe_ping_stall_waits_for_n_equals_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First failure is not a ping; second same fingerprint sets need=true."""
    monkeypatch.setenv("CSD_NEED_GROK", "1")
    monkeypatch.setenv("CSD_STOP_RETRY", "1")
    mod = load_loop(tmp_path, monkeypatch)
    mod.GROK_NEED = tmp_path / "csd-need-grok.json"
    mod.STALL = tmp_path / "autodev-stall.json"
    first = mod.maybe_ping_stall(
        "P1-09",
        "required pytest red",
        "contract or runner?",
        "blocker: CI; paths: scripts/csd-autodev-loop; tried: wait",
        paths=["scripts/csd-autodev-loop"],
        tried=["wait"],
    )
    assert first["need"] is False
    assert first["n"] == 1
    second = mod.maybe_ping_stall(
        "P1-09",
        "required pytest red",
        "contract or runner?",
        "blocker: CI; paths: scripts/csd-autodev-loop; tried: wait",
        paths=["scripts/csd-autodev-loop"],
        tried=["wait"],
    )
    assert second["need"] is True
    assert second["identity"] == "autodev"


def test_run_refuses_hosted_grok_and_github(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Loop never spawns grok or GitHub remotes."""
    mod = load_loop(tmp_path, monkeypatch)
    rc, text = mod._run(["grok", "--print"])
    assert rc == 2
    assert "refusing hosted grok" in text
    rc, text = mod._run(["git", "push", "https://github.com/tzervas/CogSynDelta.git"])
    assert rc == 2
    assert "never GitHub" in text


def test_ti_ok_requires_live_and_guest_smi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1080 Ti only when catalog live=true and guest smi lists 1080."""
    mod = load_loop(tmp_path, monkeypatch)
    plan = tmp_path / "gpu-plan.json"
    mod.GPU_PLAN = plan
    plan.write_text("{}", encoding="utf-8")
    assert mod.ti_ok() is False
    plan.write_text(
        '{"host-gpu-b-1080ti": {"live": true, "name": "NVIDIA GeForce GTX 1080 Ti"}}',
        encoding="utf-8",
    )
    # catalog in-repo currently live=true; both gates must pass
    assert mod.ti_ok() is True
    plan.write_text(
        '{"host-gpu-b-1080ti": {"live": true, "name": "NVIDIA GeForce RTX 5080"}}',
        encoding="utf-8",
    )
    assert mod.ti_ok() is False


def test_repo_for_region_pretrain_is_cogsyndelta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Region-pretrain PRs target CogSynDelta, never memory-gate."""
    mod = load_loop(tmp_path, monkeypatch)
    assert mod.repo_for("region-pretrain") == "CogSynDelta"
    assert mod.repo_for("P1-region-pretrain") == "CogSynDelta"
    assert mod.repo_for("P1-09") == "memory-gate"


def test_land_git_skips_when_staged_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unrelated dirty files must not force a no-op commit."""
    mod = load_loop(tmp_path, monkeypatch)
    dest = tmp_path / "wt"
    dest.mkdir()
    monkeypatch.setattr(mod, "dest_worktree", lambda _g: dest)

    def fake_git(args: list[str], cwd: Path) -> dict:
        if args[0] in {"commit", "push"}:
            raise AssertionError(f"must not {args[0]} when staged empty")
        stdout = {
            ("rev-parse", "--abbrev-ref"): "feat/agent-harness",
            ("rev-parse", "HEAD"): "abc123",
            ("remote",): "forgejo\norigin",
            ("fetch",): "Already up to date.",
            ("merge",): "Already up to date.",
            ("add",): "",
            ("diff",): "",
        }
        key = tuple(args[:2]) if args[:1] == ["rev-parse"] else (args[0],)
        return {"ok": True, "stdout": stdout.get(key, ""), "rc": 0}

    monkeypatch.setattr(mod, "git_cmd", fake_git)
    # No PR from this branch has ever merged, so a clean tree means the
    # implementer wrote the file back unchanged -- not that the work landed.
    monkeypatch.setattr(mod, "branch_ever_merged", lambda *_a, **_k: False)
    rec = mod.land_git(
        "region-pretrain",
        {"ok": True, "path": "docs/program/GOAL-LOOP.md"},
    )
    # The original point of this test stands: nothing is committed or pushed,
    # which fake_git asserts directly. What changed is the VERDICT -- a no-op
    # used to be reported as success and closed the goal.
    assert rec["ok"] is False
    assert rec["no_op"] is True
    assert rec["repo"] == "CogSynDelta"

    # With evidence that the work landed, it is satisfied and closes.
    monkeypatch.setattr(mod, "branch_ever_merged", lambda *_a, **_k: True)
    monkeypatch.setattr(mod, "close_goal", lambda _b: {"ok": True})
    monkeypatch.setattr(mod, "emit_continue", lambda _w: {"ok": True})
    rec = mod.land_git(
        "region-pretrain",
        {"ok": True, "path": "docs/program/GOAL-LOOP.md"},
    )
    assert rec["ok"] is True
    assert rec["skipped"] == "clean"


def test_harness_goal_is_csd_autodev_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Loop/lab/forgejo CLI lands in csd-autodev, never CogSynDelta."""
    mod = load_loop(tmp_path, monkeypatch)
    home = tmp_path / "csd-autodev"
    home.mkdir()
    mod.AUTODEV_HOME = home
    assert mod.is_harness_goal("autodev-harness") is True
    assert mod.is_harness_rel("scripts/csd-autodev-loop") is True
    assert mod.is_harness_rel("tests/test_poc_region_pretrain.py") is False
    assert mod.dest_worktree("lab-console") == home
    assert mod.repo_for("autodev-harness") == "csd-autodev"
    assert mod.repo_for("region-pretrain") == "CogSynDelta"


def test_apply_reroutes_harness_path_off_cogsyndelta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """scripts/csd-lab-console proposed during a CSD goal still leaves CSD."""
    mod = load_loop(tmp_path, monkeypatch)
    home = tmp_path / "csd-autodev"
    home.mkdir()
    mod.AUTODEV_HOME = home
    rec = mod.apply_proposal(
        {"path": "scripts/csd-lab-console", "content": "#!/usr/bin/env python3\n"},
        mod.ROOT,
        worktree="region-pretrain",
    )
    assert rec["ok"] is True
    wrote = Path(str(rec.get("wrote") or ""))
    assert wrote.is_file()
    assert str(home) in str(wrote)


def test_launch_local_workflow_dispatches_not_grok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Queue-failed fallback is Forgejo csd-python-first-drive, not hosted Grok."""
    mod = load_loop(tmp_path, monkeypatch)
    seen: list[list[str]] = []

    def fake_run(argv: list[str], **_k: object) -> tuple[int, str]:
        seen.append(list(argv))
        return 0, '{"ok": true, "http": 204}'

    monkeypatch.setattr(mod, "_run", fake_run)
    rec = mod.launch_local_workflow("region-pretrain", "queue-failed")
    assert rec["ok"] is True
    assert rec["hosted_grok"] is False
    assert rec["workflow"] == "csd-python-first-drive.yml"
    assert seen
    flat = " ".join(str(a) for a in seen[0])
    assert "workflow-dispatch" in flat
    assert "csd-python-first-drive.yml" in flat
    assert "grok" not in flat.lower()


def test_cluster_view_three_backends_not_256(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Heartbeat cluster is 3090+5080+guest retrieve-index-light, not 256."""
    mod = load_loop(tmp_path, monkeypatch)
    plan = tmp_path / "gpu-plan.json"
    plan.write_text(
        '{"host-gpu-b-1080ti": {"live": true, "name": "NVIDIA GeForce GTX 1080 Ti"}}',
        encoding="utf-8",
    )
    mod.GPU_PLAN = plan
    view = mod.cluster_view()
    assert view["backends"] == ["host-a", "host-gpu-b", "host-gpu-b-1080ti"]
    assert view["count"] == 3
    assert view["not_256_specialists"] is True
    guest = view["host-gpu-b-1080ti"]
    assert guest["live"] is True
    assert guest["role"] == "retrieve-index-light"
    assert guest["host_ip"] == "203.0.113.11"
    assert guest["guest_ip"] == "203.0.113.12"


def test_beat_records_cluster_next_goal_and_need_grok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """beat() writes next_goal, 1080 Ti live role, mailbox, identity autodev."""
    mod = load_loop(tmp_path, monkeypatch)
    steer = tmp_path / "csd-steer.json"
    steer.write_text(
        '{"pause": false, "next_goal": "region-pretrain"}',
        encoding="utf-8",
    )
    heart = tmp_path / "autodev-heartbeat.json"
    need = tmp_path / "csd-need-grok.json"
    need.write_text('{"need": false}', encoding="utf-8")
    plan = tmp_path / "gpu-plan.json"
    plan.write_text(
        '{"host-gpu-b-1080ti": {"live": true, "name": "NVIDIA GeForce GTX 1080 Ti"}}',
        encoding="utf-8",
    )
    mod.STEER = steer
    mod.HEART = heart
    mod.GROK_NEED = need
    mod.GPU_PLAN = plan
    mod.beat()
    rec = __import__("json").loads(heart.read_text(encoding="utf-8"))
    assert rec["identity"] == "autodev"
    assert rec["next_goal"] == "region-pretrain"
    assert rec["ti"] is True
    assert rec["cluster"]["backends"] == [
        "host-a",
        "host-gpu-b",
        "host-gpu-b-1080ti",
    ]
    assert rec["cluster"]["host-gpu-b-1080ti"]["live"] is True
    assert rec["cluster"]["host-gpu-b-1080ti"]["role"] == "retrieve-index-light"
    assert rec["need_grok"]["need"] is False
    assert rec["need_grok"]["path"] == str(need)
    assert "memory-gate" not in rec["wt"]


def test_ensure_steer_cluster_keeps_region_pretrain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cluster annotation must not steer away from region-pretrain."""
    mod = load_loop(tmp_path, monkeypatch)
    steer = tmp_path / "csd-steer.json"
    steer.write_text(
        '{"pause": false, "next_goal": "region-pretrain", "note": "keep"}',
        encoding="utf-8",
    )
    plan = tmp_path / "gpu-plan.json"
    plan.write_text(
        '{"host-gpu-b-1080ti": {"live": true, "name": "NVIDIA GeForce GTX 1080 Ti"}}',
        encoding="utf-8",
    )
    need = tmp_path / "csd-need-grok.json"
    mod.STEER = steer
    mod.GPU_PLAN = plan
    mod.GROK_NEED = need
    out = mod.ensure_steer_cluster()
    assert out["next_goal"] == "region-pretrain"
    assert out["cluster"] == ["host-a", "host-gpu-b", "host-gpu-b-1080ti"]
    assert out["host-gpu-b-1080ti"]["live"] is True
    assert out["host-gpu-b-1080ti"]["role"] == "retrieve-index-light"
    assert out["need_grok"] == str(need)
    assert out["note"] == "keep"


def test_ensure_steer_cluster_keeps_p1_09_if_already(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If steer is already P1-09, leave that board row; still attach cluster."""
    mod = load_loop(tmp_path, monkeypatch)
    steer = tmp_path / "csd-steer.json"
    steer.write_text('{"pause": false, "next_goal": "P1-09"}', encoding="utf-8")
    plan = tmp_path / "gpu-plan.json"
    plan.write_text(
        '{"host-gpu-b-1080ti": {"live": true, "name": "NVIDIA GeForce GTX 1080 Ti"}}',
        encoding="utf-8",
    )
    mod.STEER = steer
    mod.GPU_PLAN = plan
    mod.GROK_NEED = tmp_path / "csd-need-grok.json"
    out = mod.ensure_steer_cluster()
    assert out["next_goal"] == "P1-09"
    assert out["cluster"] == ["host-a", "host-gpu-b", "host-gpu-b-1080ti"]


def test_beat_preserves_inflight_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Start-of-tick beat() must not drop an in-flight PR last record."""
    mod = load_loop(tmp_path, monkeypatch)
    steer = tmp_path / "csd-steer.json"
    steer.write_text(
        '{"pause": false, "next_goal": "region-pretrain"}',
        encoding="utf-8",
    )
    heart = tmp_path / "autodev-heartbeat.json"
    need = tmp_path / "csd-need-grok.json"
    need.write_text('{"need": false}', encoding="utf-8")
    plan = tmp_path / "gpu-plan.json"
    plan.write_text(
        '{"host-gpu-b-1080ti": {"live": true, "name": "NVIDIA GeForce GTX 1080 Ti"}}',
        encoding="utf-8",
    )
    mod.STEER = steer
    mod.HEART = heart
    mod.GROK_NEED = need
    mod.GPU_PLAN = plan
    inflight = {
        "ok": False,
        "goal": "region-pretrain",
        "number": 3,
        "sha": "abc123",
        "merged": False,
        "required_ran": False,
    }
    mod.beat({"last": inflight})
    mod.beat()
    rec = __import__("json").loads(heart.read_text(encoding="utf-8"))
    assert rec["next_goal"] == "region-pretrain"
    assert rec["last"]["number"] == 3
    assert rec["last"]["merged"] is False
    assert rec["last"]["sha"] == "abc123"


def test_git_cmd_allows_merge_origin_main_on_feature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch-up merge of origin/main into a feature branch is allowed."""
    mod = load_loop(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "current_branch", lambda _cwd: "feat/agent-harness")
    seen: list[list[str]] = []

    def fake_run(argv: list[str], **_k: object) -> tuple[int, str]:
        seen.append(list(argv))
        return 0, "ok"

    monkeypatch.setattr(mod, "_run", fake_run)
    rec = mod.git_cmd(["merge", "--no-edit", "origin/main"], tmp_path)
    assert rec["ok"] is True
    assert "merge" in seen[0]


def test_git_cmd_allows_checkout_of_a_trunk_but_never_a_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Standing on a trunk is not authoring on it.

    Checkout was refused until 2026-09-08. The loop has to stand on the default
    branch to fetch and fast-forward it, and refusing that stranded it on a
    branch the forge had deleted -- every tick then failed. The rule that
    actually protects the trunk is the push refusal, which is asserted here
    alongside so this test cannot be read as a relaxation.
    """
    mod = load_loop(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "_run", lambda *_a, **_k: (0, "ok"))
    assert mod.git_cmd(["checkout", "main"], tmp_path)["ok"] is True

    rec = mod.git_cmd(["push", "origin", "main"], tmp_path)
    assert rec["ok"] is False
    assert "refusing push" in rec["error"]


def test_git_cmd_refuses_merge_while_on_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Merge while HEAD is main is still forbidden."""
    mod = load_loop(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "current_branch", lambda _cwd: "main")
    rec = mod.git_cmd(["merge", "--no-edit", "feat/agent-harness"], tmp_path)
    assert rec["ok"] is False
    assert "refusing merge on main" in rec["error"]


def test_refresh_pr_replaces_stale_heartbeat_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Operator merge/rebase moves the PR head; heartbeat SHA must not stick."""
    mod = load_loop(tmp_path, monkeypatch)

    def fj(args: list[str], timeout: int = 90) -> dict:
        if args[:1] == ["pr"]:
            return {
                "http": 200,
                "sha": "c31186ccnew",
                "head": "feat/agent-harness",
                "base": "main",
                "merged": False,
            }
        return {}

    monkeypatch.setattr(mod, "forgejo_cmd", fj)
    live = mod.refresh_pr(
        {
            "number": 3,
            "sha": "b5ed0e75old",
            "repo": "CogSynDelta",
            "goal": "region-pretrain",
        }
    )
    assert live["sha"] == "c31186ccnew"
    assert live["base"] == "main"


def test_follow_pr_statuses_live_head_not_heartbeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """status/wait must use the live PR SHA after a merge into the feature."""
    mod = load_loop(tmp_path, monkeypatch)
    seen: list[list[str]] = []

    def fj(args: list[str], timeout: int = 90) -> dict:
        seen.append(list(args))
        if args[:1] == ["pr"]:
            return {
                "http": 200,
                "sha": "c31186ccnew",
                "head": "feat/agent-harness",
                "base": "main",
            }
        if args[:1] == ["status"]:
            return {"http": 200, "state": "failure", "checks": []}
        return {}

    monkeypatch.setattr(mod, "forgejo_cmd", fj)
    rec = mod.follow_pr(
        {
            "number": 3,
            "sha": "b5ed0e75old",
            "repo": "CogSynDelta",
            "goal": "region-pretrain",
        }
    )
    assert rec["sha"] == "c31186ccnew"
    assert rec["failed"] is True
    assert ["status", "CogSynDelta", "c31186ccnew"] in seen


def test_handle_inflight_merges_when_behind_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Behind merge-target → merge origin/main into feature, never rebase."""
    mod = load_loop(tmp_path, monkeypatch)

    def fj(args: list[str], timeout: int = 90) -> dict:
        if args[:1] == ["pr"]:
            return {
                "http": 200,
                "sha": "oldsha",
                "head": "feat/agent-harness",
                "base": "main",
                "mergeable_state": "behind",
            }
        if args[:1] == ["compare"]:
            return {"http": 200, "behind": 4, "ahead": 2, "ok": True}
        if args[:1] == ["status"]:
            return {"http": 200, "state": "pending"}
        return {}

    monkeypatch.setattr(mod, "forgejo_cmd", fj)
    monkeypatch.setattr(mod, "dest_worktree", lambda _g: tmp_path)
    monkeypatch.setattr(
        mod,
        "sync_feature_with_base",
        lambda _cwd, base="main": {
            "ok": True,
            "sha": "mergedsha",
            "how": "merge",
            "base": base,
        },
    )
    monkeypatch.setattr(mod, "current_sha", lambda _cwd: "oldsha")
    rec = mod.handle_inflight(
        {
            "number": 3,
            "sha": "oldsha",
            "repo": "CogSynDelta",
            "goal": "region-pretrain",
            "merged": False,
        }
    )
    assert rec["synced"] is True
    assert rec["sha"] == "mergedsha"
    assert rec["sync"]["how"] == "merge"


def test_handle_inflight_steer_note_retries_implement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """New steer with CI job URLs must unstick follow-only on a red PR."""
    mod = load_loop(tmp_path, monkeypatch)
    steer = tmp_path / "csd-steer.json"
    note = (
        "updated the branch for the PR off the upstream since it was out of date. "
        "reran checks. https://git.example.com/tzervas/CogSynDelta/actions/runs/631/jobs/0"
    )
    steer.write_text(
        json.dumps({"pause": False, "next_goal": "region-pretrain", "note": note}),
        encoding="utf-8",
    )
    mod.STEER = steer

    def fj(args: list[str], timeout: int = 90) -> dict:
        if args[:1] == ["pr"]:
            return {
                "http": 200,
                "sha": "c31186ccnew",
                "head": "feat/agent-harness",
                "base": "main",
            }
        if args[:1] == ["compare"]:
            return {"http": 200, "behind": 0, "ahead": 3, "ok": True}
        if args[:1] == ["status"]:
            return {"http": 200, "state": "failure", "checks": []}
        return {}

    monkeypatch.setattr(mod, "forgejo_cmd", fj)
    monkeypatch.setattr(mod, "dest_worktree", lambda _g: tmp_path)
    monkeypatch.setattr(mod, "current_sha", lambda _cwd: "c31186ccnew")
    rec = mod.handle_inflight(
        {
            "number": 3,
            "sha": "b5ed0e75old",
            "repo": "CogSynDelta",
            "goal": "region-pretrain",
            "merged": False,
        }
    )
    assert rec["retry_implement"] is True
    assert rec["sha"] == "c31186ccnew"
    assert rec["consumed_note"]


def test_handle_inflight_same_steer_note_does_not_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Already-consumed steer note must not re-implement forever."""
    mod = load_loop(tmp_path, monkeypatch)
    note = "updated the branch. reran checks. actions/runs/631/jobs/0"
    steer = tmp_path / "csd-steer.json"
    steer.write_text(
        json.dumps({"pause": False, "next_goal": "region-pretrain", "note": note}),
        encoding="utf-8",
    )
    mod.STEER = steer
    fp = mod.note_fingerprint(note)

    def fj(args: list[str], timeout: int = 90) -> dict:
        if args[:1] == ["pr"]:
            return {
                "http": 200,
                "sha": "c31186ccnew",
                "head": "feat/agent-harness",
                "base": "main",
            }
        if args[:1] == ["compare"]:
            return {"http": 200, "behind": 0, "ahead": 1, "ok": True}
        if args[:1] == ["status"]:
            return {"http": 200, "state": "failure", "checks": []}
        return {}

    monkeypatch.setattr(mod, "forgejo_cmd", fj)
    monkeypatch.setattr(mod, "dest_worktree", lambda _g: tmp_path)
    monkeypatch.setattr(mod, "current_sha", lambda _cwd: "c31186ccnew")
    rec = mod.handle_inflight(
        {
            "number": 3,
            "sha": "c31186ccnew",
            "repo": "CogSynDelta",
            "goal": "region-pretrain",
            "merged": False,
            "consumed_note": fp,
            "implemented_sha": "c31186ccnew",
            "approach_fails": 1,
        }
    )
    assert rec.get("retry_implement") is not True
    assert rec.get("failed") is True


def test_handle_inflight_integrates_when_local_sha_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Local worker behind operator merge on the feature ref must merge, not glue."""
    mod = load_loop(tmp_path, monkeypatch)

    def fj(args: list[str], timeout: int = 90) -> dict:
        if args[:1] == ["pr"]:
            return {
                "http": 200,
                "sha": "c31186ccnew",
                "head": "feat/agent-harness",
                "base": "main",
            }
        if args[:1] == ["compare"]:
            return {"http": 200, "behind": 0, "ahead": 5, "ok": True}
        if args[:1] == ["status"]:
            return {"http": 200, "state": "pending"}
        return {}

    monkeypatch.setattr(mod, "forgejo_cmd", fj)
    monkeypatch.setattr(mod, "dest_worktree", lambda _g: tmp_path)
    monkeypatch.setattr(mod, "current_sha", lambda _cwd: "34f63ecalocal")
    monkeypatch.setattr(
        mod,
        "sync_feature_with_base",
        lambda _cwd, base="main": {
            "ok": True,
            "sha": "c31186ccnew",
            "how": "merge",
            "base": base,
        },
    )
    rec = mod.handle_inflight(
        {
            "number": 3,
            "sha": "b5ed0e75old",
            "repo": "CogSynDelta",
            "goal": "region-pretrain",
            "merged": False,
        }
    )
    assert rec["synced"] is True
    assert rec["sha"] == "c31186ccnew"
    assert rec["sync"]["how"] == "merge"


def test_plan_next_approach_changes_after_n_same_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same red SHA: wait until N, then change_approach instead of stopping."""
    monkeypatch.setenv("CSD_APPROACH_RETRIES", "3")
    monkeypatch.delenv("CSD_STOP_RETRY", raising=False)
    monkeypatch.delenv("CSD_NEED_GROK", raising=False)
    mod = load_loop(tmp_path, monkeypatch)
    last = {"implemented_sha": "abc", "approach_fails": 2, "approach_id": 0}
    out = mod.plan_next_approach(last, {"sha": "abc", "state": "failure"})
    assert out["change_approach"] is True
    assert out["retry_implement"] is True
    assert out["approach_id"] == 1
    ping = mod.maybe_ping_stall("g", "b", "q", "e")
    assert ping["need"] is False
    assert ping.get("stop_retry") is False


def test_plan_next_approach_new_sha_retries_without_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """New SHA is a fresh tactic, not an immediate approach change."""
    monkeypatch.setenv("CSD_APPROACH_RETRIES", "3")
    mod = load_loop(tmp_path, monkeypatch)
    last = {"implemented_sha": "old", "approach_fails": 3, "approach_id": 1}
    out = mod.plan_next_approach(last, {"sha": "new", "state": "failure"})
    assert out["change_approach"] is False
    assert out["retry_implement"] is True
    assert out["approach_fails"] == 1


def test_implement_prompt_includes_steer_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """local/code must see the operator steer (job URLs, merge, import fixes)."""
    mod = load_loop(tmp_path, monkeypatch)
    steer = tmp_path / "csd-steer.json"
    steer.write_text(
        json.dumps(
            {
                "pause": False,
                "next_goal": "region-pretrain",
                "note": "updated the branch; jobs/0 import issues",
            }
        ),
        encoding="utf-8",
    )
    mod.STEER = steer
    captured: list[str] = []

    def fake_run(argv: list[str], **_k: object) -> tuple[int, str]:
        if "--prompt" in argv:
            captured.append(argv[argv.index("--prompt") + 1])
        return 0, '{"path":"tests/x.py","content":"x"}'

    monkeypatch.setattr(mod, "_run", fake_run)
    rec = mod.implement("region-pretrain")
    assert rec["rc"] == 0
    assert captured
    assert "updated the branch" in captured[0]
    assert "live PR head" in captured[0]


def test_unreachable_paths_read_as_absent_not_as_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path we cannot traverse must be "not there", never an exception.

    `Path.is_file()` raises PermissionError when a parent denies traversal, and
    several defaults point outside the service account's reach. Under the
    operator's uid they stat fine, so this only ever failed where it mattered:
    dest_worktree("region-pretrain") crashed the tick on the CSD product root
    under /home/operator.
    """
    mod = load_loop(tmp_path, monkeypatch)

    denied = tmp_path / "denied"
    denied.mkdir()
    (denied / "child").mkdir()
    denied.chmod(0o000)
    try:
        target = denied / "child"
        # The bare pathlib calls are what used to be here; assert they really
        # do raise, so this test cannot quietly stop testing anything.
        with pytest.raises(OSError):
            target.is_dir()
        assert mod.readable_dir(target) is False
        assert mod.readable_file(target / "f.json") is False
        assert mod._read_json(target / "f.json") == {}

        monkeypatch.setenv("CSD_PRODUCT_ROOT", str(target))
        monkeypatch.delenv("AUTODEV_TARGET_WORKTREE", raising=False)
        mod.TARGET_WT = ""
        assert mod.dest_worktree("region-pretrain") == mod.ROOT
    finally:
        denied.chmod(0o755)


def test_a_closed_pr_is_dropped_not_retried_forever(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PR closed without merging is terminal.

    Re-gating one asks Forgejo to merge a number that no longer exists, which
    answers 404 -- every tick, forever, with the goal never returning to the
    queue. Observed after a destructive PR was closed by hand.
    """
    mod = load_loop(tmp_path, monkeypatch)

    def fake_forgejo(args: list[str], **_k: object) -> dict:
        assert args[0] == "pr", f"a closed PR must not reach {args[0]}"
        return {
            "http": 200,
            "sha": "deadbeef",
            "head": "autodev/g-x",
            "base": "main",
            "merged": False,
            "state": "closed",
        }

    monkeypatch.setattr(mod, "forgejo_cmd", fake_forgejo)
    rec = mod.handle_inflight(
        {"number": 4, "repo": "o/r", "goal": "| G-X-1 | do a thing | open |"}
    )
    assert rec["ok"] is True
    assert rec["abandoned"] is True
    assert rec["clear_inflight"] is True
    assert "closed without merging" in rec["reason"]


def test_an_open_pr_is_still_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The drop must be specific to closed, or every PR is abandoned."""
    mod = load_loop(tmp_path, monkeypatch)
    seen: list[str] = []

    def fake_forgejo(args: list[str], **_k: object) -> dict:
        seen.append(args[0])
        if args[0] == "pr":
            return {
                "http": 200,
                "sha": "deadbeef",
                "head": "autodev/g-x",
                "base": "main",
                "merged": False,
                "state": "open",
                "mergeable": True,
            }
        return {"ok": False, "state": "pending"}

    monkeypatch.setattr(mod, "forgejo_cmd", fake_forgejo)
    monkeypatch.setattr(mod, "divergence", lambda *_a, **_k: {"behind": 0})
    monkeypatch.setattr(mod, "current_sha", lambda *_a, **_k: "deadbeef")
    rec = mod.handle_inflight(
        {"number": 4, "repo": "o/r", "goal": "| G-X-1 | do a thing | open |"}
    )
    assert not rec.get("abandoned")
    assert not rec.get("clear_inflight")


def test_the_goals_preamble_reaches_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rows say "use the mapping table above"; the table must come with them.

    The loop sent only the matched row, so every one of the nine
    doc-sanitisation goals arrived without the mapping table it referred to and
    the model correctly refused: "no mapping table was provided in the input".
    A goal the implementer cannot act on is a stall wearing a decline's clothes.
    """
    mod = load_loop(tmp_path, monkeypatch)
    goals = tmp_path / "GOALS.md"
    goals.write_text(
        "# Sanitise\n\n"
        "## The mapping — use exactly these\n\n"
        "| real | replace with |\n|---|---|\n| `203.0.113.10` | `203.0.113.10` |\n\n"
        "| id | goal | state |\n|---|---|---|\n"
        "| G-S-1 | Change ONLY `docs/A.md` using the table above. | open |\n",
        encoding="utf-8",
    )
    mod.GOALS = goals

    pre = mod.goals_preamble()
    assert "203.0.113.10" in pre
    assert "| id | goal | state |" not in pre, "the table of rows is not preamble"
    assert "G-S-1" not in pre


def test_a_goals_file_with_no_preamble_adds_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No prose above the table means no context to carry, not an empty header."""
    mod = load_loop(tmp_path, monkeypatch)
    goals = tmp_path / "GOALS.md"
    goals.write_text("| id | goal | state |\n|---|---|---|\n", encoding="utf-8")
    mod.GOALS = goals
    assert mod.goals_preamble() == ""


def test_an_oversized_preamble_is_cut_at_a_heading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Truncating mid-table is worse than dropping a section whole."""
    mod = load_loop(tmp_path, monkeypatch)
    goals = tmp_path / "GOALS.md"
    goals.write_text(
        "# One\n\n"
        + ("filler line\n" * 900)
        + "\n## Two\n\nlate section\n\n| id | goal | state |\n",
        encoding="utf-8",
    )
    mod.GOALS = goals
    pre = mod.goals_preamble()
    assert len(pre) <= mod.GOALS_PREAMBLE_MAX
    assert "late section" not in pre
    assert pre.rstrip().endswith("filler line")


def test_the_github_guard_looks_at_remotes_not_prose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The implementer's prompt is one argv element carrying whole documents.

    `"github.com" in any argument` refused to call the MODEL whenever a file
    being changed mentioned GitHub. G-SANITISE-ONBOARD failed that way on every
    attempt: the document it was asked to sanitise describes a GitHub mirror.
    """
    mod = load_loop(tmp_path, monkeypatch)

    # Prose must not trip it, however much of it there is.
    assert mod.targets_github(["--prompt", "the mirror lives at github.com"]) is False
    assert mod.targets_github(["--prompt", "See https://github.com in the docs"]) is False
    assert mod.targets_github(["git", "status"]) is False

    # An actual remote must, in every form that reaches one.
    assert mod.targets_github(["git", "push", "https://github.com/o/r.git"]) is True
    assert mod.targets_github(["git", "fetch", "git@github.com:o/r.git"]) is True
    assert mod.targets_github(["git", "clone", "ssh://github.com/o/r"]) is True
    assert mod.targets_github(["git", "remote", "add", "gh", "github.com/o/r"]) is True


def test_implement_retries_are_counted_across_ticks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A per-tick counter is always 1, so the bounded retry never terminates."""
    mod = load_loop(tmp_path, monkeypatch)
    budget = mod.approach_retries()
    # At the budget the emitter must refuse rather than wake itself again.
    assert mod.emit_retry("| G-X-1 | x | open |", "rc=2", budget)["ok"] is False
    assert mod.emit_retry("| G-X-1 | x | open |", "rc=2", budget + 1)["ok"] is False


def test_a_no_op_write_does_not_close_the_goal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean tree means one of two things, and guessing closed real work.

    "Nothing to commit" happens when the change landed by another route -- and
    also when the implementer wrote the file back byte-identical. G-ARCH-GUARDS
    was asked to correct five wrong paragraphs, produced a no-op, and was marked
    done with every wrong paragraph still in it, on a green gate, because prose
    has no tests.
    """
    mod = load_loop(tmp_path, monkeypatch)
    asked: list[list] = []

    def fake_forgejo(args: list[str], **_k: object) -> dict:
        asked.append(args)
        # No PR from this branch has ever merged.
        return {"pulls": [{"head": "autodev/other", "merged": True}]}

    monkeypatch.setattr(mod, "forgejo_cmd", fake_forgejo)
    assert mod.branch_ever_merged("o/r", "autodev/g-x") is False
    assert asked and "--state" in asked[0] and "all" in asked[0]


def test_a_merged_branch_still_counts_as_satisfied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case the clean-tree branch was written for must keep working.

    A goal whose PR merged outside the loop has to close, or it is re-run
    forever on work that is done.
    """
    mod = load_loop(tmp_path, monkeypatch)
    monkeypatch.setattr(
        mod,
        "forgejo_cmd",
        lambda *_a, **_k: {
            "pulls": [
                {"head": "autodev/g-x", "merged": False, "state": "closed"},
                {"head": "autodev/g-x", "merged": True, "state": "closed"},
            ]
        },
    )
    assert mod.branch_ever_merged("o/r", "autodev/g-x") is True


def test_a_closed_unmerged_pr_is_not_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`state` is open/closed only; a rejected PR must not read as landed."""
    mod = load_loop(tmp_path, monkeypatch)
    monkeypatch.setattr(
        mod,
        "forgejo_cmd",
        lambda *_a, **_k: {
            "pulls": [{"head": "autodev/g-x", "merged": False, "state": "closed"}]
        },
    )
    assert mod.branch_ever_merged("o/r", "autodev/g-x") is False
