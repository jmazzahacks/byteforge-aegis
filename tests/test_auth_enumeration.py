"""
Regression tests for account-enumeration oracles.

bcrypt hashes at most 72 bytes, and bcrypt 5 raises rather than truncating.
Because an over-long password reached bcrypt at a different point depending
on whether the account existed, the resulting error distinguished the two —
one request per address, no valid credentials required.
"""
import time

import pytest

from byteforge_aegis_models import UserRole
from database import db_manager
from models import User
from services.password_service import password_service
from utils.uuid7 import generate_uuid7

OVERSIZED = 'a' * 100
TENANT_HEADER = 'X-Tenant-Api-Key'


@pytest.fixture
def known_user(sample_site):
    """A real, verified, password-bearing account on the sample site."""
    now = int(time.time())
    user = User(
        uuid=generate_uuid7(),
        site_uuid=sample_site.uuid,
        email='known@test.example.com',
        password_hash=password_service.hash_password('correct_horse_8'),
        is_verified=True,
        role=UserRole.USER,
        created_at=now,
        updated_at=now,
    )
    return db_manager.create_user(user)


def _login(test_client, site, email, password):
    return test_client.post(
        '/api/auth/login',
        headers={TENANT_HEADER: site.tenant_api_key},
        json={'site_id': site.uuid, 'email': email, 'password': password},
    )


def test_oversized_password_does_not_distinguish_known_from_unknown(
    test_client, sample_site, known_user
):
    """The oracle: identical responses whether or not the account exists."""
    known = _login(test_client, sample_site, known_user.email, OVERSIZED)
    unknown = _login(test_client, sample_site, 'nobody@test.example.com', OVERSIZED)

    assert known.status_code == unknown.status_code
    assert known.get_json() == unknown.get_json()


def test_oversized_password_is_rejected_before_any_lookup(
    test_client, sample_site, known_user
):
    """Rejected at the schema, so the response cannot depend on the account.

    Naming the 72-byte limit is fine — it is a fixed property of the
    algorithm and identical for every caller. What must never appear is
    bcrypt's own exception text, which only surfaces once hashing is
    actually attempted and therefore implies the account exists.
    """
    response = _login(test_client, sample_site, known_user.email, OVERSIZED)

    assert response.status_code == 400
    assert b'truncate manually' not in response.data


def test_wrong_password_and_unknown_account_are_indistinguishable(
    test_client, sample_site, known_user
):
    """The ordinary case must stay uniform too, not just the oversized one."""
    wrong = _login(test_client, sample_site, known_user.email, 'wrong_password_9')
    unknown = _login(test_client, sample_site, 'nobody@test.example.com', 'wrong_password_9')

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.get_json() == unknown.get_json()


def test_correct_password_still_works(test_client, sample_site, known_user):
    """Guard against a fix that simply breaks login."""
    response = _login(test_client, sample_site, known_user.email, 'correct_horse_8')
    assert response.status_code == 200


def test_registration_does_not_reveal_existing_email_via_oversized_password(
    test_client, sample_site, known_user
):
    """Registration's duplicate branch returned before hashing, inverting the
    oracle: a *free* address errored while a taken one reported success."""
    taken = test_client.post(
        '/api/auth/register',
        headers={TENANT_HEADER: sample_site.tenant_api_key},
        json={'site_id': sample_site.uuid, 'email': known_user.email,
              'password': OVERSIZED},
    )
    free = test_client.post(
        '/api/auth/register',
        headers={TENANT_HEADER: sample_site.tenant_api_key},
        json={'site_id': sample_site.uuid, 'email': 'brand-new@test.example.com',
              'password': OVERSIZED},
    )

    assert taken.status_code == free.status_code
    assert taken.get_json() == free.get_json()
