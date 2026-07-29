from dataclasses import dataclass
from typing import Any, Dict, Optional
from byteforge_aegis_models import User as BaseUser, UserRole


@dataclass
class User(BaseUser):
    """
    Backend User model extending the shared BaseUser with password_hash.

    The shared BaseUser contains: uuid, site_uuid, email, is_verified, role,
    created_at, updated_at. This subclass adds password_hash which is
    backend-only (never exposed to clients).
    """
    password_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialise WITHOUT the password hash.

        to_dict() is the obvious thing to reach for in a handler, and it
        used to include password_hash — so any handler taking that shortcut
        would have leaked bcrypt hashes for the whole tenant. Nothing did,
        but verify_email.py carries a comment recording that it nearly
        happened. Use to_db_dict() where the hash is genuinely needed.
        """
        return super().to_dict()

    def to_db_dict(self) -> Dict[str, Any]:
        """Serialise including the password hash, for persistence only."""
        result = super().to_dict()
        result['password_hash'] = self.password_hash
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'User':
        return cls(
            uuid=data['uuid'],
            site_uuid=data['site_uuid'],
            email=data['email'],
            password_hash=data.get('password_hash'),
            is_verified=data['is_verified'],
            role=UserRole(data['role']),
            created_at=data['created_at'],
            updated_at=data['updated_at'],
            deletion_protected=data.get('deletion_protected', False),
        )
