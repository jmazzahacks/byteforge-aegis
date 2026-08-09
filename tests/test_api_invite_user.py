"""
Tests for POST /api/auth/invite-user.

The endpoint exists to give an integrating backend a way to add a user to
its own site using only the tenant key it already holds. Three properties
are the whole point of it existing, and each is asserted here rather than
assumed:

  * it works with self-registration DISABLED (the reason /api/auth/register
    was unusable for invite-only sites),
  * it reports a duplicate instead of silently doing nothing (the reason
    the enumeration-safe register response was unusable),
  * it cannot reach another site, and cannot mint an admin.
"""
import sys
import time

import pytest

from byteforge_aegis_models import Site, UserRole
from database import db_manager
from models import User
from utils.uuid7 import generate_uuid7

TENANT_KEY = "test_tenant_api_key_64chars_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
OTHER_KEY = "other_tenant_api_key_64chars_ccccccccccccccccccccccccccccccc"

INVITEE = 'invitee@example.com'


@pytest.fixture
def closed_site(sample_site):
    """The site with self-registration switched OFF.

    This is the configuration an invite-only tenant actually runs, and the
    one /api/auth/register refuses to serve.
    """
    sample_site.allow_self_registration = False
    sample_site.updated_at = int(time.time())
    return db_manager.update_site(sample_site)


@pytest.fixture
def other_site(clean_database):
    now = int(time.time())
    return db_manager.create_site(Site(
        uuid=generate_uuid7(),
        name="Other Site",
        domain="other.example.com",
        frontend_url="http://other.example.com",
        email_from="noreply@other.example.com",
        email_from_name="Other Site",
        created_at=now,
        updated_at=now,
        tenant_api_key=OTHER_KEY,
    ))


def _invite(client, site_id, email, key=TENANT_KEY):
    return client.post(
        '/api/auth/invite-user',
        json={'site_id': site_id, 'email': email},
        headers={'X-Tenant-Api-Key': key},
    )


# --- the reason this endpoint exists ---------------------------------------

def test_invite_works_with_self_registration_disabled(test_client, closed_site):
    """The whole point. /api/auth/register 400s on this exact site."""
    response = _invite(test_client, closed_site.uuid, INVITEE)

    assert response.status_code == 201
    created = db_manager.find_user_by_email(closed_site.uuid, INVITEE)
    assert created is not None


def test_register_still_refuses_the_same_site(test_client, closed_site):
    """Pins the contrast, so nobody 'simplifies' invite-user away later."""
    response = test_client.post(
        '/api/auth/register',
        json={'site_id': closed_site.uuid, 'email': 'someone@example.com'},
        headers={'X-Tenant-Api-Key': TENANT_KEY},
    )

    assert response.status_code == 400


def test_established_duplicate_is_reported_not_swallowed(test_client, closed_site, sample_user):
    """register returns 201 and creates nothing here; this must not."""
    response = _invite(test_client, closed_site.uuid, sample_user.email)

    assert response.status_code == 400
    assert 'already registered' in response.get_json()['error']


# --- re-inviting someone who has not accepted ------------------------------
#
# A verification token expires in 24h but the user row does not. Without a
# resend path, an invitation that is ignored for a day — or spam-filtered,
# or bounced — leaves a row that blocks every retry, and the invitee is
# locked out for good: password reset does not set is_verified, so login
# keeps refusing, and every repair endpoint needs the master key.

def test_reinviting_a_pending_user_resends_instead_of_failing(test_client, closed_site):
    first = _invite(test_client, closed_site.uuid, INVITEE)
    assert first.status_code == 201

    second = _invite(test_client, closed_site.uuid, INVITEE)

    assert second.status_code == 201
    assert second.get_json()['uuid'] == first.get_json()['uuid'], \
        're-inviting must return the same account, not create a second'


