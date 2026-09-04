"""Log-based event publisher.

CLAUDE.md forbids external brokers, so the sink is a log line. The port keeps the
broker-shaped seam: swapping this for a real publisher is a change to one class
and one line of wiring, with no change to the outbox relay or the saga.
"""

import json
import logging
from typing import Any
from uuid import UUID

logger = logging.getLogger("outbox.publisher")


class LoggingEventPublisher:
    """Implements EventPublisher."""

    def __init__(self) -> None:
        # Published events, for tests to assert on without parsing log output.
        self.published: list[tuple[str, UUID]] = []

    async def publish(
        self,
        *,
        event_type: str,
        aggregate_id: UUID,
        payload: dict[str, Any],
    ) -> None:
        logger.info(
            "published %s",
            json.dumps(
                {
                    "event_type": event_type,
                    "aggregate_id": str(aggregate_id),
                    "payload": payload,
                },
                default=str,
            ),
        )
        self.published.append((event_type, aggregate_id))
