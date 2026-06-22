from dataclasses import dataclass
from typing import Dict, Any, Optional


@dataclass
class EmailVerificationToken:
    """
    Email verification token model for confirming user email ownership.

    Sent to users upon registration to verify their email address.
    One-time use token that is deleted upon successful verification.
    Scoped to a specific site.

    Attributes:
        token: Unique secure token string
        site_id: Legacy integer id of the site this token belongs to
        user_id: Legacy integer id of the user this token belongs to
        expires_at: Unix timestamp when the token expires
        created_at: Unix timestamp when the token was created
        site_uuid: Globally-unique id of the site this token belongs to
        user_uuid: Globally-unique id of the user this token belongs to
    """
    token: str
    site_id: int
    user_id: int
    expires_at: int
    created_at: int
    site_uuid: Optional[str] = None
    user_uuid: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert email verification token model to dictionary"""
        return {
            'token': self.token,
            'site_id': self.site_id,
            'user_id': self.user_id,
            'expires_at': self.expires_at,
            'created_at': self.created_at,
            'site_uuid': self.site_uuid,
            'user_uuid': self.user_uuid
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'EmailVerificationToken':
        """Create email verification token model from dictionary"""
        return cls(
            token=data['token'],
            site_id=data['site_id'],
            user_id=data['user_id'],
            expires_at=data['expires_at'],
            created_at=data['created_at'],
            site_uuid=data.get('site_uuid'),
            user_uuid=data.get('user_uuid')
        )
