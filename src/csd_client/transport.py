"""HTTP transport for harness clients. Fail closed on connection errors."""

from __future__ import annotations

import contextlib
import json
import os
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote, urlencode


class TransportError(OSError):
    """Raised when the harness HTTP API is unreachable or refuses the call."""


DEFAULT_API = "http://203.0.113.10:9118"


def api_base(explicit: str | None = None) -> str:
    raw = explicit or os.environ.get("CSD_API") or DEFAULT_API
    return str(raw).rstrip("/")


def request(
    method: str,
    path: str,
    *,
    base: str | None = None,
    body: dict[str, Any] | None = None,
    query: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """JSON request against the harness API.

    Raises TransportError on connection refused / DNS / timeout so every
    CLI subcommand fails closed when the service is down (R1-A3).
    """
    url = api_base(base) + path
    if query:
        url = url + "?" + urlencode(query)
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            if not raw:
                return {}
            out = json.loads(raw)
            return out if isinstance(out, dict) else {"data": out}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        try:
            parsed = json.loads(detail) if detail else {}
        except json.JSONDecodeError:
            parsed = {"error": detail or exc.reason}
        if not isinstance(parsed, dict):
            parsed = {"error": detail or str(exc.reason)}
        parsed.setdefault("http", exc.code)
        parsed.setdefault("ok", False)
        return parsed
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, ConnectionRefusedError) or (
            isinstance(reason, OSError) and getattr(reason, "errno", None) in {111, 61}
        ):
            raise TransportError(f"connection refused: {url}") from exc
        raise TransportError(f"transport error: {url}: {reason!r}") from exc
    except TimeoutError as exc:
        raise TransportError(f"timeout: {url}") from exc
    except ConnectionRefusedError as exc:
        raise TransportError(f"connection refused: {url}") from exc
    except OSError as exc:
        raise TransportError(f"transport error: {url}: {exc}") from exc


def path_id(resource: str, ident: str) -> str:
    return f"/api/{resource}/{quote(ident, safe='')}"


def iter_sse(
    path: str,
    *,
    base: str | None = None,
    query: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> Iterator[dict[str, Any]]:
    """Yield JSON objects from an SSE stream (data: lines)."""
    url = api_base(base) + path
    if query:
        url = url + "?" + urlencode(query)
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "text/event-stream"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise TransportError(f"http {exc.code}: {url}") from exc
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, ConnectionRefusedError) or (
            isinstance(reason, OSError) and getattr(reason, "errno", None) in {111, 61}
        ):
            raise TransportError(f"connection refused: {url}") from exc
        raise TransportError(f"transport error: {url}: {reason!r}") from exc
    except OSError as exc:
        raise TransportError(f"transport error: {url}: {exc}") from exc

    buf = ""
    try:
        while True:
            chunk = resp.read(1)
            if not chunk:
                break
            buf += chunk.decode("utf-8", errors="replace")
            while "\n\n" in buf:
                frame, buf = buf.split("\n\n", 1)
                data_lines: list[str] = []
                for line in frame.splitlines():
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                    elif line.startswith(":"):
                        continue
                if not data_lines:
                    continue
                raw = "\n".join(data_lines)
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    finally:
        with contextlib.suppress(OSError):
            resp.close()
