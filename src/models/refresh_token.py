from dataclasses import dataclass
from typing import Dict, Any, Optional


@dataclass
class RefreshToken:
    """
    Refresh token model for long-lived session management.

    These tokens are used to obtain new auth tokens without re-authentication.
    They have longer expiration than auth tokens and support rotation for security.
    Tokens are scoped to a specific site and grouped by family_id for theft detection.

    Attributes:
        token: Unique secure token string
        site_id: ID of the site this token belongs to
        user_id: ID of the user this token belongs to
        family_id: Groups related rotated tokens for revocation on theft detection
        expires_at: Unix timestamp when the token expires
        created_at: Unix timestamp when the token was created
        used_at: Unix timestamp when token was exchanged (None if unused)
        revoked: Whether the token has been revoked
    """
    token: str
    site_id: int
    user_id: int
    family_id: str
    expires_at: int
    created_at: int
    used_at: Optional[int] = None
    revoked: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert refresh token model to dictionary"""
        return {
            'token': self.token,
            'site_id': self.site_id,
            'user_id': self.user_id,
            'family_id': self.family_id,
            'expires_at': self.expires_at,
            'created_at': self.created_at,
            'used_at': self.used_at,
            'revoked': self.revoked
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'RefreshToken':
        """Create refresh token model from dictionary"""
        return cls(
            token=data['token'],
            site_id=data['site_id'],
            user_id=data['user_id'],
            family_id=data['family_id'],
            expires_at=data['expires_at'],
            created_at=data['created_at'],
            used_at=data.get('used_at'),
            revoked=data.get('revoked', False)
        )
