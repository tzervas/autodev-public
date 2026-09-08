"""Thin HTTP client for the csd-autodev harness API.

No imports of the loop library or lab-console server modules.
"""

from csd_client.api import Client
from csd_client.transport import TransportError

__all__ = ["Client", "TransportError"]
