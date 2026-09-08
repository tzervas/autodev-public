"""Thin CLI over the harness HTTP API. Subcommands require a live transport."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Sequence
from typing import Any

from csd_client.api import Client
from csd_client.transport import TransportError

# Registered operator subcommands — every one must fail closed without HTTP (R1-A3).
SUBCOMMANDS: tuple[str, ...] = (
    "status",
    "watch",
    "chat",
    "steer",
    "steer-note",
    "steer-next",
    "runs",
    "agents",
    "sessions",
    "leases",
    "tui",
)


def _print(data: Any) -> None:
    if isinstance(data, str):
        print(data)
    else:
        print(json.dumps(data, indent=2))


def _client(ns: argparse.Namespace) -> Client:
    return Client.from_env(getattr(ns, "api", None))


def cmd_status(ns: argparse.Namespace) -> int:
    _print(_client(ns).get_status())
    return 0


def cmd_watch(ns: argparse.Namespace) -> int:
    client = _client(ns)
    while True:
        print("\033[2J\033[H", end="")
        _print(client.get_status())
        time.sleep(float(ns.interval))


def cmd_chat(ns: argparse.Namespace) -> int:
    rec = _client(ns).chat(ns.prompt)
    if "content" in rec:
        print(rec["content"])
    else:
        _print(rec)
    return 0


def cmd_steer(ns: argparse.Namespace) -> int:
    action = ns.action
    if action == "pause":
        _print(_client(ns).post_steer({"pause": True}))
    elif action == "resume":
        _print(_client(ns).post_steer({"pause": False}))
    elif action == "show":
        _print(_client(ns).get_steer())
    else:
        print(f"unknown steer action: {action}", file=sys.stderr)
        return 2
    return 0


def cmd_steer_note(ns: argparse.Namespace) -> int:
    _print(_client(ns).post_steer({"note": ns.note}))
    return 0


def cmd_steer_next(ns: argparse.Namespace) -> int:
    _print(_client(ns).post_steer({"next_goal": ns.goal}))
    return 0


def _format_feed_line(ev: dict[str, Any]) -> str:
    kind = str(ev.get("kind") or "?")
    seq = ev.get("seq")
    payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
    text = payload.get("text")
    if text is None:
        text = payload.get("content")
    if text is None:
        text = json.dumps(payload, separators=(",", ":")) if payload else ""
    cp = ev.get("checkpoint_sha256") or ""
    cp_pref = (str(cp)[:8] + "…") if cp else "-"
    return f"[{seq}] {kind} cp={cp_pref} {text}"


def cmd_runs_attach(ns: argparse.Namespace) -> int:
    """Print the SSE feed; stdin lines → POST /input (same field as the TUI)."""
    import select
    import threading

    client = _client(ns)
    run_id = ns.run_id
    after = int(getattr(ns, "after", 0) or 0)
    stop = threading.Event()

    def _stdin_loop() -> None:
        while not stop.is_set():
            try:
                ready, _, _ = select.select([sys.stdin], [], [], 0.5)
            except (OSError, ValueError):
                break
            if not ready:
                continue
            line = sys.stdin.readline()
            if line == "":
                stop.set()
                break
            text = line.rstrip("\n")
            if not text:
                continue
            try:
                client.post_input(run_id, {"text": text})
            except TransportError as exc:
                print(f"transport error: {exc}", file=sys.stderr)
                stop.set()
                break

    reader = threading.Thread(target=_stdin_loop, daemon=True)
    reader.start()
    try:
        for ev in client.iter_events(run_id, after=after, timeout=3600.0):
            print(_format_feed_line(ev), flush=True)
            if stop.is_set():
                break
    finally:
        stop.set()
    return 0


def cmd_runs(ns: argparse.Namespace) -> int:
    client = _client(ns)
    if ns.runs_cmd == "list":
        _print(client.list_runs())
        return 0
    if ns.runs_cmd == "show":
        _print(client.get_run(ns.run_id))
        return 0
    if ns.runs_cmd == "attach":
        return cmd_runs_attach(ns)
    print("runs requires list|show|attach", file=sys.stderr)
    return 2


def cmd_agents(ns: argparse.Namespace) -> int:
    client = _client(ns)
    if ns.agents_cmd == "list":
        _print(client.list_agents())
        return 0
    if ns.agents_cmd == "show":
        _print(client.get_agent(ns.agent_id))
        return 0
    print("agents requires list|show", file=sys.stderr)
    return 2


def cmd_sessions(ns: argparse.Namespace) -> int:
    client = _client(ns)
    if ns.sessions_cmd == "list":
        _print(client.list_sessions())
        return 0
    if ns.sessions_cmd == "show":
        _print(client.get_session(ns.session_id))
        return 0
    print("sessions requires list|show", file=sys.stderr)
    return 2


def cmd_leases(ns: argparse.Namespace) -> int:
    client = _client(ns)
    if ns.leases_cmd == "list":
        _print(client.list_leases())
        return 0
    if ns.leases_cmd == "show":
        _print(client.get_lease(ns.lease_id))
        return 0
    print("leases requires list|show", file=sys.stderr)
    return 2


def cmd_tui(ns: argparse.Namespace) -> int:
    """Launch textual TUI (HTTP+SSE only). Lazy-import keeps CLI free of textual cost."""
    from csd_client.tui import AutodevApp

    # Fail closed if API is down before opening the UI (R1-A3).
    client = _client(ns)
    client.list_runs()
    app = AutodevApp(
        client=client,
        initial_run_id=getattr(ns, "run_id", None),
    )
    app.run()
    return 0


HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "status": cmd_status,
    "watch": cmd_watch,
    "chat": cmd_chat,
    "steer": cmd_steer,
    "steer-note": cmd_steer_note,
    "steer-next": cmd_steer_next,
    "runs": cmd_runs,
    "agents": cmd_agents,
    "sessions": cmd_sessions,
    "leases": cmd_leases,
    "tui": cmd_tui,
}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="csd",
        description="Thin HTTP client for the csd-autodev harness API.",
    )
    ap.add_argument(
        "--api",
        default=None,
        help="Harness API base (default CSD_API or http://203.0.113.10:9118)",
    )
    # Legacy flag forms used by scripts/csd-lab-console wrappers.
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--chat", dest="legacy_chat", default=None)
    ap.add_argument("--steer", dest="legacy_steer", choices=["pause", "resume"])
    ap.add_argument("--steer-note", dest="legacy_steer_note", default=None)
    ap.add_argument("--steer-next", dest="legacy_steer_next", default=None)
    ap.add_argument("--interval", type=float, default=5.0)

    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("status", help="GET /api/status")
    p_watch = sub.add_parser("watch", help="Poll GET /api/status")
    p_watch.add_argument("--interval", type=float, default=5.0)

    p_chat = sub.add_parser("chat", help="POST /api/chat")
    p_chat.add_argument("prompt")

    p_steer = sub.add_parser("steer", help="GET/POST /api/steer")
    p_steer.add_argument("action", choices=["pause", "resume", "show"])

    p_note = sub.add_parser("steer-note", help="POST /api/steer note=")
    p_note.add_argument("note")

    p_next = sub.add_parser("steer-next", help="POST /api/steer next_goal=")
    p_next.add_argument("goal")

    p_runs = sub.add_parser("runs", help="Runs resource")
    p_runs_sub = p_runs.add_subparsers(dest="runs_cmd")
    p_runs_sub.add_parser("list")
    p_show = p_runs_sub.add_parser("show")
    p_show.add_argument("run_id")
    p_attach = p_runs_sub.add_parser(
        "attach", help="SSE feed + stdin → POST /api/runs/{id}/input"
    )
    p_attach.add_argument("run_id")
    p_attach.add_argument(
        "--after",
        type=int,
        default=0,
        help="Resume after this seq (gapless)",
    )

    p_agents = sub.add_parser("agents", help="Agents resource")
    p_agents_sub = p_agents.add_subparsers(dest="agents_cmd")
    p_agents_sub.add_parser("list")
    p_ashow = p_agents_sub.add_parser("show")
    p_ashow.add_argument("agent_id")

    p_sess = sub.add_parser("sessions", help="Sessions resource")
    p_sess_sub = p_sess.add_subparsers(dest="sessions_cmd")
    p_sess_sub.add_parser("list")
    p_sshow = p_sess_sub.add_parser("show")
    p_sshow.add_argument("session_id")

    p_leases = sub.add_parser("leases", help="Leases resource")
    p_leases_sub = p_leases.add_subparsers(dest="leases_cmd")
    p_leases_sub.add_parser("list")
    p_lshow = p_leases_sub.add_parser("show")
    p_lshow.add_argument("lease_id")

    p_tui = sub.add_parser("tui", help="Textual live I/O feed (HTTP+SSE)")
    p_tui.add_argument("--run", dest="run_id", default=None, help="Open run_id")

    return ap


def _normalize_legacy(ns: argparse.Namespace) -> argparse.Namespace:
    """Map legacy --steer/--chat/--watch flags onto subcommand fields."""
    if ns.cmd:
        return ns
    if ns.legacy_steer:
        ns.cmd = "steer"
        ns.action = ns.legacy_steer
        return ns
    if ns.legacy_steer_note is not None:
        ns.cmd = "steer-note"
        ns.note = ns.legacy_steer_note
        return ns
    if ns.legacy_steer_next is not None:
        ns.cmd = "steer-next"
        ns.goal = ns.legacy_steer_next
        return ns
    if ns.legacy_chat is not None:
        ns.cmd = "chat"
        ns.prompt = ns.legacy_chat
        return ns
    if ns.watch:
        ns.cmd = "watch"
        return ns
    ns.cmd = "status"
    return ns


def main(argv: Sequence[str] | None = None) -> int:
    argv_list = list(sys.argv[1:] if argv is None else argv)
    ap = build_parser()
    ns = _normalize_legacy(ap.parse_args(argv_list))
    handler = HANDLERS.get(ns.cmd or "")
    if handler is None:
        ap.print_help()
        return 2
    try:
        return int(handler(ns))
    except TransportError as exc:
        print(f"transport error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
