from dataclasses import dataclass
from typing import Any, Dict, Optional
from byteforge_aegis_models import User as BaseUser, UserRole


@dataclass
class User(BaseUser):
    """
    Backend User model extending the shared BaseUser with password_hash.

    The shared BaseUser contains: id, site_id, email, is_verified, role, created_at, updated_at.
    This subclass adds password_hash which is backend-only (never exposed to clients).
    """
    password_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result['password_hash'] = self.password_hash
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'User':
        return cls(
            id=data['id'],
            site_id=data['site_id'],
            email=data['email'],
            password_hash=data.get('password_hash'),
            is_verified=data['is_verified'],
            role=UserRole(data['role']),
            created_at=data['created_at'],
            updated_at=data['updated_at'],
        )