def test_reinviting_supersedes_the_previous_link(test_client, closed_site):
    """Otherwise one account accumulates a live token per invitation."""
    _invite(test_client, closed_site.uuid, INVITEE)
    created = db_manager.find_user_by_email(closed_site.uuid, INVITEE)

    _invite(test_client, closed_site.uuid, INVITEE)

    with db_manager.get_cursor() as cursor:
        cursor.execute(
            'SELECT count(*) AS n FROM email_verification_tokens WHERE user_uuid = %s',
            (created.uuid,)
        )
        assert cursor.fetchone()['n'] == 1


def test_an_expired_invitation_can_be_retried(test_client, closed_site):
    """The case that motivated the resend path, reproduced directly."""
    _invite(test_client, closed_site.uuid, INVITEE)
    created = db_manager.find_user_by_email(closed_site.uuid, INVITEE)
    with db_manager.get_cursor(commit=True) as cursor:
        cursor.execute(
            'UPDATE email_verification_tokens SET expires_at = %s WHERE user_uuid = %s',
            (int(time.time()) - 60, created.uuid)
        )

    response = _invite(test_client, closed_site.uuid, INVITEE)

    assert response.status_code == 201
    with db_manager.get_cursor() as cursor:
        cursor.execute(
            'SELECT expires_at FROM email_verification_tokens WHERE user_uuid = %s',
            (created.uuid,)
        )
        assert cursor.fetchone()['expires_at'] > int(time.time())


def test_a_verified_user_is_never_resent_to(test_client, closed_site, sample_user):
    """Resending is confined to accounts nobody has started using."""
    sample_user.is_verified = True
    db_manager.update_user(sample_user)

    response = _invite(test_client, closed_site.uuid, sample_user.email)

    assert response.status_code == 400


def test_a_user_with_a_password_is_never_resent_to(test_client, closed_site, sample_user):
    """sample_user is unverified but self-registered with a password —
    mid-signup, not a pending invitation."""
    assert sample_user.password_hash is not None

    response = _invite(test_client, closed_site.uuid, sample_user.email)

    assert response.status_code == 400


# --- what the invited user looks like --------------------------------------

def test_invited_user_has_no_password_and_is_unverified(test_client, closed_site):
    """They set their own password via the emailed link; nobody sets it
    for them, so no inviter ever knows the invitee's credential."""
    _invite(test_client, closed_site.uuid, INVITEE)

    created = db_manager.find_user_by_email(closed_site.uuid, INVITEE)
    assert created.password_hash is None
    assert created.is_verified is False


def test_a_verification_token_is_issued(test_client, closed_site):
    """Without this the invitee has no way in and the invite is a dead row."""
    _invite(test_client, closed_site.uuid, INVITEE)
    created = db_manager.find_user_by_email(closed_site.uuid, INVITEE)

    with db_manager.get_cursor() as cursor:
        cursor.execute(
            'SELECT count(*) AS n FROM email_verification_tokens WHERE user_uuid = %s',
            (created.uuid,)
        )
        assert cursor.fetchone()['n'] == 1


def test_response_does_not_carry_the_verification_token(test_client, closed_site):
    """`user.verified` is only proof of mailbox control while the token
    reaches the mailbox and nowhere else."""
    response = _invite(test_client, closed_site.uuid, INVITEE)

    assert 'token' not in response.get_json()


# --- authorization ---------------------------------------------------------

def test_role_cannot_be_escalated_via_the_body(test_client, closed_site):
    """A tenant key is a service credential. If a leaked one could mint
    console admins, the leak would escalate to site administration.

    The schema has no `role` field and marshmallow's default is to RAISE on
    unknown keys, so this is rejected outright rather than quietly ignored.
    The loud version is the one worth having: a caller who thinks they are
    setting a role finds out immediately instead of shipping code that
    appears to work and silently grants nothing.
    """
    response = test_client.post(
        '/api/auth/invite-user',
        json={'site_id': closed_site.uuid, 'email': INVITEE, 'role': 'admin'},
        headers={'X-Tenant-Api-Key': TENANT_KEY},
    )

    assert response.status_code == 400
    assert db_manager.find_user_by_email(closed_site.uuid, INVITEE) is None


def test_invited_user_is_always_an_ordinary_user(test_client, closed_site):
    """The role the endpoint does assign, asserted directly."""
    _invite(test_client, closed_site.uuid, INVITEE)

    created = db_manager.find_user_by_email(closed_site.uuid, INVITEE)
    assert created.role == UserRole.USER


