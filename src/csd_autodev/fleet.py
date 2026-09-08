"""Resolve fleet configuration: safe defaults, a private override, the environment.

WHY. The fleet's real addresses were hardcoded across twenty-odd scripts and
docs — 78 IP hits across 26 files. Publishing the repository then meant rewriting
them at publish time, a step that has to be right *every* time and was wrong
twice on its first run. Parameterised, the public tree never contains them, and
correctness stops depending on remembering to scrub.

PRECEDENCE, lowest first:

1. ``config/fleet.example.json`` — committed, documentation-safe, always present.
   RFC 5737 addresses and RFC 2606 domains, so a copy-paste reaches nothing.
2. ``config/fleet.json`` — gitignored, the real values. Absent on a fresh clone,
   which is why the defaults must be *runnable* rather than placeholders that
   crash.
3. ``AUTODEV_*`` environment variables — one-off overrides, and how a systemd
   unit pins a value without editing a file.

SECRETS ARE NOT IN ANY OF THEM. These files hold addresses and names. Credentials
are injected at run time by ``secret exec VAR=name -- cmd`` from the agent's own
vault, so they are never in a file, never in argv, and never in this repository.
A ``*_secret`` key names the vault ENTRY to read, not the value — and
:func:`secret_name` exists to make that distinction impossible to blur.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "config" / "fleet.example.json"
OVERRIDE = Path(os.environ.get("AUTODEV_FLEET_CONFIG", ROOT / "config" / "fleet.json"))

# environment variable -> dotted path into the config
ENV_MAP = {
    "AUTODEV_FORGE_API": "forge.api",
    "AUTODEV_GATEWAY_URL": "gateway.url",
    "AUTODEV_CONTEXT_WINDOW": "gateway.context_window",
    "AUTODEV_EVENT_BIND": "events.bind",
    "AUTODEV_EVENT_PORT": "events.port",
    "AUTODEV_EVENT_DIR": "events.dir",
    "AUTODEV_STATE_DIR": "paths.state",
    "AUTODEV_WORK_DIR": "paths.work",
    "AUTODEV_MAIL_DIR": "paths.mail",
}


def path(dotted: str, env: str = "", *, child: str = "") -> Path:
    """A filesystem path from the environment, else the config. Never hardcoded.

    Every script grew its own `os.environ.get("AUTODEV_X", "/home/svc-autodev/x")`
    line, which puts one deployment's layout in twenty files. The config already
    declares `paths.state`, `paths.work`, `paths.mail` and `events.dir`; this is
    how a script reaches them, so moving the agent means editing ONE file rather
    than finding every default that agreed with the old one.

    `env` still wins when set, because that is how a systemd unit pins a value
    without editing anything.

        fleet.path("paths.state", "AUTODEV_STATE")
        fleet.path("paths.state", "AUTODEV_STATE", child="calls.jsonl")
    """
    raw = (os.environ.get(env) if env else "") or str(get(dotted) or "")
    if not raw:
        raise KeyError(
            f"no path configured for {dotted!r} and ${env or '(none)'} is unset; "
            "config/fleet.example.json should always supply a default"
        )
    return Path(raw) / child if child else Path(raw)


def _merge(base: dict, over: dict) -> dict:
    """Deep merge, so an override may set one key without restating a section."""
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _put(cfg: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = cfg
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = value


def load() -> dict:
    """The resolved configuration.

    A missing or unreadable override is not an error: a fresh clone has none, and
    the committed defaults are chosen so everything still runs against nothing
    real. Failing here would make the repository unusable to anyone but us, which
    is the opposite of the point.
    """
    cfg: dict = {}
    try:
        cfg = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    with contextlib.suppress(OSError, ValueError):
        cfg = _merge(cfg, json.loads(OVERRIDE.read_text(encoding="utf-8")))
    for env, dotted in ENV_MAP.items():
        raw = os.environ.get(env)
        if raw is None or raw == "":
            continue
        _put(cfg, dotted, int(raw) if raw.isdigit() else raw)
    return cfg


def get(dotted: str, default: Any = None) -> Any:
    """One value by dotted path: ``get("gateway.url")``."""
    node: Any = load()
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def secret_name(dotted: str) -> str:
    """The vault ENTRY NAME at this path — never a secret value.

    Separate from :func:`get` on purpose. A caller that wants a credential has to
    say so, and what it receives is a name to hand to ``secret exec``. There is
    no code path in which this returns the secret itself, because a function that
    *sometimes* returns one is a function whose result gets logged.
    """
    name = get(dotted)
    if not isinstance(name, str) or not name:
        raise KeyError(f"no vault entry name configured at {dotted}")
    return name
