"""
One mailbox is one account, whatever case the user types.

UNIQUE(site_uuid, email) is case sensitive, so Victim@example.com and
victim@example.com used to be two separate accounts delivering to the same
real mailbox — and registration's duplicate check missed the variant, so an
attacker could claim a genuine account that a downstream app keying on
email would conflate with the original.
"""
import time

import pytest

from byteforge_aegis_models import UserRole
from database import db_manager
from models import User
from services.auth_service import auth_service
from services.password_service import password_service
from utils.email_normalize import normalize_email
from utils.uuid7 import generate_uuid7

PASSWORD = 'valid_password_9'


@pytest.fixture
def existing_user(sample_site):
    now = int(time.time())
    return db_manager.create_user(User(
        uuid=generate_uuid7(),
        site_uuid=sample_site.uuid,
        email='victim@test.example.com',
        password_hash=password_service.hash_password(PASSWORD),
        is_verified=True,
        role=UserRole.USER,
        created_at=now,
        updated_at=now,
    ))


def test_lookup_is_case_insensitive(sample_site, existing_user):
    found = db_manager.find_user_by_email(sample_site.uuid, 'VICTIM@TEST.EXAMPLE.COM')

    assert found is not None
    assert found.uuid == existing_user.uuid


def test_addresses_are_stored_lowercased(sample_site):
    now = int(time.time())
    created = db_manager.create_user(User(
        uuid=generate_uuid7(),
        site_uuid=sample_site.uuid,
        email='  MixedCase@Test.Example.COM  ',
        password_hash='$2b$12$hashed_password',
        is_verified=False,
        role=UserRole.USER,
        created_at=now,
        updated_at=now,
    ))

    assert created.email == 'mixedcase@test.example.com'
    fetched = db_manager.find_user_by_uuid(created.uuid)
    assert fetched.email == 'mixedcase@test.example.com'


def test_case_variant_cannot_claim_an_existing_account(sample_site, existing_user):
    """The shadow-account attack.

    register_user returns None for a duplicate on the self-registration
    path — deliberately, so the response cannot be used to enumerate
    addresses. The variant must take that branch, not create a second row.
    """
    result = auth_service.register_user(
        site_uuid=sample_site.uuid,
        email='Victim@Test.Example.COM',
        password=PASSWORD,
    )

    assert result is None

    with db_manager.get_cursor() as cursor:
        cursor.execute(
            "SELECT count(*) AS n FROM users WHERE site_uuid = %s AND lower(email) = %s",
            (sample_site.uuid, 'victim@test.example.com')
        )
        assert cursor.fetchone()['n'] == 1


def test_login_works_with_different_capitalisation(test_client, sample_site, existing_user):
    response = test_client.post(
        '/api/auth/login',
        headers={'X-Tenant-Api-Key': sample_site.tenant_api_key},
        json={'site_id': sample_site.uuid,
              'email': 'Victim@Test.Example.com',
              'password': PASSWORD},
    )

    assert response.status_code == 200


def test_invite_rejects_a_case_variant_of_an_existing_account(sample_site, existing_user):
    """Invites go through the same normalized lookup as registration.

    A consumer keying on email asked whether inviting Bob@Example.com when
    bob@example.com exists creates a second account. It must not: the
    duplicate check normalizes, so the variant resolves to the existing
    verified user and is refused rather than shadowing them.
    """
    with pytest.raises(ValueError, match='already registered'):
        auth_service.invite_user(
            site_uuid=sample_site.uuid,
            email='Victim@Test.Example.COM',
        )

    with db_manager.get_cursor() as cursor:
        cursor.execute(
            "SELECT count(*) AS n FROM users WHERE site_uuid = %s AND lower(email) = %s",
            (sample_site.uuid, 'victim@test.example.com')
        )
        assert cursor.fetchone()['n'] == 1


def test_invite_stores_a_new_address_lowercased(sample_site):
    """What the tenant later receives in user.verified is the canonical form."""
    invited = auth_service.invite_user(
        site_uuid=sample_site.uuid,
        email='Fresh.Invite@Test.Example.COM',
    )

    assert invited.email == 'fresh.invite@test.example.com'


def test_normalize_email_helper():
    assert normalize_email('  User@Example.COM ') == 'user@example.com'
    assert normalize_email('already@lower.com') == 'already@lower.com'
    assert normalize_email(None) is None
