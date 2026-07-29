"""
Changing a credential must also invalidate the paths that mint credentials.

The recovery sequence that used to fail: victim notices a compromise and
changes their password, which kills the attacker's sessions — but a reset
token or email-change link the attacker already triggered is still live, and
either one hands the account straight back.
"""
import time

import pytest

from byteforge_aegis_models import UserRole
from database import db_manager
from models import User
from services.auth_service import auth_service
from services.password_service import password_service
from services.token_service import token_service
from utils.uuid7 import generate_uuid7

OLD_PASSWORD = 'old_password_88'
NEW_PASSWORD = 'new_password_99'


@pytest.fixture
def user(sample_site):
    now = int(time.time())
    return db_manager.create_user(User(
        uuid=generate_uuid7(),
        site_uuid=sample_site.uuid,
        email='victim@test.example.com',
        password_hash=password_service.hash_password(OLD_PASSWORD),
        is_verified=True,
        role=UserRole.USER,
        created_at=now,
        updated_at=now,
    ))


def _pending(user_uuid):
    """(reset tokens, email change requests) still outstanding."""
    with db_manager.get_cursor() as cursor:
        cursor.execute(
            "SELECT count(*) AS n FROM password_reset_tokens WHERE user_uuid = %s",
            (user_uuid,)
        )
        resets = cursor.fetchone()['n']
        cursor.execute(
            "SELECT count(*) AS n FROM email_change_requests WHERE user_uuid = %s",
            (user_uuid,)
        )
        changes = cursor.fetchone()['n']
    return resets, changes


def test_password_change_discards_pending_recovery_artifacts(sample_site, user):
    token_service.create_password_reset_token(sample_site.uuid, user.uuid)
    token_service.create_email_change_token(
        sample_site.uuid, user.uuid, 'attacker@evil.example.com'
    )
    assert _pending(user.uuid) == (1, 1)

    auth_service.change_password(user.uuid, OLD_PASSWORD, NEW_PASSWORD)

    assert _pending(user.uuid) == (0, 0)


def test_password_reset_discards_pending_recovery_artifacts(sample_site, user):
    reset = token_service.create_password_reset_token(sample_site.uuid, user.uuid)
    token_service.create_password_reset_token(sample_site.uuid, user.uuid)
    token_service.create_email_change_token(
        sample_site.uuid, user.uuid, 'attacker@evil.example.com'
    )

    auth_service.reset_password(reset.token, sample_site.uuid, NEW_PASSWORD)

    # Including the second reset token, which would otherwise still work.
    assert _pending(user.uuid) == (0, 0)


def test_cleanup_is_scoped_to_the_one_user(sample_site, user):
    """A credential change must not clear anyone else's pending recovery."""
    now = int(time.time())
    bystander = db_manager.create_user(User(
        uuid=generate_uuid7(),
        site_uuid=sample_site.uuid,
        email='bystander@test.example.com',
        password_hash=password_service.hash_password(OLD_PASSWORD),
        is_verified=True,
        role=UserRole.USER,
        created_at=now,
        updated_at=now,
    ))
    token_service.create_password_reset_token(sample_site.uuid, bystander.uuid)

    auth_service.change_password(user.uuid, OLD_PASSWORD, NEW_PASSWORD)

    assert _pending(bystander.uuid) == (1, 0)