def test_cannot_invite_onto_another_site(test_client, sample_site, other_site):
    response = _invite(test_client, other_site.uuid, INVITEE, key=TENANT_KEY)

    assert response.status_code == 401
    assert db_manager.find_user_by_email(other_site.uuid, INVITEE) is None


def test_missing_tenant_key_is_rejected(test_client, closed_site):
    response = test_client.post(
        '/api/auth/invite-user',
        json={'site_id': closed_site.uuid, 'email': INVITEE},
    )

    assert response.status_code == 401
    assert db_manager.find_user_by_email(closed_site.uuid, INVITEE) is None


def test_wrong_tenant_key_is_rejected(test_client, closed_site):
    response = _invite(test_client, closed_site.uuid, INVITEE, key='wrong-key')

    assert response.status_code == 401
    assert db_manager.find_user_by_email(closed_site.uuid, INVITEE) is None


# --- validation ------------------------------------------------------------

def test_malformed_email_is_rejected(test_client, closed_site):
    response = _invite(test_client, closed_site.uuid, 'not-an-email')

    assert response.status_code == 400


def test_missing_email_is_rejected(test_client, closed_site):
    response = test_client.post(
        '/api/auth/invite-user',
        json={'site_id': closed_site.uuid},
        headers={'X-Tenant-Api-Key': TENANT_KEY},
    )

    assert response.status_code == 400


def test_email_is_matched_case_insensitively(test_client, closed_site):
    """Otherwise Invitee@ and invitee@ become two accounts for one mailbox."""
    first = _invite(test_client, closed_site.uuid, INVITEE)

    second = _invite(test_client, closed_site.uuid, INVITEE.upper())

    # Resolves to the same pending invitation and resends, rather than
    # minting a second account for the same person.
    assert second.get_json()['uuid'] == first.get_json()['uuid']
    with db_manager.get_cursor() as cursor:
        cursor.execute(
            'SELECT count(*) AS n FROM users WHERE site_uuid = %s',
            (closed_site.uuid,)
        )
        assert cursor.fetchone()['n'] == 1


# --- rate limiting ---------------------------------------------------------

def test_failed_auth_does_not_consume_the_tenants_budget(test_client, closed_site):
    """A 401 must be free to send and free to receive.

    Both limits here key off the request body, and neither site_id nor an
    email address is secret. flask-limiter deducts in before_request by
    default, so without deduct_when an anonymous caller could spend a
    tenant's invite allowance for them: three wrong-key requests naming a
    victim and their next real invitation to that address is refused for
    fifteen minutes.
    """
    for _ in range(5):
        rejected = _invite(test_client, closed_site.uuid, INVITEE, key='wrong-key')
        assert rejected.status_code == 401

    response = _invite(test_client, closed_site.uuid, INVITEE)

    assert response.status_code == 201, \
        'unauthenticated requests consumed the tenant\'s own rate limit'


def test_authenticated_invites_are_still_limited(test_client, closed_site):
    """The limit must still bind the caller who actually holds the key —
    otherwise deduct_when would have disabled it rather than scoped it."""
    statuses = []
    for i in range(5):
        response = _invite(test_client, closed_site.uuid, INVITEE)
        statuses.append(response.status_code)

    assert 429 in statuses, 'the per-address limit never engaged'


# --- webhooks --------------------------------------------------------------

def test_inviting_fires_no_webhook(test_client, closed_site, monkeypatch):
    """Callers must expire their own pending invitations: an invite that is
    never accepted produces no event, ever."""
    sent = []

    def record(site, payload):
        sent.append(payload)

    # Patched through sys.modules for the reason conftest documents: the
    # package re-exports the AuthService *instance* as `services.auth_service`,
    # so a dotted-string target resolves to the object, not the module.
    monkeypatch.setattr(
        sys.modules['services.auth_service'].webhook_service,
        'send_webhook',
        record,
    )

    _invite(test_client, closed_site.uuid, INVITEE)

    assert sent == []
