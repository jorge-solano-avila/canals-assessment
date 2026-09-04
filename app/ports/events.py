"""The event publishing port.

Exists so the outbox relay has a broker-shaped seam without a broker. CLAUDE.md
forbids external brokers; this keeps the shape without the dependency.
"""

from typing import Any, Protocol
from uuid import UUID


class EventPublisher(Protocol):
    async def publish(
        self,
        *,
        event_type: str,
        aggregate_id: UUID,
        payload: dict[str, Any],
    ) -> None:
        """Publish one event. Raising means the outbox row stays unprocessed."""
        ...
