"""
Confirming an email change.

Uniqueness was checked only when the change was REQUESTED. If the target
address got taken in between, the confirm hit the database constraint —
which is not a ValueError, so it escaped as an unhandled 500 — and the
one-time token had already been consumed on the way there. The user was
left with no route forward and nothing explaining why.
"""
import time

import pytest

from byteforge_aegis_models import UserRole
from database import db_manager
from models import User
from services.password_service import password_service
from services.token_service import token_service
from utils.uuid7 import generate_uuid7

ENDPOINT = '/api/auth/confirm-email-change'
NEW_EMAIL = 'wanted@test.example.com'


def _user(site, email):
    now = int(time.time())
    return db_manager.create_user(User(
        uuid=generate_uuid7(),
        site_uuid=site.uuid,
        email=email,
        password_hash=password_service.hash_password('valid_password_9'),
        is_verified=True,
        role=UserRole.USER,
        created_at=now,
        updated_at=now,
    ))


@pytest.fixture
def mover(sample_site):
    """A user with a pending change to NEW_EMAIL."""
    return _user(sample_site, 'mover@test.example.com')


@pytest.fixture
def pending_token(sample_site, mover):
    return token_service.create_email_change_token(
        sample_site.uuid, mover.uuid, NEW_EMAIL
    ).token


def _token_exists(token):
    return db_manager.find_email_change_request(token) is not None


def test_confirm_moves_the_address_and_spends_the_token(
    test_client, pending_token, mover
):
    response = test_client.post(ENDPOINT, json={'token': pending_token})

    assert response.status_code == 200
    assert db_manager.find_user_by_uuid(mover.uuid).email == NEW_EMAIL
    assert not _token_exists(pending_token)


def test_address_taken_in_between_is_a_400_not_a_500(
    test_client, sample_site, pending_token, mover
):
    """Someone else claims the address after the change was requested."""
    _user(sample_site, NEW_EMAIL)

    response = test_client.post(ENDPOINT, json={'token': pending_token})

    assert response.status_code == 400, (
        f'expected a clean rejection, got {response.status_code}: {response.data}'
    )
    assert 'already in use' in response.get_json()['error'].lower()


def test_a_failed_confirm_does_not_burn_the_token(
    test_client, sample_site, pending_token, mover
):
    """The user must be able to retry once the collision is resolved."""
    squatter = _user(sample_site, NEW_EMAIL)

    test_client.post(ENDPOINT, json={'token': pending_token})

    assert _token_exists(pending_token), 'token was consumed by a failed confirm'
    assert db_manager.find_user_by_uuid(mover.uuid).email == 'mover@test.example.com'

    # Resolve the collision; the original token must still work.
    db_manager.delete_user(squatter.uuid)
    retry = test_client.post(ENDPOINT, json={'token': pending_token})

    assert retry.status_code == 200
    assert db_manager.find_user_by_uuid(mover.uuid).email == NEW_EMAIL


def test_unknown_token_is_rejected(test_client):
    response = test_client.post(ENDPOINT, json={'token': 'no_such_token'})
    assert response.status_code == 400


def test_expired_token_is_rejected_and_left_alone(test_client, sample_site, mover):
    request = token_service.create_email_change_token(
        sample_site.uuid, mover.uuid, NEW_EMAIL
    )
    with db_manager.get_cursor(commit=True) as cursor:
        cursor.execute(
            "UPDATE email_change_requests SET expires_at = %s WHERE token = %s",
            (int(time.time()) - 60, request.token)
        )

    response = test_client.post(ENDPOINT, json={'token': request.token})

    assert response.status_code == 400
    assert db_manager.find_user_by_uuid(mover.uuid).email == 'mover@test.example.com'
