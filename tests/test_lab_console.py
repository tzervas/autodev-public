"""Unit tests for lab JSON routes wrapping autodev CLIs."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "csd-lab-console"


def load_lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Import csd-lab-console with isolated plan/steer/heartbeat paths."""
    monkeypatch.setenv("CSD_GPU_PLAN", str(tmp_path / "gpu-plan.json"))
    monkeypatch.setenv("CSD_STEER", str(tmp_path / "csd-steer.json"))
    monkeypatch.setenv("CSD_AUTODEV_HEARTBEAT", str(tmp_path / "hb.json"))
    monkeypatch.setenv("CSD_GROK_NEED", str(tmp_path / "csd-need-grok.json"))
    monkeypatch.setenv("CSD_AUTODEV_OUT", str(tmp_path / "autodev-out"))
    monkeypatch.setenv("CSD_LOCALAI_QUEUE", str(tmp_path / "localai-queue"))
    monkeypatch.setenv("CSD_ROUTER_STATE", str(tmp_path / "model-router-state.json"))
    monkeypatch.setenv("CSD_LAB_BIND", "203.0.113.10")
    vault = tmp_path / "csd-vault"
    (vault / "hf").mkdir(parents=True)
    monkeypatch.setenv("CSD_VAULT", str(vault))
    monkeypatch.delenv("SECRET_VAULT", raising=False)
    monkeypatch.delenv("TOKEN", raising=False)
    monkeypatch.delenv("FORGEJO_TOKEN", raising=False)
    loader = importlib.machinery.SourceFileLoader("csd_lab_console", str(SCRIPT))
    spec = importlib.util.spec_from_loader("csd_lab_console", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _plan() -> dict[str, Any]:
    return {
        "autodev_priority": True,
        "host-a": {"name": "RTX 3090 Ti", "free_mib": 6000},
        "host-gpu-b": {
            "lock": "lock=idle",
            "comfy": "masked",
            "helper_ok": True,
        },
    }


def _needs(*rels: str) -> None:
    """Skip when a CSD artifact this test reads is not in this repo.

    Both callers assert on `benchmark_results/scale_ladder.json`, which lives in
    the CSD tree. They were INVISIBLE until 2026-09-08, when the pytest
    allow-list that excluded this whole file was removed -- so the suite had been
    reporting green while never running them.

    Named rather than deleted: the assertions are the ones we want, and the test
    revives by itself if the artifact lands here.
    """
    missing = [r for r in rels if not (ROOT / r).exists()]
    if missing:
        pytest.skip("not in this repo: " + ", ".join(missing))


def test_gpu_and_lock_read_plan_without_ssh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = load_lab(tmp_path, monkeypatch)

    def boom(*_a: object, **_k: object) -> tuple[int, str]:
        raise AssertionError("must not spawn CLI when plan file exists")

    monkeypatch.setattr(mod, "_run_cli", boom)
    (tmp_path / "gpu-plan.json").write_text(json.dumps(_plan()), encoding="utf-8")
    code, rec = mod.handle_lab("GET", "/api/gpu", {})
    assert code == 200
    assert rec["host-a"]["name"] == "RTX 3090 Ti"
    assert rec["host-gpu-b"]["comfy"] == "masked"
    code, lock = mod.handle_lab("GET", "/api/gpu/lock", {})
    assert code == 200
    assert lock["lock"] == "lock=idle"
    assert lock["comfy"] == "masked"
    assert lock["helper_ok"] is True


def test_comfy_lab_field_wrap_ready_not_masked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lab status comfy field is wrapped when lock drop-in is live."""
    mod = load_lab(tmp_path, monkeypatch)
    assert mod.comfy_lab_field("generated", True) == "wrapped"
    assert mod.parse_comfy_ssh("generated\nwrap-ready") == "wrapped"
    assert mod.parse_comfy_ssh("masked\nwrap-ready") == "masked"
    (tmp_path / "gpu-plan.json").write_text(
        json.dumps(
            {
                "autodev_priority": True,
                "host-a": {"name": "RTX 3090 Ti"},
                "host-gpu-b": {
                    "lock": "lock=idle",
                    "comfy": "wrapped",
                    "helper_ok": True,
                },
            }
        ),
        encoding="utf-8",
    )

    def boom(*_a: object, **_k: object) -> tuple[int, str]:
        raise AssertionError("must not spawn CLI when plan file exists")

    monkeypatch.setattr(mod, "_run_cli", boom)
    code, rec = mod.handle_lab("GET", "/api/gpu", {})
    assert code == 200
    assert rec["host-gpu-b"]["comfy"] == "wrapped"
    code, lock = mod.handle_lab("GET", "/api/gpu/lock", {})
    assert lock["comfy"] == "wrapped"
    assert lock["helper_ok"] is True


def test_git_refuses_github_and_protected_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = load_lab(tmp_path, monkeypatch)
    gh = mod.lab_git(["push", "https://github.com/tzervas/CogSynDelta.git", "HEAD"])
    assert gh["ok"] is False
    assert "GitHub" in gh["stdout"]
    main = mod.lab_git(["push", "forgejo", "HEAD:main"])
    assert main["ok"] is False
    assert "main" in main["stdout"]
    code, rec = mod.handle_lab(
        "POST", "/api/git", {"args": ["push", "forgejo", "HEAD:dev"]}
    )
    assert code == 200
    assert rec["ok"] is False


def test_git_wraps_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = load_lab(tmp_path, monkeypatch)
    called: dict[str, Any] = {}

    def fake(argv: list[str], timeout: int = 90) -> tuple[int, str]:
        called["argv"] = argv
        called["timeout"] = timeout
        return 0, "feat/agent-harness"

    monkeypatch.setattr(mod, "_run_cli", fake)
    rec = mod.lab_git(["status", "-sb"])
    assert rec["ok"] is True
    assert rec["rc"] == 0
    assert rec["stdout"] == "feat/agent-harness"
    assert str(mod.GIT_CLI) == called["argv"][0]


def test_forgejo_prs_and_status_wrap_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = load_lab(tmp_path, monkeypatch)
    called: list[list[str]] = []

    def fake(argv: list[str], timeout: int = 90) -> tuple[int, str]:
        called.append(argv)
        if "prs" in argv:
            return 0, json.dumps(
                {"http": 200, "repo": "tzervas/CogSynDelta", "pulls": [{"number": 1}]}
            )
        if "status" in argv:
            return 0, json.dumps({"http": 200, "state": "pending", "checks": []})
        return 1, "{}"

    monkeypatch.setattr(mod, "_run_cli", fake)
    code, rec = mod.handle_lab(
        "GET", "/api/forgejo/prs", {"repo": "CogSynDelta", "limit": 5}
    )
    assert code == 200
    assert rec["http"] == 200
    assert rec["pulls"][0]["number"] == 1
    assert "TOKEN=git/autodev" in called[0]
    code, st = mod.handle_lab(
        "POST",
        "/api/forgejo/status",
        {"repo": "tzervas/CogSynDelta", "sha": "abc"},
    )
    assert code == 200
    assert st["state"] == "pending"
    bad = mod.handle_lab("GET", "/api/forgejo/prs", {"repo": "tzervas/evil"})
    assert bad is not None
    assert bad[1].get("error") == "repo not allowlisted"


def test_loop_worker_does_not_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = load_lab(tmp_path, monkeypatch)
    (tmp_path / "hb.json").write_text(
        json.dumps({"t": time.time(), "last": {"goal": "G-QD", "applied": {"ok": True}}}),
        encoding="utf-8",
    )

    def boom(*_a: object, **_k: object) -> tuple[int, str]:
        raise AssertionError("HTTP must not spawn --worker")

    monkeypatch.setattr(mod, "_run_cli", boom)
    code, rec = mod.handle_lab("POST", "/api/loop", {"mode": "worker"})
    assert code == 200
    assert rec["ok"] is True
    assert rec["goal"] == "G-QD"
    assert "systemd" in rec["notes"]
    assert mod.handle_lab("POST", "/api/provision", {}) is None


def test_steer_get_and_priority_and_bind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = load_lab(tmp_path, monkeypatch)
    (tmp_path / "csd-steer.json").write_text(
        json.dumps({"pause": False, "note": "hold", "autodev_priority": True}),
        encoding="utf-8",
    )
    code, rec = mod.handle_lab("GET", "/api/steer", {})
    assert code == 200
    assert rec["pause"] is False
    assert rec["autodev_priority"] is True
    pri = mod.lab_priority("nope")
    assert pri["ok"] is False
    assert mod.lab_bind() == "203.0.113.10"
    monkeypatch.setenv("CSD_LAB_BIND", "0.0.0.0")
    with pytest.raises(SystemExit, match="WAN"):
        mod.lab_bind()


def test_apply_allowlist_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = load_lab(tmp_path, monkeypatch)
    wt = tmp_path / "p1-08"
    (wt / "src").mkdir(parents=True)
    protected = tmp_path / "memory-gate"
    protected.mkdir()
    mod.WORKTREES = {"p1-08": wt}
    mod.PROTECTED_WT = protected
    ok = mod.http_apply("p1-08", "src/foo.py", "x = 1\n")
    assert ok["ok"] is True
    deny = mod.http_apply("p1-08", "README.md", "nope")
    assert deny["ok"] is False
    assert "not allowed" in deny["error"]
    mod.WORKTREES = {"p1-08": protected}
    refuse = mod.http_apply("p1-08", "src/foo.py", "x")
    assert refuse["ok"] is False
    assert "operator-main-wip" in refuse["error"]


def test_comfy_paths_not_captured_by_lab_wrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = load_lab(tmp_path, monkeypatch)
    assert mod.handle_lab("GET", "/api/comfy/list-workflows", {}) is None
    assert mod.handle_lab("POST", "/api/comfy/queue-prompt", {"prompt": {}}) is None
    assert mod.handle_lab("GET", "/api/status", {}) is None


def test_cluster_snapshot_includes_1080ti_guest_ip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cluster snapshot always exposes host-gpu-b `.251` and 1080 Ti guest_ip."""
    mod = load_lab(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "ssh_5080", lambda _cmd: "")
    rec = mod.cluster_snapshot()
    assert rec["host-gpu-b"]["ip"] == "203.0.113.11"
    ids = [b["id"] for b in rec["backends"]]
    assert ids == ["host-a", "host-gpu-b", "host-gpu-b-1080ti"]
    assert rec["count"] == 3
    assert rec["groups"] == ["csd-autodev", "fleet-rag"]
    g = rec["host_gpu_c"]
    assert g["host"] == "host-gpu-b"
    assert g["host_ip"] == "203.0.113.11"
    assert "guest_ip" in g
    assert g["guest_ip"] == "203.0.113.12"
    assert g["live"] is True
    assert g["group"] == "fleet-rag"
    assert g["path"] == "lab.host-gpu-b.index.1080ti"
    assert g["rag"] == "retrieve-index-light"
    assert rec["prime"]["role"] == "autodev-local-code"
    assert rec["host-gpu-b"]["role"] == "chat-comfy"
    roles = rec.get("roles") or {}
    assert "autodev" in str(roles.get("host-a") or rec["prime"]["role"])
    assert rec["backends"][0]["role"] == "autodev-local-code"
    assert rec["backends"][1]["role"] == "chat-comfy"
    assert rec["backends"][2]["role"] == "retrieve-index-light"


def test_cluster_snapshot_parses_guest_ip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parse a real virsh domifaddr IPv4; never treat `.251` as the guest."""
    mod = load_lab(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "ssh_5080", lambda _cmd: " vnet0  ipv4  203.0.113.21/24")
    rec = mod.cluster_snapshot()
    assert rec["host_gpu_c"]["guest_ip"] == "203.0.113.21"
    assert rec["host_gpu_c"]["live"] is True


def test_steer_post_next_goal_not_p1_08(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /api/steer writes next_goal P1-09 (remaining closeable after P1-08)."""
    mod = load_lab(tmp_path, monkeypatch)
    code, rec = mod.handle_lab("POST", "/api/steer", {"next_goal": "P1-09"})
    assert code == 200
    assert rec["next_goal"] == "P1-09"
    got = json.loads((tmp_path / "csd-steer.json").read_text(encoding="utf-8"))
    assert got["next_goal"] == "P1-09"
    code, rec = mod.handle_lab("GET", "/api/steer", {})
    assert code == 200
    assert rec["next_goal"] == "P1-09"


def test_steer_post_region_pretrain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /api/steer writes next_goal region-pretrain (one live PoC region)."""
    mod = load_lab(tmp_path, monkeypatch)
    code, rec = mod.handle_lab(
        "POST",
        "/api/steer",
        {
            "next_goal": "region-pretrain",
            "pause": False,
            "note": "LatentVAE WikiText-2 train; failing test first",
        },
    )
    assert code == 200
    assert rec["next_goal"] == "region-pretrain"
    got = json.loads((tmp_path / "csd-steer.json").read_text(encoding="utf-8"))
    assert got["next_goal"] == "region-pretrain"
    assert "region-pretrain" in got["next_goal"]
    code, rec = mod.handle_lab("GET", "/api/steer", {})
    assert code == 200
    assert rec["next_goal"] == "region-pretrain"
    code, goals = mod.handle_lab("GET", "/api/goals", {})
    assert code == 200
    assert goals["next_goal"] == "region-pretrain"
    assert any(row["id"] == "region-pretrain" for row in goals["todos"])
    assert mod.WORKTREES["region-pretrain"] == mod.ROOT


def test_api_goals_phase1_steer_heartbeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/goals returns PHASE-1 board, next_goal, heartbeat, notes."""
    _needs("benchmark_results/scale_ladder.json")
    mod = load_lab(tmp_path, monkeypatch)
    (tmp_path / "csd-steer.json").write_text(
        json.dumps(
            {
                "pause": False,
                "next_goal": "P1-09",
                "note": "retrieve domain isolation",
                "reasoning": "P1-08 merged; next closeable is P1-09",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "hb.json").write_text(
        json.dumps(
            {
                "t": time.time(),
                "pid": 1,
                "wt": str(tmp_path / "p1-09"),
                "last": {"ok": True, "goal": "P1-09", "applied": {"ok": True}},
            }
        ),
        encoding="utf-8",
    )
    code, rec = mod.handle_lab("GET", "/api/goals", {})
    assert code == 200
    assert rec["ok"] is True
    assert rec["next_goal"] == "P1-09"
    assert rec["notes"] == "retrieve domain isolation"
    assert "P1-09" in rec["reasoning"]
    assert rec["heartbeat"]["live"] is True
    assert rec["heartbeat"]["identity"] == "autodev"
    assert rec["heartbeat"]["next_goal"] == "P1-09"
    assert rec["heartbeat"]["last"]["goal"] == "P1-09"
    assert rec["heartbeat"]["last"]["ok"] is True
    assert rec["heartbeat"]["last"]["error"] == ""
    assert rec["heartbeat"]["cluster"] == {}
    ids = [row["id"] for row in rec["phase1"]]
    assert "P1-08" in ids
    assert "P1-09" in ids
    by_id = {row["id"]: row for row in rec["phase1"]}
    assert by_id["P1-09"]["status"] == "next"
    assert by_id["P1-08"]["status"] == "done"
    assert by_id["P1-05"]["status"] == "blocked"
    assert any(t["id"] == "P1-09" for t in rec["todos"])
    assert rec["hf"]["autodev"] is False
    assert rec["hf"]["gap"] == "mint HF"
    assert rec["hf"]["never_copy"] == "gpu/huggingface-token"
    assert "tzervas/cogsyndelta-tiny" in rec["hf"]["repos"]
    assert "tzervas/cogsyndelta-region-stream_vae-tiny" in rec["hf"]["repos"]
    assert rec["scale_ladder"]["ok"] is True
    assert rec["scale_ladder"]["any_green"] is False
    sizes = [row["size"] for row in rec["scale_ladder"]["rungs"]]
    assert sizes[0] == "region_pretrain"
    assert sizes == ["region_pretrain", "router", "tiny_mind", "small", "medium"]
    assert all(row.get("green") is False for row in rec["scale_ladder"]["rungs"])


def test_goals_tab_renders_from_api_goals_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Goals/Todos tab fetches /api/goals and does not iframe Open WebUI."""
    mod = load_lab(tmp_path, monkeypatch)
    page = mod.PAGE
    assert "data-tab=todos" in page
    assert "/api/goals" in page
    assert "function loadGoals" in page or "async function loadGoals" in page
    assert "renderGoals" in page
    todos_start = page.index("id=todos")
    chat_start = page.index("id=chat")
    todos_html = page[todos_start:chat_start]
    assert "ai.example.com" not in todos_html
    assert "iframe" not in todos_html
    assert "id=gladder" in todos_html
    assert "id=gping" in todos_html
    assert "scale_ladder" in page
    assert "grok_need" in page or "gping" in page


def test_metrics_scale_ladder_gauge_stays_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /metrics gauge is 0 while scale_ladder.json green is false."""
    _needs("benchmark_results/scale_ladder.json")
    mod = load_lab(tmp_path, monkeypatch)
    code, rec = mod.handle_lab("GET", "/metrics", {})
    assert code == 200
    text = rec["exposition"]
    assert "csd_scale_ladder_rung_green" in text
    assert 'rung="0",size="region_pretrain"} 0' in text
    assert 'rung="1",size="router"} 0' in text
    assert 'rung="2",size="tiny_mind"} 0' in text
    assert 'rung="3",size="small"} 0' in text
    assert 'rung="4",size="medium"} 0' in text
    assert 'csd_need_grok{identity="autodev"} 0' in text
    assert 'csd_need_grok{identity="autodev"} 1' not in text
    assert "csd_need_grok_mtime_seconds" in text


def test_hf_autodev_present_when_file_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """hf/autodev file in CSD vault clears the mint-HF gap."""
    mod = load_lab(tmp_path, monkeypatch)
    vault = tmp_path / "csd-vault"
    (vault / "hf" / "autodev").write_text("placeholder-not-a-token\n", encoding="utf-8")
    rec = mod.hf_autodev_gap()
    assert rec["autodev"] is True
    assert rec["gap"] is None


def test_hf_autodev_refuses_operator_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never treat ~/.secrets as the CSD vault."""
    mod = load_lab(tmp_path, monkeypatch)
    monkeypatch.setenv("CSD_VAULT", str(Path.home() / ".secrets"))
    rec = mod.hf_autodev_gap()
    assert rec["autodev"] is False
    assert rec["gap"] == "mint HF"
    assert rec["vault"] == "refused-operator-vault"


def test_grok_need_flag_off_refuses_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default CSD_NEED_GROK=off: POST cannot set need=true."""
    monkeypatch.delenv("CSD_NEED_GROK", raising=False)
    monkeypatch.delenv("CSD_NEED_GROK_ENABLED", raising=False)
    mod = load_lab(tmp_path, monkeypatch)
    code, rec = mod.handle_lab(
        "POST",
        "/api/grok-need",
        {"need": True, "question": "should not land"},
    )
    assert code == 200
    assert rec["need"] is False
    assert rec["enabled"] is False
    code, rec = mod.handle_lab("GET", "/metrics", {})
    assert 'csd_need_grok{identity="autodev"} 0' in rec["exposition"]
    assert "csd_need_grok_enabled 0" in rec["exposition"]


def test_grok_need_get_idle_and_post_caps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET idle; POST need=true writes mailbox; transcripts dropped; 2 KiB cap."""
    monkeypatch.setenv("CSD_NEED_GROK", "1")
    mod = load_lab(tmp_path, monkeypatch)
    code, rec = mod.handle_lab("GET", "/api/grok-need", {})
    assert code == 200
    assert rec["need"] is False
    assert rec["hosted_grok"] is False
    assert rec["identity"] == "autodev"
    code, rec = mod.handle_lab(
        "POST",
        "/api/grok-need",
        {
            "need": True,
            "question": "Forgejo required pytest failed twice?",
            "evidence": "blocker: CI red; paths: tests/test_x.py; tried: wait x2",
            "goal": "P1-09",
            "transcript": "never dump a chat",
            "stdout": "pytest spew",
        },
    )
    assert code == 200
    assert rec["need"] is True
    assert rec["identity"] == "autodev"
    assert rec["hosted_grok"] is False
    assert "transcript" not in rec
    assert "stdout" not in rec
    raw = (tmp_path / "csd-need-grok.json").read_bytes()
    assert len(raw) <= 2048
    code, rec = mod.handle_lab("GET", "/api/goals", {})
    assert rec["grok_need"]["need"] is True
    code, rec = mod.handle_lab("GET", "/metrics", {})
    text = rec["exposition"]
    assert 'csd_need_grok{identity="autodev"} 1' in text
    mtime_line = [
        ln for ln in text.splitlines() if ln.startswith("csd_need_grok_mtime_seconds")
    ]
    assert mtime_line
    assert float(mtime_line[0].rsplit(" ", 1)[1]) > 0
    code, rec = mod.handle_lab("POST", "/api/grok-need", {"need": False})
    assert rec["need"] is False
    code, rec = mod.handle_lab("GET", "/metrics", {})
    assert 'csd_need_grok{identity="autodev"} 0' in rec["exposition"]


def test_grok_need_post_does_not_spawn_grok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mailbox write must not call hosted grok or GitHub."""
    monkeypatch.setenv("CSD_NEED_GROK", "1")
    mod = load_lab(tmp_path, monkeypatch)

    def boom(*_a: object, **_k: object) -> tuple[int, str]:
        raise AssertionError("grok-need must not spawn CLI")

    monkeypatch.setattr(mod, "_run_cli", boom)
    monkeypatch.setattr(mod, "sh", boom)
    code, rec = mod.handle_lab(
        "POST",
        "/api/grok-need",
        {"need": True, "question": "HF mint?", "evidence": "empty", "goal": "P1-15"},
    )
    assert code == 200
    assert rec["need"] is True
    assert rec["hosted_grok"] is False


def test_live_feed_pool_combined_when_1080ti_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """combined_mib sums live catalog VRAM; never the 24+16 39331 default."""
    mod = load_lab(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "ssh_5080", lambda _cmd: "")
    (tmp_path / "gpu-plan.json").write_text(
        json.dumps({"pool": {"combined_mib": 39331, "usable_mib": 37795}}),
        encoding="utf-8",
    )
    rec = mod.live_feed(
        {
            "prime_smi": "RTX 3090 Ti, 100, 22900, 0",
            "host_gpu_b_smi": "RTX 5080, 10, 16200, 0",
            "guest_smi": "NVIDIA GeForce GTX 1080 Ti, 12, 11200, 0",
            "cluster": mod.cluster_snapshot(),
        }
    )
    want = 23028 + 16303 + 11264
    assert rec["pool"]["combined_mib"] == want
    assert rec["pool"]["combined_mib"] >= 50000
    assert rec["pool"]["combined_mib"] != 39331
    assert rec["pool"]["usable_mib"] == 20480 + 14336 + 10240
    assert rec["pool"]["live_1080ti"] is True
    ids = [c["id"] for c in rec["pool"]["cards"]]
    assert ids == ["host-a", "host-gpu-b", "host-gpu-b-1080ti"]
    gibs = [c["gib"] for c in rec["pool"]["cards"]]
    assert gibs == [22.5, 15.9, 11.0]
    ti = rec["host_gpu_c"]
    assert ti["gpu"] == "GTX 1080 Ti Pascal sm_61"
    assert "1080 Ti" in (ti.get("smi") or "")
    assert ti["live"] is True
    assert ti["guest_ip"] == "203.0.113.12"
    assert ti["rag"] == "retrieve-index-light"
    assert rec["prime"]["smi"].startswith("RTX 3090")
    assert rec["host-gpu-b"]["smi"].startswith("RTX 5080")


def test_live_feed_pool_excludes_1080ti_when_not_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1080 Ti VRAM is omitted when catalog live is not true."""
    mod = load_lab(tmp_path, monkeypatch)
    cat = json.loads((ROOT / "config" / "model-router.json").read_text(encoding="utf-8"))
    cat["hosts"]["host-gpu-b-1080ti"]["live"] = False
    path = tmp_path / "model-router.json"
    path.write_text(json.dumps(cat), encoding="utf-8")
    mod.ROUTER_JSON = path
    rec = mod.live_feed({})
    assert rec["pool"]["combined_mib"] == 23028 + 16303
    assert rec["pool"]["usable_mib"] == 20480 + 14336
    assert rec["pool"]["live_1080ti"] is False
    assert [c["id"] for c in rec["pool"]["cards"]] == ["host-a", "host-gpu-b"]


def test_pool_tab_lists_three_cards_not_24_16(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pool HTML lists three cards + GiB and drops stale 24:16 copy."""
    mod = load_lab(tmp_path, monkeypatch)
    page = mod.PAGE
    assert "id=c2" in page
    assert "host_gpu_c" in page
    assert "Three hosts" in page
    assert "p.cards" in page
    assert "c.gib" in page
    assert "24:16" not in page
    assert "39331" not in page
    assert "~40 GiB class" not in page
    assert "cols3" in page


def test_snapshot_status_feed_pool_includes_guest_smi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/status path (snapshot) combined >= 50000 when 1080ti live."""
    import urllib.error

    mod = load_lab(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "sh", lambda *_a, **_k: "RTX 3090 Ti, 100, 22900, 0")
    monkeypatch.setattr(mod, "ssh_5080", lambda _cmd: "")
    monkeypatch.setattr(
        mod,
        "ssh_guest",
        lambda *_a, **_k: "NVIDIA GeForce GTX 1080 Ti, 12, 11200, 0",
    )
    monkeypatch.setattr(mod, "autodev_app", lambda: {"worker_live": False})

    def _down(*_a: object, **_k: object) -> None:
        raise urllib.error.URLError("lab-test")

    monkeypatch.setattr(mod.urllib.request, "urlopen", _down)
    rec = mod.snapshot()
    pool = rec["feed"]["pool"]
    assert pool["combined_mib"] >= 50000
    assert pool["combined_mib"] == 23028 + 16303 + 11264
    assert rec["guest_smi"].startswith("NVIDIA GeForce GTX 1080 Ti")
    assert rec["feed"]["host_gpu_c"]["smi"].startswith("NVIDIA GeForce GTX 1080 Ti")
    assert rec["feed"]["host_gpu_c"]["live"] is True


def test_autodev_tab_streams_think_or_inflight_from_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Autodev pane uses splitThink + in-flight pre from queue/out, not ticks-only."""
    mod = load_lab(tmp_path, monkeypatch)
    qdir = tmp_path / "localai-queue"
    qdir.mkdir()
    (qdir / "job1.json").write_text(
        json.dumps(
            {
                "id": "job1",
                "status": "running",
                "kind": "autodev",
                "alias": "local/code",
                "prompt": "implement region-pretrain LatentVAE WikiText-2",
                "prio": 0,
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "autodev-out"
    out.mkdir()
    (out / "1.json").write_text(
        json.dumps(
            {
                "goal": "region-pretrain",
                "ok": False,
                "pytest_rc": 1,
                "text": (
                    "<think>need failing test first</think>"
                    "patch tests/test_poc_region_pretrain.py"
                ),
                "applied": {"ok": False, "path": "tests/test_poc_region_pretrain.py"},
                "error": "required pytest red",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "hb.json").write_text(
        json.dumps(
            {
                "t": time.time(),
                "pid": 9,
                "identity": "autodev",
                "next_goal": "region-pretrain",
                "last": {
                    "ok": False,
                    "goal": "region-pretrain",
                    "error": "required pytest red",
                    "pytest_rc": 1,
                    "applied": {"path": "tests/test_poc_region_pretrain.py"},
                },
            }
        ),
        encoding="utf-8",
    )
    rec = mod.autodev_app()
    now = rec["now"]
    assert now["phase"] == "in-flight"
    assert "region-pretrain" in now["prompt"]
    assert now["inflight"] == now["prompt"]
    assert "<think>" in now["text"]
    assert now["goal"] == "region-pretrain"
    assert "tests/test_poc_region_pretrain.py" in now["files"]
    assert now["pytest_rc"] == 1
    assert now["ok"] is False
    assert "pytest" in now["error"]
    assert rec["heartbeat"]["last"]["goal"] == "region-pretrain"
    assert rec["heartbeat"]["last"]["ok"] is False
    assert rec["heartbeat"]["last"]["error"]
    assert rec["queue_live"][0]["prompt"]
    page = mod.PAGE
    auto_html = page[page.index("id=auto") : page.index("id=todos")]
    assert "id=anow" in auto_html
    assert "id=ameta" in auto_html
    assert "What the model is doing now" in auto_html
    assert "Goals board stays on the Goals tab" in auto_html
    assert "function renderAuto" in page
    assert "splitThink(now.text" in page
    assert "<h3>thinking</h3><pre>" in page
    assert "<h3>in-flight</h3><pre>" in page
    assert "setInterval(refresh,2000)" in page
    assert "data-tab=todos" in page
    assert "function loadGoals" in page
    todos_html = page[page.index("id=todos") : page.index("id=chat")]
    assert "/api/goals" in page
    assert "PHASE-1 board" in todos_html
    assert "iframe" not in todos_html


def test_gpu_tab_5080_receipt_and_1080ti_thinking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GPUs tab keeps prime think; 5080 shows cuda receipt; 1080 Ti loaded/thinking."""
    mod = load_lab(tmp_path, monkeypatch)
    (tmp_path / "gpu-plan.json").write_text(
        json.dumps(
            {
                "host-gpu-b": {
                    "lock": "lock=1234",
                    "comfy": "masked",
                    "helper_ok": False,
                    "gguf_count": 2,
                    "role": "cuda-tests",
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "model-router-state.json").write_text(
        json.dumps(
            {
                "resident": {
                    "embed-qwen3-0.6b": {
                        "host": "host-gpu-b-1080ti",
                        "until": time.time() + 3600,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    qdir = tmp_path / "localai-queue"
    qdir.mkdir()
    (qdir / "prime.json").write_text(
        json.dumps(
            {
                "id": "prime",
                "status": "running",
                "kind": "autodev",
                "alias": "local/code",
                "prompt": "<think>slice</think>write failing test",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "ssh_5080", lambda _cmd: "")
    rec = mod.live_feed(
        {
            "prime_smi": "RTX 3090 Ti, 100, 22900, 0",
            "host_gpu_b_smi": "RTX 5080, 10, 16200, 0",
            "host_gpu_b_lock": "1234",
            "host_gpu_b_comfy": "masked",
            "host_gpu_b_runner": "active",
            "guest_smi": "NVIDIA GeForce GTX 1080 Ti, 12, 11200, 0",
            "cluster": mod.cluster_snapshot(),
        }
    )
    prime_th = rec["prime"]["thinking"]
    assert prime_th["phase"] == "in-flight"
    assert "failing test" in (prime_th.get("prompt") or "")
    g = rec["host-gpu-b"]["thinking"]
    assert g is not None
    assert "lock=" in (g.get("prompt") or g.get("text") or "")
    assert "comfy=masked" in (g.get("text") or g.get("prompt") or "")
    assert g.get("goal") == "exclusive-cuda"
    ti = rec["host_gpu_c"]
    assert any(x.get("alias") == "embed-qwen3-0.6b" for x in ti["loaded"])
    tth = ti["thinking"]
    assert tth is not None
    assert "embed-qwen3-0.6b" in (tth.get("prompt") or tth.get("text") or "")
    assert tth.get("goal") == "retrieve-index-light"
    assert ti["live"] is True
    assert ti["guest_ip"] == "203.0.113.12"


def test_root_redirects_to_lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bare host / must 302 to /lab (code.example.com without suffix)."""
    mod = load_lab(tmp_path, monkeypatch)
    assert mod.lab_root_redirect("/") == "/lab"
    assert mod.lab_root_redirect("/goals") == "/lab"
    assert mod.lab_root_redirect("/lab") is None
    assert mod.lab_root_redirect("/api/status") is None
    assert "const BASE='/lab'" in mod.PAGE
    assert "fetch(BASE+'/api/chat'" in mod.PAGE


def test_gpu_tab_shows_roles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Lab GPU pane documents 3090 autodev / 5080 chat+Comfy / 1080 RAG."""
    mod = load_lab(tmp_path, monkeypatch)
    page = mod.PAGE
    assert "role" in page
    assert "autodev local/code" in page
    assert "chat+Comfy" in page
    assert "retrieve-index-light" in page
