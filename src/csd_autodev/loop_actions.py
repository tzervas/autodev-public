"""Parity table: every loop action on the shared surface has an HTTP route (S13).

A loop-only action (present in LOOP_ACTIONS but missing from API_ROUTES) fails
tests/test_client_boundary.py::test_loop_api_parity.
"""

from __future__ import annotations

# Actions the autodev loop performs through the shared HTTP resource surface.
# The loop is a client — no private door for these.
LOOP_ACTIONS: frozenset[str] = frozenset(
    {
        "steer.get",
        "steer.post",
        "runs.create",
        "runs.get",
        "runs.events.post",
        "runs.override.consume",
        "runs.call.register",
        "runs.call.clear",
        # Outcome recording, the decision table, and the escalation contract
        # all ride the shared surface. The loop is a client here too.
        "runs.patch",
        "decisions.get",
        "decisions.evaluate",
        "escalations.list",
        "escalations.create",
        "escalations.get",
        "escalations.resolve",
        "escalations.stats",
    }
)

# Full shared resource surface (runs, agents, sessions, leases) + steer verb.
# Human CLI/TUI and the loop share these routes.
API_ROUTES: dict[str, tuple[str, str]] = {
    "steer.get": ("GET", "/api/steer"),
    "steer.post": ("POST", "/api/steer"),
    "runs.list": ("GET", "/api/runs"),
    "runs.create": ("POST", "/api/runs"),
    "runs.get": ("GET", "/api/runs/{id}"),
    "runs.patch": ("PATCH", "/api/runs/{id}"),
    "runs.events": ("GET", "/api/runs/{id}/events"),
    "runs.events.post": ("POST", "/api/runs/{id}/events"),
    "runs.input": ("POST", "/api/runs/{id}/input"),
    "runs.override.consume": ("POST", "/api/runs/{id}/override/consume"),
    "runs.call.register": ("POST", "/api/runs/{id}/call"),
    "runs.call.clear": ("POST", "/api/runs/{id}/call"),
    "decisions.get": ("GET", "/api/decisions"),
    "decisions.evaluate": ("POST", "/api/decisions"),
    "escalations.list": ("GET", "/api/escalations"),
    "escalations.create": ("POST", "/api/escalations"),
    "escalations.get": ("GET", "/api/escalations/{id}"),
    "escalations.resolve": ("PATCH", "/api/escalations/{id}"),
    "escalations.stats": ("GET", "/api/escalations/stats"),
    "agents.list": ("GET", "/api/agents"),
    "agents.create": ("POST", "/api/agents"),
    "agents.get": ("GET", "/api/agents/{id}"),
    "sessions.list": ("GET", "/api/sessions"),
    "sessions.get": ("GET", "/api/sessions"),
    "leases.list": ("GET", "/api/leases"),
    "leases.create": ("POST", "/api/leases"),
    "leases.get": ("GET", "/api/leases/{id}"),
    # Existing service routes the CLI also uses (not loop-private).
    "status.get": ("GET", "/api/status"),
    "chat.post": ("POST", "/api/chat"),
}
