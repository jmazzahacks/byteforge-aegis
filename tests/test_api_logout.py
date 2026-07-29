"""
Tests for logout.

Logout used to delete one auth-token row and nothing else, so the refresh
token — the long-lived credential — survived the action users treat as the
kill switch. A captured refresh token kept minting auth tokens for up to a
week after "signing out".
"""
import time

import pytest

from byteforge_aegis_models import AuthToken, RefreshToken, UserRole
from database import db_manager
from models import User
from utils.uuid7 import generate_uuid7


def _make_user(site, email):
    now = int(time.time())
    return db_manager.create_user(User(
        uuid=generate_uuid7(),
        site_uuid=site.uuid,
        email=email,
        password_hash='$2b$12$hashed_password',
        is_verified=True,
        role=UserRole.USER,
        created_at=now,
        updated_at=now,
    ))


def _make_session(site, user, label):
    """An auth token plus a refresh token, as login would issue."""
    now = int(time.time())
    auth = db_manager.create_auth_token(AuthToken(
        token=f'auth_{label}',
        user_uuid=user.uuid,
        site_uuid=site.uuid,
        expires_at=now + 3600,
        created_at=now,
    ))
    refresh = db_manager.create_refresh_token(RefreshToken(
        token=f'refresh_{label}',
        site_uuid=site.uuid,
        user_uuid=user.uuid,
        family_id=f'family_{label}',
        expires_at=now + 604800,
        created_at=now,
        used_at=None,
        revoked=False,
    ))
    return auth, refresh


@pytest.fixture
def session_owner(sample_site):
    return _make_user(sample_site, 'owner@test.example.com')


def test_logout_revokes_the_supplied_refresh_family(
    test_client, sample_site, session_owner
):
    auth, refresh = _make_session(sample_site, session_owner, 'a')

    response = test_client.post(
        '/api/auth/logout',
        headers={'Authorization': f'Bearer {auth.token}'},
        json={'refresh_token': refresh.token},
    )

    assert response.status_code == 200
    assert db_manager.find_auth_token_by_token(auth.token) is None
    assert db_manager.find_refresh_token_by_token(refresh.token).revoked is True


def test_logout_leaves_other_sessions_alone(test_client, sample_site, session_owner):
    """Per-device logout: the phone stays signed in when the laptop signs out."""
    laptop_auth, laptop_refresh = _make_session(sample_site, session_owner, 'laptop')
    _, phone_refresh = _make_session(sample_site, session_owner, 'phone')

    test_client.post(
        '/api/auth/logout',
        headers={'Authorization': f'Bearer {laptop_auth.token}'},
        json={'refresh_token': laptop_refresh.token},
    )

    assert db_manager.find_refresh_token_by_token(laptop_refresh.token).revoked is True
    assert db_manager.find_refresh_token_by_token(phone_refresh.token).revoked is False


def test_logout_cannot_revoke_another_users_session(
    test_client, sample_site, session_owner
):
    """Otherwise logout is a denial-of-service primitive against other accounts."""
    attacker = _make_user(sample_site, 'attacker@test.example.com')
    attacker_auth, _ = _make_session(sample_site, attacker, 'attacker')
    _, victim_refresh = _make_session(sample_site, session_owner, 'victim')

    response = test_client.post(
        '/api/auth/logout',
        headers={'Authorization': f'Bearer {attacker_auth.token}'},
        json={'refresh_token': victim_refresh.token},
    )

    assert response.status_code == 200  # the attacker's own logout still succeeds
    assert db_manager.find_refresh_token_by_token(victim_refresh.token).revoked is False


def test_logout_without_refresh_token_still_clears_the_auth_token(
    test_client, sample_site, session_owner
):
    """Back-compatible: an older client that sends no body is not broken."""
    auth, _ = _make_session(sample_site, session_owner, 'bare')

    response = test_client.post(
        '/api/auth/logout',
        headers={'Authorization': f'Bearer {auth.token}'},
    )

    assert response.status_code == 200
    assert db_manager.find_auth_token_by_token(auth.token) is None


def test_logout_requires_authentication(test_client):
    assert test_client.post('/api/auth/logout').status_code == 401
