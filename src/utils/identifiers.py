"""
Identifier resolution for the int -> UUID migration (dual-support phase).

During the migration a site or user may be addressed by either its legacy
integer id or its UUID. These helpers accept either form and return the
canonical model, so request handlers and the tenant-api-key middleware can
work transparently with whichever identifier a tenant sends.

Malformed UUID strings are rejected before hitting the database (a bad value
against a uuid-typed column would otherwise raise instead of returning None).

Every hit on the integer branch is logged at WARNING ("legacy_int_identifier")
so the phase-3 contract bake can verify via Loki that no tenant still sends
integer identifiers before the int columns are dropped. Callers that resolve
BEFORE authentication (the tenant-api-key middleware) must pass
warn_on_int=False — unauthenticated traffic could otherwise spray integer ids
and pollute the bake signal; every authenticated route re-resolves its
identifiers post-auth, so no legitimate hit is lost by suppressing them.
"""
import logging
import uuid as uuid_module
from typing import Optional

from flask import has_request_context, request

from byteforge_aegis_models import Site
from models.user import User
from database import db_manager

logger = logging.getLogger(__name__)


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


def _warn_legacy_int_usage(entity: str, int_value: int, resolved: object) -> None:
    """Log an integer-identifier hit with enough context to attribute it to a tenant."""
    if has_request_context():
        # request.path is attacker-influenced; strip CR/LF so it can't forge
        # log lines in the plain-text stdout fallback, and cap the length.
        path = request.path.replace('\r', '').replace('\n', '')[:256]
        route = f'{request.method} {path}'
    else:
        route = '(no request context)'
    logger.warning(
        'legacy_int_identifier %s=%s uuid=%s site_uuid=%s route=%s',
        entity, int_value, getattr(resolved, 'uuid', None),
        getattr(resolved, 'site_uuid', None), route,
    )


def resolve_site(value: object, warn_on_int: bool = True) -> Optional[Site]:
    """Resolve a site by integer id or UUID string. None if missing/invalid/unknown."""
    if _is_int_like(value):
        site = db_manager.find_site_by_id(int(value))
        if warn_on_int:
            _warn_legacy_int_usage('site', int(value), site)
        return site
    if isinstance(value, str) and _is_valid_uuid(value):
        return db_manager.find_site_by_uuid(value)
    return None


def resolve_user(value: object, warn_on_int: bool = True) -> Optional[User]:
    """Resolve a user by integer id or UUID string. None if missing/invalid/unknown."""
    if _is_int_like(value):
        user = db_manager.find_user_by_id(int(value))
        if warn_on_int:
            _warn_legacy_int_usage('user', int(value), user)
        return user
    if isinstance(value, str) and _is_valid_uuid(value):
        return db_manager.find_user_by_uuid(value)
    return None
