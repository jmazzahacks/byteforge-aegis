"""
Bearer tokens must not be readable from the database.

Tokens were stored verbatim, so any read of auth_tokens or refresh_tokens —
a backup, a replica snapshot, a slow-query log, SQL injection elsewhere in
the system — was immediate takeover of every live session across every
tenant, with no cracking step. Passwords were bcrypted while the credential
we issue sat in the clear.

These assert on the RAW column, because everything above the database layer
is designed to hide that the digest exists.
"""
import time

import pytest

from byteforge_aegis_models import AuthToken, RefreshToken, UserRole
from database import db_manager
from models import User
from services.token_service import token_service
from utils.token_hash import token_digest
from utils.uuid7 import generate_uuid7

PLAINTEXT_AUTH = 'plaintext_auth_token_value'
PLAINTEXT_REFRESH = 'plaintext_refresh_token_value'


@pytest.fixture
def user(sample_site):
    now = int(time.time())
    return db_manager.create_user(User(
        uuid=generate_uuid7(),
        site_uuid=sample_site.uuid,
        email='hashme@test.example.com',
        password_hash='$2b$12$hashed_password',
        is_verified=True,
        role=UserRole.USER,
        created_at=now,
        updated_at=now,
    ))


def _raw(table, digest):
    with db_manager.get_cursor() as cursor:
        cursor.execute(f'SELECT token FROM {table} WHERE token = %s', (digest,))
        row = cursor.fetchone()
    return row['token'] if row else None


def test_auth_token_is_not_stored_in_plaintext(sample_site, user):
    now = int(time.time())
    db_manager.create_auth_token(AuthToken(
        token=PLAINTEXT_AUTH, user_uuid=user.uuid, site_uuid=sample_site.uuid,
        expires_at=now + 3600, created_at=now,
    ))

    with db_manager.get_cursor() as cursor:
        cursor.execute('SELECT token FROM auth_tokens')
        stored = [row['token'] for row in cursor.fetchall()]

    assert PLAINTEXT_AUTH not in stored, 'the bearer value is readable in the database'
    assert token_digest(PLAINTEXT_AUTH) in stored


def test_refresh_token_is_not_stored_in_plaintext(sample_site, user):
    now = int(time.time())
    db_manager.create_refresh_token(RefreshToken(
        token=PLAINTEXT_REFRESH, site_uuid=sample_site.uuid, user_uuid=user.uuid,
        family_id='family_hash_test', expires_at=now + 604800, created_at=now,
    ))

    with db_manager.get_cursor() as cursor:
        cursor.execute('SELECT token FROM refresh_tokens')
        stored = [row['token'] for row in cursor.fetchall()]

    assert PLAINTEXT_REFRESH not in stored
    assert token_digest(PLAINTEXT_REFRESH) in stored


def test_lookup_still_works_with_the_plaintext(sample_site, user):
    """The whole point: callers keep presenting the plaintext."""
    now = int(time.time())
    db_manager.create_auth_token(AuthToken(
        token=PLAINTEXT_AUTH, user_uuid=user.uuid, site_uuid=sample_site.uuid,
        expires_at=now + 3600, created_at=now,
    ))

    found = db_manager.find_auth_token_by_token(PLAINTEXT_AUTH)

    assert found is not None
    assert found.user_uuid == user.uuid
    # And the model carries the plaintext back, not the digest — callers and
    # tests should never have to know the digest exists.
    assert found.token == PLAINTEXT_AUTH


def test_the_digest_itself_is_not_a_usable_credential(sample_site, user):
    """Someone who reads the database has a hash, not a token.

    This is the property the whole change buys: the stored value cannot be
    replayed against the API.
    """
    now = int(time.time())
    db_manager.create_auth_token(AuthToken(
        token=PLAINTEXT_AUTH, user_uuid=user.uuid, site_uuid=sample_site.uuid,
        expires_at=now + 3600, created_at=now,
    ))

    assert token_service.validate_auth_token(PLAINTEXT_AUTH) == user.uuid
    assert token_service.validate_auth_token(token_digest(PLAINTEXT_AUTH)) is None


def test_full_login_refresh_cycle_survives_hashing(test_client, sample_site, user):
    """End to end, since this touched create, find, claim and delete."""
    login = token_service.create_auth_token(sample_site.uuid, user.uuid)
    refresh = token_service.create_refresh_token(sample_site.uuid, user.uuid)

    assert token_service.validate_auth_token(login.token) == user.uuid

    rotated = token_service.validate_and_rotate_refresh_token(refresh.token)
    assert rotated is not None
    assert rotated.new_refresh_token is not None

    # The successor is usable, which means claim + create round-tripped.
    again = token_service.validate_and_rotate_refresh_token(
        rotated.new_refresh_token.token
    )
    assert again is not None
