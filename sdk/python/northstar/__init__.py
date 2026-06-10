"""
North Star Python SDK.

Quick start:

    pip install northstar-sdk

    # Async
    from northstar import NorthStarClient

    async with NorthStarClient("http://localhost:8000") as ns:
        await ns.ingest("document", {"text": "Fleet cost review Q2..."})
        results = await ns.retrieve("What are the fleet maintenance costs?")

    # Sync
    ns = NorthStarClient.sync("http://localhost:8000")
    results = ns.retrieve("What decisions were made about Vehicle 259?")
"""
from .client import NorthStarAPIError, NorthStarClient

__version__ = "0.1.0"

__all__ = [
    "NorthStarClient",
    "NorthStarAPIError",
]
