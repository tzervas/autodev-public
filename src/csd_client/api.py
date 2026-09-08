"""High-level harness API client (runs, agents, sessions, leases, steer)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from urllib.parse import quote

from csd_client import transport


class Client:
    """HTTP-only client. Construct with an API base or from_env()."""

    def __init__(self, base: str | None = None) -> None:
        self.base = transport.api_base(base)

    @classmethod
    def from_env(cls, explicit: str | None = None) -> Client:
        return cls(base=explicit)

    def _req(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return transport.request(method, path, base=self.base, body=body, query=query)

    def _run_path(self, run_id: str, *parts: str) -> str:
        base = transport.path_id("runs", run_id)
        if not parts:
            return base
        return base + "/" + "/".join(quote(p, safe="") for p in parts)

    # --- steer ---
    def get_steer(self) -> dict[str, Any]:
        return self._req("GET", "/api/steer")

    def post_steer(self, fields: dict[str, Any]) -> dict[str, Any]:
        return self._req("POST", "/api/steer", body=fields)

    # --- status / chat (existing service routes) ---
    def get_status(self) -> dict[str, Any]:
        return self._req("GET", "/api/status")

    def chat(self, prompt: str) -> dict[str, Any]:
        return self._req("POST", "/api/chat", body={"prompt": prompt})

    # --- runs ---
    def list_runs(self) -> dict[str, Any]:
        return self._req("GET", "/api/runs")

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._req("GET", transport.path_id("runs", run_id))

    def create_run(self, fields: dict[str, Any]) -> dict[str, Any]:
        return self._req("POST", "/api/runs", body=fields)

    def patch_run(self, run_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        return self._req("PATCH", transport.path_id("runs", run_id), body=fields)

    def publish_event(
        self,
        run_id: str,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        checkpoint_sha256: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"kind": kind, "payload": payload or {}}
        if checkpoint_sha256 is not None:
            body["checkpoint_sha256"] = checkpoint_sha256
        return self._req("POST", self._run_path(run_id, "events"), body=body)

    def list_events(self, run_id: str, *, after: int = 0) -> dict[str, Any]:
        return self._req(
            "GET",
            self._run_path(run_id, "events"),
            query={"after": str(after), "format": "json"},
        )

    def iter_events(
        self, run_id: str, *, after: int = 0, timeout: float = 60.0
    ) -> Iterator[dict[str, Any]]:
        return transport.iter_sse(
            self._run_path(run_id, "events"),
            base=self.base,
            query={"after": str(after)},
            timeout=timeout,
        )

    def post_input(self, run_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        return self._req("POST", self._run_path(run_id, "input"), body=fields)

    def consume_override(self, run_id: str) -> dict[str, Any]:
        return self._req("POST", self._run_path(run_id, "override", "consume"), body={})

    def register_call(
        self, run_id: str, pid: int, *, argv: list[str] | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"pid": int(pid)}
        if argv is not None:
            body["argv"] = list(argv)
        return self._req("POST", self._run_path(run_id, "call"), body=body)

    def clear_call(self, run_id: str) -> dict[str, Any]:
        return self._req("POST", self._run_path(run_id, "call"), body={"action": "clear"})

    # --- decision table + escalations (server reads the checked-in table) ---
    def get_decision_table(self) -> dict[str, Any]:
        return self._req("GET", "/api/decisions")

    def decide(self, fields: dict[str, Any]) -> dict[str, Any]:
        return self._req("POST", "/api/decisions", body=fields)

    def list_escalations(self, *, status: str | None = None) -> dict[str, Any]:
        query = {"status": status} if status else None
        return self._req("GET", "/api/escalations", query=query)

    def open_escalation(self, fields: dict[str, Any]) -> dict[str, Any]:
        return self._req("POST", "/api/escalations", body=fields)

    def get_escalation(self, escalation_id: str) -> dict[str, Any]:
        return self._req("GET", transport.path_id("escalations", escalation_id))

    def resolve_escalation(
        self, escalation_id: str, fields: dict[str, Any]
    ) -> dict[str, Any]:
        return self._req(
            "PATCH", transport.path_id("escalations", escalation_id), body=fields
        )

    def escalation_stats(self) -> dict[str, Any]:
        return self._req("GET", "/api/escalations/stats")

    # --- agents ---
    def list_agents(self) -> dict[str, Any]:
        return self._req("GET", "/api/agents")

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        return self._req("GET", transport.path_id("agents", agent_id))

    def create_agent(self, fields: dict[str, Any]) -> dict[str, Any]:
        return self._req("POST", "/api/agents", body=fields)

    # --- sessions ---
    def list_sessions(self) -> dict[str, Any]:
        return self._req("GET", "/api/sessions")

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self._req("GET", "/api/sessions", query={"id": session_id})

    # --- leases ---
    def list_leases(self) -> dict[str, Any]:
        return self._req("GET", "/api/leases")

    def get_lease(self, lease_id: str) -> dict[str, Any]:
        return self._req("GET", transport.path_id("leases", lease_id))

    def create_lease(self, fields: dict[str, Any]) -> dict[str, Any]:
        return self._req("POST", "/api/leases", body=fields)
