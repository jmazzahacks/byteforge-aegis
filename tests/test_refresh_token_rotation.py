"""
Tests for refresh token rotation, the grace window, and reuse detection.

None of this had any test coverage, which is how a check-then-act race
survived in it: rotation read used_at, tested it, then wrote, across three
separate transactions. Two concurrent presentations both won, the family
forked, and reuse detection could never fire again on either branch.
"""
import threading
import time

import pytest

from byteforge_aegis_models import AuthToken, RefreshToken, UserRole
from config import get_config
from database import db_manager
from models import User
from services.token_service import token_service
from utils.uuid7 import generate_uuid7


@pytest.fixture
def user(sample_site):
    now = int(time.time())
    return db_manager.create_user(User(
        uuid=generate_uuid7(),
        site_uuid=sample_site.uuid,
        email='rotate@test.example.com',
        password_hash='$2b$12$hashed_password',
        is_verified=True,
        role=UserRole.USER,
        created_at=now,
        updated_at=now,
    ))


def _make_refresh(site, user, *, used_at=None, revoked=False,
                  expires_in=3600, family_id=None, token=None):
    now = int(time.time())
    return db_manager.create_refresh_token(RefreshToken(
        token=token or f'refresh_{generate_uuid7()}',
        site_uuid=site.uuid,
        user_uuid=user.uuid,
        family_id=family_id or f'family_{generate_uuid7()}',
        expires_at=now + expires_in,
        created_at=now,
        used_at=used_at,
        revoked=revoked,
    ))


# --- the race -------------------------------------------------------------

def test_only_one_caller_can_claim_a_token(sample_site, user):
    """The core invariant: the claim succeeds exactly once."""
    refresh = _make_refresh(sample_site, user)
    now = int(time.time())

    first = db_manager.claim_refresh_token(refresh.token, now)
    second = db_manager.claim_refresh_token(refresh.token, now)

    assert first is True
    assert second is False, "a second caller claimed an already-used token"


def _family_size(family_id: int) -> int:
    with db_manager.get_cursor() as cursor:
        cursor.execute(
            "SELECT count(*) AS n FROM refresh_tokens WHERE family_id = %s",
            (family_id,)
        )
        return cursor.fetchone()['n']


def test_sequential_rotation_of_a_spent_token_mints_nothing_new(sample_site, user):
    """Invariant check, not a race reproduction — the calls do not interleave."""
    refresh = _make_refresh(sample_site, user)

    token_service.validate_and_rotate_refresh_token(refresh.token)
    token_service.validate_and_rotate_refresh_token(refresh.token)

    assert _family_size(refresh.family_id) == 2


def test_truly_concurrent_rotation_does_not_fork_the_family(sample_site, user):
    """The actual race: threads interleaved between the read and the write.

    Against the pre-fix code the UPDATE was unconditional, so several
    threads could each observe used_at IS NULL and each mint a successor,
    leaving a family with multiple live branches that reuse detection would
    never notice. A fork is invisible in the responses — every caller gets
    a valid result — so this asserts on the family's row count.
    """
    refresh = _make_refresh(sample_site, user)
    # Kept below the pool's max_conn (5) — more threads than connections
    # just fails on PoolError before any interleaving happens.
    threads_count = 4
    barrier = threading.Barrier(threads_count)
    errors = []

    def rotate() -> None:
        try:
            barrier.wait(timeout=10)
            token_service.validate_and_rotate_refresh_token(refresh.token)
        except ValueError:
            pass  # reuse detection is a legitimate outcome for a loser
        except Exception as exc:  # noqa: BLE001 - surfaced via the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=rotate) for _ in range(threads_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not errors, f"unexpected errors during concurrent rotation: {errors}"
    assert _family_size(refresh.family_id) == 2, (
        f"family forked under concurrency: expected original + exactly 1 "
        f"successor, got {_family_size(refresh.family_id)}"
    )


# --- normal rotation ------------------------------------------------------

