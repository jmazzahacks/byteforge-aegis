from dataclasses import dataclass
from typing import Dict, Any


@dataclass
class WebhookPayload:
    """
    Structured webhook payload sent to tenant sites.

    This is the data that gets signed with HMAC-SHA256 and POSTed
    to the site's webhook_url.

    Attributes:
        event_type: Type of event (e.g., 'user.verified')
        site_id: The site this event belongs to
        user_id: The Aegis user ID
        email: The user's email address
        aegis_role: The user's Aegis role ('user' or 'admin'), distinct from tenant app roles
        timestamp: Unix timestamp when the event occurred
    """
    event_type: str
    site_id: int
    user_id: int
    email: str
    aegis_role: str
    timestamp: int

    def to_dict(self) -> Dict[str, Any]:
        """Convert payload to dictionary for JSON serialization."""
        return {
            'event_type': self.event_type,
            'site_id': self.site_id,
            'user_id': self.user_id,
            'email': self.email,
            'aegis_role': self.aegis_role,
            'timestamp': self.timestamp
        }
