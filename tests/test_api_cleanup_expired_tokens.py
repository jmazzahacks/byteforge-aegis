"""
Tests for the expired-token cleanup endpoint.

The property that matters most here is the negative one: cleanup must remove
only rows that have already expired. A bug that over-deletes would log every
active user out across every tenant at once, and the endpoint is designed to
run unattended on a schedule, so nobody would be watching when it happened.
"""
import time

import pytest

from byteforge_aegis_models import AuthToken, RefreshToken
from config import get_config
from database import db_manager
from models import (
    EmailChangeRequest,
    EmailVerificationToken,
    PasswordResetToken,
)

ENDPOINT = '/api/admin/cleanup-expired-tokens'


@pytest.fixture
def master_headers():
    return {'X-API-Key': get_config().MASTER_API_KEY}


def _seed_tokens(site_uuid: str, user_uuid: str, expires_at: int, label: str) -> None:
    """Create one row in each of the five token tables with a given expiry."""
    now = int(time.time())

    db_manager.create_auth_token(AuthToken(
        token=f'auth_{label}',
        user_uuid=user_uuid,
        site_uuid=site_uuid,
        expires_at=expires_at,
        created_at=now,
    ))
    db_manager.create_refresh_token(RefreshToken(
        token=f'refresh_{label}',
        site_uuid=site_uuid,
        user_uuid=user_uuid,
        expires_at=expires_at,
        family_id=f'family_{label}',
        created_at=now,
    ))
    db_manager.create_email_verification_token(EmailVerificationToken(
        token=f'verify_{label}',
        site_uuid=site_uuid,
        user_uuid=user_uuid,
        expires_at=expires_at,
        created_at=now,
    ))
    db_manager.create_password_reset_token(PasswordResetToken(
        token=f'reset_{label}',
        site_uuid=site_uuid,
        user_uuid=user_uuid,
        expires_at=expires_at,
        created_at=now,
        used=False,
    ))
    db_manager.create_email_change_request(EmailChangeRequest(
        token=f'change_{label}',
        site_uuid=site_uuid,
        user_uuid=user_uuid,
        new_email=f'{label}@example.com',
        expires_at=expires_at,
        created_at=now,
    ))


def test_requires_master_api_key(test_client):
    response = test_client.post(ENDPOINT)
    assert response.status_code == 401


def test_rejects_wrong_master_api_key(test_client):
    response = test_client.post(ENDPOINT, headers={'X-API-Key': 'wrong_key'})
    assert response.status_code == 401


def test_removes_expired_rows_from_every_table(test_client, master_headers, sample_site, sample_user):
    _seed_tokens(sample_site.uuid, sample_user.uuid, int(time.time()) - 60, 'expired')

    response = test_client.post(ENDPOINT, headers=master_headers)

    assert response.status_code == 200
    body = response.get_json()
    assert body['auth_tokens'] == 1
    assert body['refresh_tokens'] == 1
    assert body['email_verification_tokens'] == 1
    assert body['password_reset_tokens'] == 1
    assert body['email_change_requests'] == 1
    assert body['total'] == 5


def test_leaves_unexpired_rows_intact(test_client, master_headers, sample_site, sample_user):
    """The safety property: a live session must survive a cleanup run."""
    _seed_tokens(sample_site.uuid, sample_user.uuid, int(time.time()) + 3600, 'live')

    response = test_client.post(ENDPOINT, headers=master_headers)

    assert response.status_code == 200
    assert response.get_json()['total'] == 0

    # Verify against the database rather than trusting the reported count.
    assert db_manager.find_auth_token_by_token('auth_live') is not None
    assert db_manager.find_refresh_token_by_token('refresh_live') is not None


def test_removes_only_the_expired_rows_when_both_exist(test_client, master_headers, sample_site, sample_user):
    _seed_tokens(sample_site.uuid, sample_user.uuid, int(time.time()) - 60, 'expired')
    _seed_tokens(sample_site.uuid, sample_user.uuid, int(time.time()) + 3600, 'live')

    response = test_client.post(ENDPOINT, headers=master_headers)

    assert response.get_json()['total'] == 5
    assert db_manager.find_auth_token_by_token('auth_live') is not None
    assert db_manager.find_auth_token_by_token('auth_expired') is None


def test_is_idempotent(test_client, master_headers, sample_site, sample_user):
    """A scheduler will call this repeatedly; the second run must be a no-op."""
    _seed_tokens(sample_site.uuid, sample_user.uuid, int(time.time()) - 60, 'expired')

    first = test_client.post(ENDPOINT, headers=master_headers)
    second = test_client.post(ENDPOINT, headers=master_headers)

    assert first.get_json()['total'] == 5
    assert second.get_json()['total'] == 0
