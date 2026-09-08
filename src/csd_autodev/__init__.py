"""csd-autodev harness service library (store + route contract).

Clients must not import this package for operator work — use csd_client over HTTP.
"""

__all__ = ["API_ROUTES", "LOOP_ACTIONS"]


def __getattr__(name: str):
    if name in {"LOOP_ACTIONS", "API_ROUTES"}:
        from csd_autodev.loop_actions import API_ROUTES, LOOP_ACTIONS

        return LOOP_ACTIONS if name == "LOOP_ACTIONS" else API_ROUTES
    raise AttributeError(name)
