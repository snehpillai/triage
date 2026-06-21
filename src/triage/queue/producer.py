"""Redis Streams producer: enqueue a ticket for worker processing."""

import redis

from triage.config import settings

# Module-level sync client - reused across calls (connection pool under the hood).
_redis = redis.from_url(settings.redis_url, decode_responses=True)


def enqueue_ticket(ticket_id: str, content: str) -> str:
    """XADD a ticket message to the stream.

    Returns the Redis-assigned message ID (e.g. '1718640000000-0').
    Raises redis.RedisError on connection or command failure.
    """
    msg_id: str = _redis.xadd(
        settings.redis_stream_name,
        {"ticket_id": ticket_id, "content": content},
    )
    return msg_id
