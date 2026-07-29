"""
Tests for requesting an email change.

The endpoint changes the account's login identifier, so a bearer token alone
must not be enough. Otherwise a stolen token — an hour's worth of access —
converts into permanent ownership: repoint the address, confirm it, then
drive password reset to the new address.
"""
import time

import pytest

from byteforge_aegis_models import AuthToken, UserRole
from database import db_manager
from models import User
from services.password_service import password_service
from utils.uuid7 import generate_uuid7

PASSWORD = 'correct_horse_8'
ENDPOINT = '/api/auth/request-email-change'


@pytest.fixture
def user_with_password(sample_site):
    now = int(time.time())
    return db_manager.create_user(User(
        uuid=generate_uuid7(),
        site_uuid=sample_site.uuid,
        email='owner@test.example.com',
        password_hash=password_service.hash_password(PASSWORD),
        is_verified=True,
        role=UserRole.USER,
        created_at=now,
        updated_at=now,
    ))


@pytest.fixture
def bearer(sample_site, user_with_password):
    now = int(time.time())
    token = db_manager.create_auth_token(AuthToken(
        token='email_change_token_abc',
        user_uuid=user_with_password.uuid,
        site_uuid=sample_site.uuid,
        expires_at=now + 3600,
        created_at=now,
    ))
    return {'Authorization': f'Bearer {token.token}'}


def test_correct_password_is_accepted(test_client, bearer):
    response = test_client.post(
        ENDPOINT,
        headers=bearer,
        json={'new_email': 'new@test.example.com', 'password': PASSWORD},
    )

    assert response.status_code == 200


def test_stolen_token_alone_cannot_move_the_account(test_client, bearer):
    """The attack this endpoint exists to stop."""
    response = test_client.post(
        ENDPOINT,
        headers=bearer,
        json={'new_email': 'attacker@evil.example.com', 'password': 'not_the_password'},
    )

    assert response.status_code == 400

    # And no pending request was created for the attacker's address.
    with db_manager.get_cursor() as cursor:
        cursor.execute(
            "SELECT count(*) AS n FROM email_change_requests WHERE new_email = %s",
            ('attacker@evil.example.com',)
        )
        assert cursor.fetchone()['n'] == 0


def test_password_is_required(test_client, bearer):
    """Omitting it must fail, not fall through to the old behaviour."""
    response = test_client.post(
        ENDPOINT,
        headers=bearer,
        json={'new_email': 'new@test.example.com'},
    )

    assert response.status_code == 400


def test_still_requires_authentication(test_client):
    response = test_client.post(
        ENDPOINT,
        json={'new_email': 'new@test.example.com', 'password': PASSWORD},
    )

    assert response.status_code == 401


# NOT TESTED HERE: a user whose password_hash is NULL. The service guards
# it (verify_password on None raises AttributeError, an unhandled 500), but
# the case cannot be set up against this database — database/schema.sql
# declares password_hash nullable while the actual users table has it NOT
# NULL. That drift is a separate problem: admin-created users are inserted
# with password_hash=None, so on a database carrying the constraint admin
# registration fails at the INSERT.