def test_rotation_issues_a_new_token_and_consumes_the_old(sample_site, user):
    refresh = _make_refresh(sample_site, user)

    result = token_service.validate_and_rotate_refresh_token(refresh.token)

    assert result is not None
    assert result.new_refresh_token is not None
    assert result.new_refresh_token.token != refresh.token
    assert result.new_refresh_token.family_id == refresh.family_id

    consumed = db_manager.find_refresh_token_by_token(refresh.token)
    assert consumed.used_at is not None


# --- grace window ---------------------------------------------------------

def test_within_grace_succeeds_but_issues_no_new_refresh_token(sample_site, user):
    """A concurrent client re-presenting the spent token is not failed...

    ...but gets no second refresh token. It does not need one: the same
    client already holds the successor from the winning response. Issuing
    one per loser is what forks the family.
    """
    refresh = _make_refresh(sample_site, user)
    rotated = token_service.validate_and_rotate_refresh_token(refresh.token)

    again = token_service.validate_and_rotate_refresh_token(refresh.token)

    assert again is not None, 'the loser of a concurrent refresh must not fail'
    assert again.user_uuid == rotated.user_uuid
    assert again.new_refresh_token is None


def test_grace_does_not_hand_out_the_family_live_token(sample_site, user):
    """The old behaviour returned the family's current token to anyone
    presenting a spent one inside the window — including an attacker
    replaying a captured token."""
    refresh = _make_refresh(sample_site, user)
    rotated = token_service.validate_and_rotate_refresh_token(refresh.token)
    live_token = rotated.new_refresh_token.token

    replayed = token_service.validate_and_rotate_refresh_token(refresh.token)

    assert replayed.new_refresh_token is None
    # And the live token is still usable by its rightful holder.
    assert token_service.validate_and_rotate_refresh_token(live_token) is not None


# --- reuse detection ------------------------------------------------------

def test_reuse_past_grace_revokes_the_family(sample_site, user):
    grace = get_config().REFRESH_TOKEN_GRACE_PERIOD
    stale = int(time.time()) - grace - 60
    refresh = _make_refresh(sample_site, user, used_at=stale)
    sibling = _make_refresh(sample_site, user, family_id=refresh.family_id)

    with pytest.raises(ValueError, match='reuse detected'):
        token_service.validate_and_rotate_refresh_token(refresh.token)

    assert db_manager.find_refresh_token_by_token(sibling.token).revoked is True


def test_reuse_past_grace_also_kills_the_auth_token(sample_site, user):
    """Revoking only the refresh family left the credential the thief is
    actually holding working for up to AUTH_TOKEN_EXPIRATION."""
    now = int(time.time())
    db_manager.create_auth_token(AuthToken(
        token='stolen_auth_token',
        user_uuid=user.uuid,
        site_uuid=sample_site.uuid,
        expires_at=now + 3600,
        created_at=now,
    ))
    grace = get_config().REFRESH_TOKEN_GRACE_PERIOD
    refresh = _make_refresh(sample_site, user, used_at=now - grace - 60)

    with pytest.raises(ValueError, match='reuse detected'):
        token_service.validate_and_rotate_refresh_token(refresh.token)

    assert db_manager.find_auth_token_by_token('stolen_auth_token') is None


def test_reuse_does_not_touch_other_families(sample_site, user):
    """Revocation must be scoped to the compromised session only."""
    other = _make_refresh(sample_site, user)
    grace = get_config().REFRESH_TOKEN_GRACE_PERIOD
    compromised = _make_refresh(
        sample_site, user, used_at=int(time.time()) - grace - 60
    )

    with pytest.raises(ValueError):
        token_service.validate_and_rotate_refresh_token(compromised.token)

    assert db_manager.find_refresh_token_by_token(other.token).revoked is False


# --- rejection paths ------------------------------------------------------

def test_revoked_token_is_rejected(sample_site, user):
    refresh = _make_refresh(sample_site, user, revoked=True)
    assert token_service.validate_and_rotate_refresh_token(refresh.token) is None


def test_expired_token_is_rejected(sample_site, user):
    refresh = _make_refresh(sample_site, user, expires_in=-60)
    assert token_service.validate_and_rotate_refresh_token(refresh.token) is None


def test_unknown_token_is_rejected():
    assert token_service.validate_and_rotate_refresh_token('no_such_token') is None
