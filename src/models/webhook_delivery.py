"""
The durable record of a webhook that owes a tenant a delivery.

This is an outbox row, written BEFORE the first HTTP attempt. That
ordering is the whole point of the table: delivery used to be a bare
submit to an in-process thread pool, so anything queued or in flight when
the container rotated vanished with no trace at all — the log row was
written after the POST, so a dropped event left no evidence it had ever
existed. Persisting first means a restart costs latency, not data.

Distinct from WebhookEvent, which is the per-ATTEMPT log. One delivery
row can have several event rows against it.
"""
from dataclasses import dataclass
from typing import Any, Dict, Optional

# A delivery is owed until it is either accepted or given up on.
STATUS_PENDING = 'pending'
STATUS_DELIVERED = 'delivered'
STATUS_EXHAUSTED = 'exhausted'


@dataclass
class WebhookDelivery:
    """
    Attributes:
        event_id: The id carried in the payload. Primary key, so writing
            the same event twice is a conflict rather than a duplicate
            delivery.
        site_uuid: Site the webhook is owed to.
        event_type: Event type, denormalized from the payload for queries.
        payload: The exact JSON body to send. Stored verbatim because the
            signature is computed over it byte-for-byte; re-serializing
            could reorder keys and invalidate the signature.
        status: pending | delivered | exhausted.
        attempts: How many attempts have been started.
        next_attempt_at: Unix time this row becomes claimable. Also used
            as a lease: claiming pushes it forward, so a worker that dies
            mid-attempt releases the row by simply letting the lease
            expire, with no stuck state to reap.
        last_status: HTTP status of the most recent attempt, if any.
        last_error: Transport error from the most recent attempt, if any.
        created_at: When the event was raised.
        updated_at: When this row last changed.
    """
    event_id: str
    site_uuid: str
    event_type: str
    payload: str
    status: str
    attempts: int
    next_attempt_at: int
    created_at: int
    updated_at: int
    last_status: Optional[int] = None
    last_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert delivery to dictionary."""
        return {
            'event_id': self.event_id,
            'site_uuid': self.site_uuid,
            'event_type': self.event_type,
            'payload': self.payload,
            'status': self.status,
            'attempts': self.attempts,
            'next_attempt_at': self.next_attempt_at,
            'last_status': self.last_status,
            'last_error': self.last_error,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'WebhookDelivery':
        """Create delivery from a database row."""
        return cls(
            event_id=str(data['event_id']),
            site_uuid=str(data['site_uuid']),
            event_type=data['event_type'],
            payload=data['payload'],
            status=data['status'],
            attempts=data['attempts'],
            next_attempt_at=data['next_attempt_at'],
            last_status=data.get('last_status'),
            last_error=data.get('last_error'),
            created_at=data['created_at'],
            updated_at=data['updated_at'],
        )
