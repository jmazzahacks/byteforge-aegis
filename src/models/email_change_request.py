from dataclasses import dataclass
from typing import Dict, Any, Optional


@dataclass
class EmailChangeRequest:
    """
    Email change request model for updating user email addresses.

    When a user requests to change their email, a token is sent to the NEW email
    address to verify ownership. One-time use token deleted upon successful change.
    Scoped to a specific site.

    Attributes:
        token: Unique secure token string
        site_id: Legacy integer id of the site this token belongs to
        user_id: Legacy integer id of the user requesting the email change
        new_email: The new email address to be verified
        expires_at: Unix timestamp when the token expires
        created_at: Unix timestamp when the token was created
        site_uuid: Globally-unique id of the site this token belongs to
        user_uuid: Globally-unique id of the user requesting the email change
    """
    token: str
    site_id: int
    user_id: int
    new_email: str
    expires_at: int
    created_at: int
    site_uuid: Optional[str] = None
    user_uuid: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert email change request model to dictionary"""
        return {
            'token': self.token,
            'site_id': self.site_id,
            'user_id': self.user_id,
            'new_email': self.new_email,
            'expires_at': self.expires_at,
            'created_at': self.created_at,
            'site_uuid': self.site_uuid,
            'user_uuid': self.user_uuid
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'EmailChangeRequest':
        """Create email change request model from dictionary"""
        return cls(
            token=data['token'],
            site_id=data['site_id'],
            user_id=data['user_id'],
            new_email=data['new_email'],
            expires_at=data['expires_at'],
            created_at=data['created_at'],
            site_uuid=data.get('site_uuid'),
            user_uuid=data.get('user_uuid')
        )
