from dataclasses import dataclass
from typing import Dict, Any


@dataclass
class EmailChangeRequest:
    """
    Email change request model for updating user email addresses.

    When a user requests to change their email, a token is sent to the NEW email
    address to verify ownership. One-time use token deleted upon successful change.
    Scoped to a specific site.

    Attributes:
        token: Unique secure token string
        site_uuid: Globally-unique id of the site this token belongs to
        user_uuid: Globally-unique id of the user requesting the email change
        new_email: The new email address to be verified
        expires_at: Unix timestamp when the token expires
        created_at: Unix timestamp when the token was created
    """
    token: str
    site_uuid: str
    user_uuid: str
    new_email: str
    expires_at: int
    created_at: int

    def to_dict(self) -> Dict[str, Any]:
        """Convert email change request model to dictionary"""
        return {
            'token': self.token,
            'site_uuid': self.site_uuid,
            'user_uuid': self.user_uuid,
            'new_email': self.new_email,
            'expires_at': self.expires_at,
            'created_at': self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'EmailChangeRequest':
        """Create email change request model from dictionary"""
        return cls(
            token=data['token'],
            site_uuid=data['site_uuid'],
            user_uuid=data['user_uuid'],
            new_email=data['new_email'],
            expires_at=data['expires_at'],
            created_at=data['created_at'],
        )
