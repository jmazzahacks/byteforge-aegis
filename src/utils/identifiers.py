"""
Identifier resolution for the int -> UUID migration (dual-support phase).

During the migration a site or user may be addressed by either its legacy
integer id or its UUID. These helpers accept either form and return the
canonical model, so request handlers and the tenant-api-key middleware can
work transparently with whichever identifier a tenant sends.

Malformed UUID strings are rejected before hitting the database (a bad value
against a uuid-typed column would otherwise raise instead of returning None).
"""
import uuid as uuid_module
from typing import Optional

from byteforge_aegis_models import Site
from models.user import User
from database import db_manager


# PostgreSQL INTEGER upper bound. Values above this cannot be a valid id, and
# binding them to an INTEGER column raises NumericValueOutOfRange (a 500), so we
# treat out-of-range values as "not an int id" and let them fall through to a
# clean not-found instead.
_MAX_INT_ID = 2147483647


def _is_int_like(value: object) -> bool:
    """True if the value is (or spells) a base-10 integer id within INTEGER range."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return 0 <= value <= _MAX_INT_ID
    return isinstance(value, str) and value.isdigit() and int(value) <= _MAX_INT_ID


def _is_valid_uuid(value: str) -> bool:
    """True if the string parses as a UUID."""
    try:
        uuid_module.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def resolve_site(value: object) -> Optional[Site]:
    """Resolve a site by integer id or UUID string. None if missing/invalid/unknown."""
    if _is_int_like(value):
        return db_manager.find_site_by_id(int(value))
    if isinstance(value, str) and _is_valid_uuid(value):
        return db_manager.find_site_by_uuid(value)
    return None


def resolve_user(value: object) -> Optional[User]:
    """Resolve a user by integer id or UUID string. None if missing/invalid/unknown."""
    if _is_int_like(value):
        return db_manager.find_user_by_id(int(value))
    if isinstance(value, str) and _is_valid_uuid(value):
        return db_manager.find_user_by_uuid(value)
    return None
