"""
Identifier resolution helpers (post-contract: UUID is the only identifier form).

Sites and users are addressed exclusively by UUID. These helpers validate the
incoming value is a well-formed UUID before hitting the database (a bad value
against a uuid-typed column would otherwise raise instead of returning None)
and return the canonical model.
"""
import uuid as uuid_module
from typing import Optional

from byteforge_aegis_models import Site
from models.user import User
from database import db_manager


def _is_valid_uuid(value: object) -> bool:
    """True if the value is a string that parses as a UUID."""
    if not isinstance(value, str):
        return False
    try:
        uuid_module.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def resolve_site(value: object) -> Optional[Site]:
    """Resolve a site by UUID string. None if missing/invalid/unknown."""
    if _is_valid_uuid(value):
        return db_manager.find_site_by_uuid(value)
    return None


def resolve_user(value: object) -> Optional[User]:
    """Resolve a user by UUID string. None if missing/invalid/unknown."""
    if _is_valid_uuid(value):
        return db_manager.find_user_by_uuid(value)
    return None
