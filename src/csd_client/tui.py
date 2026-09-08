"""Textual TUI: live I/O feed + one operator composer (R1-A2 / TUI-DESIGN).

HTTP+SSE client only. Importing this module must not load the loop library.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from typing import Any, ClassVar

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Static,
)

from csd_client.api import Client
from csd_client.transport import TransportError

CSS = """
Screen {
    layout: vertical;
}
#body {
    height: 1fr;
}
#runs {
    width: 28;
    min-width: 20;
    border: solid $accent;
    height: 1fr;
}
#runs-title {
    dock: top;
    padding: 0 1;
    background: $accent;
    color: $text;
}
#main {
    width: 1fr;
    height: 1fr;
}
#feed {
    height: 1fr;
    border: solid $primary;
}
#composer-row {
    height: auto;
    dock: bottom;
}
#composer {
    width: 1fr;
}
#hint {
    color: $text-muted;
    padding: 0 1;
}
RunItem {
    padding: 0 1;
}
"""


def _cp_prefix(raw: object) -> str:
    text = str(raw or "").strip()
    if not text:
        return "-"
    return text[:8]


def _feed_line(ev: dict[str, Any]) -> str:
    kind = str(ev.get("kind") or "?")
    seq = ev.get("seq")
    payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
    text = payload.get("text")
    if text is None:
        text = payload.get("content")
    if text is None and payload:
        text = str(payload)
    if text is None:
        text = ""
    one = " ".join(str(text).split())
    if len(one) > 200:
        one = one[:197] + "..."
    return f"[{seq}] {kind}: {one}"


class RunItem(ListItem):
    def __init__(self, run: dict[str, Any]) -> None:
        self.run_id = str(run.get("run_id") or "")
        self.run = run
        label = (
            f"{self.run_id}\n"
            f"cp={_cp_prefix(run.get('checkpoint_sha256'))} "
            f"{run.get('status') or '?'}"
        )
        super().__init__(Label(label))


class AutodevApp(App[None]):
    """Narrow run list + live feed + one composer (halt/steer/handover prefixes)."""

    CSS = CSS
    TITLE = "csd-autodev"
    # ClassVar: a mutable class attribute without it is SHARED by every
    # instance, so one app mutating its bindings would change them for all.
    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("q", "quit", "Quit"),
        ("r", "refresh_runs", "Refresh"),
    ]

    def __init__(
        self,
        client: Client | None = None,
        *,
        api: str | None = None,
        initial_run_id: str | None = None,
    ) -> None:
        super().__init__()
        self.client = client or Client.from_env(api)
        self.initial_run_id = initial_run_id
        self.active_run_id: str | None = None
        self._event_after = 0
        self._poll = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            with Vertical(id="runs"):
                yield Static("runs", id="runs-title")
                yield ListView(id="run-list")
            with Vertical(id="main"):
                yield RichLog(id="feed", highlight=True, markup=False)
                yield Static(
                    "composer: text | /halt | /take-control | /hand-back",
                    id="hint",
                )
                yield Input(
                    placeholder="operator field — Enter to send",
                    id="composer",
                )
        yield Footer()

    def on_mount(self) -> None:
        self.action_refresh_runs()
        if self.initial_run_id:
            self.open_run(self.initial_run_id)
        self.set_interval(0.5, self._poll_events)

    def action_refresh_runs(self) -> None:
        try:
            rec = self.client.list_runs()
        except TransportError as exc:
            self.query_one("#feed", RichLog).write(f"transport error: {exc}")
            return
        runs = rec.get("runs") if isinstance(rec, dict) else None
        if not isinstance(runs, list):
            runs = []
        listing = self.query_one("#run-list", ListView)
        listing.clear()
        for run in runs:
            if isinstance(run, dict) and run.get("run_id"):
                listing.append(RunItem(run))
        if self.initial_run_id and self.active_run_id is None:
            self.open_run(self.initial_run_id)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, RunItem) and item.run_id:
            self.open_run(item.run_id)

    def open_run(self, run_id: str) -> None:
        self.active_run_id = run_id
        self._event_after = 0
        feed = self.query_one("#feed", RichLog)
        feed.clear()
        feed.write(f"— open {run_id} —")
        try:
            detail = self.client.get_run(run_id)
        except TransportError as exc:
            feed.write(f"transport error: {exc}")
            return
        status = detail.get("status") if isinstance(detail, dict) else "?"
        cp = _cp_prefix(
            detail.get("checkpoint_sha256") if isinstance(detail, dict) else None
        )
        feed.write(f"status={status} checkpoint={cp}")
        self._fetch_events()

    def _poll_events(self) -> None:
        if self.active_run_id:
            self._fetch_events()

    def _fetch_events(self) -> None:
        run_id = self.active_run_id
        if not run_id:
            return
        try:
            rec = self.client.list_events(run_id, after=self._event_after)
        except TransportError:
            return
        events = rec.get("events") if isinstance(rec, dict) else None
        if not isinstance(events, list):
            return
        feed = self.query_one("#feed", RichLog)
        for ev in events:
            if not isinstance(ev, dict):
                continue
            feed.write(_feed_line(ev))
            with contextlib.suppress(TypeError, ValueError):
                self._event_after = max(self._event_after, int(ev.get("seq") or 0))

    @work(thread=True, exclusive=True, group="send")
    def _send_input(self, run_id: str, text: str) -> None:
        try:
            self.client.post_input(run_id, {"text": text})
        except TransportError as exc:
            self.call_from_thread(
                self.query_one("#feed", RichLog).write, f"send error: {exc}"
            )
            return
        self.call_from_thread(self._fetch_events)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text or not self.active_run_id:
            return
        self._send_input(self.active_run_id, text)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="csd tui", description="csd-autodev textual TUI")
    ap.add_argument("--api", default=None, help="Harness API base")
    ap.add_argument("--run", dest="run_id", default=None, help="Open this run_id")
    return ap


def run_app(
    argv: list[str] | None = None,
    *,
    client: Client | None = None,
) -> None:
    ns = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    app = AutodevApp(client=client, api=ns.api, initial_run_id=ns.run_id)
    app.run()


def main(argv: list[str] | None = None) -> int:
    run_app(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
