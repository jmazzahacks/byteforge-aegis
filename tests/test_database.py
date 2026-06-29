import time
from unittest.mock import MagicMock

import psycopg2
import pytest

from database import db_manager, DatabaseManager, MAX_HEALTH_RETRIES
from byteforge_aegis_models import AuthToken, Site, UserRole
from models.user import User


def test_create_site(clean_database):
    """Test creating a site in the database"""
    current_time = int(time.time())
    site = Site(
        id=0,
        name="Test Site",
        domain="test.example.com",
        frontend_url="http://test.example.com",
        email_from="noreply@test.example.com",
        email_from_name="Test Site",
        created_at=current_time,
        updated_at=current_time,
        tenant_api_key="test_create_site_tenant_key"
    )

    created_site = db_manager.create_site(site)

    assert created_site.id > 0
    assert created_site.name == "Test Site"
    assert created_site.domain == "test.example.com"
    assert created_site.frontend_url == "http://test.example.com"
    assert created_site.email_from == "noreply@test.example.com"
    assert created_site.email_from_name == "Test Site"


def test_find_site_by_id(sample_site):
    """Test finding a site by ID"""
    found_site = db_manager.find_site_by_id(sample_site.id)

    assert found_site is not None
    assert found_site.id == sample_site.id
    assert found_site.name == sample_site.name
    assert found_site.domain == sample_site.domain


def test_find_site_by_domain(sample_site):
    """Test finding a site by domain"""
    found_site = db_manager.find_site_by_domain(sample_site.domain)

    assert found_site is not None
    assert found_site.id == sample_site.id
    assert found_site.domain == sample_site.domain


def test_find_site_by_id_not_found(clean_database):
    """Test finding a site that doesn't exist"""
    found_site = db_manager.find_site_by_id(9999)

    assert found_site is None


def test_update_site(sample_site):
    """Test updating a site"""
    sample_site.name = "Updated Site Name"
    sample_site.updated_at = int(time.time())

    updated_site = db_manager.update_site(sample_site)

    assert updated_site.name == "Updated Site Name"

    # Verify in database
    found_site = db_manager.find_site_by_id(sample_site.id)
    assert found_site.name == "Updated Site Name"


def test_create_user(sample_site):
    """Test creating a user in the database"""
    current_time = int(time.time())
    user = User(
        id=0,
        site_id=sample_site.id,
        email="newuser@example.com",
        password_hash="hashed_password",
        is_verified=False,
        role=UserRole.USER,
        created_at=current_time,
        updated_at=current_time
    )

    created_user = db_manager.create_user(user)

    assert created_user.id > 0
    assert created_user.site_id == sample_site.id
    assert created_user.email == "newuser@example.com"
    assert created_user.role == UserRole.USER


def test_find_user_by_id(sample_user):
    """Test finding a user by ID"""
    found_user = db_manager.find_user_by_id(sample_user.id)

    assert found_user is not None
    assert found_user.id == sample_user.id
    assert found_user.email == sample_user.email


def test_find_user_by_email(sample_site, sample_user):
    """Test finding a user by email for a specific site"""
    found_user = db_manager.find_user_by_email(sample_site.id, sample_user.email)

    assert found_user is not None
    assert found_user.id == sample_user.id
    assert found_user.email == sample_user.email
    assert found_user.site_id == sample_site.id


def test_find_user_by_email_different_site(sample_site, sample_user):
    """Test that users are isolated by site"""
    # Try to find the user with a different site_id
    found_user = db_manager.find_user_by_email(9999, sample_user.email)

    assert found_user is None


def test_update_user(sample_user):
    """Test updating a user"""
    sample_user.email = "updated@example.com"
    sample_user.is_verified = True
    sample_user.updated_at = int(time.time())

    updated_user = db_manager.update_user(sample_user)

    assert updated_user.email == "updated@example.com"
    assert updated_user.is_verified is True

    # Verify in database
    found_user = db_manager.find_user_by_id(sample_user.id)
    assert found_user.email == "updated@example.com"
    assert found_user.is_verified is True


def test_create_auth_token(sample_site, sample_user):
    """Test creating an auth token"""
    current_time = int(time.time())
    auth_token = AuthToken(
        token="test_token_123",
        site_id=sample_site.id,
        user_id=sample_user.id,
        expires_at=current_time + 3600,
        created_at=current_time
    )

    created_token = db_manager.create_auth_token(auth_token)

    assert created_token.token == "test_token_123"
    assert created_token.site_id == sample_site.id
    assert created_token.user_id == sample_user.id


def test_find_auth_token_by_token(sample_site, sample_user):
    """Test finding an auth token by token string"""
    current_time = int(time.time())
    auth_token = AuthToken(
        token="find_me_token",
        site_id=sample_site.id,
        user_id=sample_user.id,
        expires_at=current_time + 3600,
        created_at=current_time
    )
    db_manager.create_auth_token(auth_token)

    found_token = db_manager.find_auth_token_by_token("find_me_token")

    assert found_token is not None
    assert found_token.token == "find_me_token"
    assert found_token.user_id == sample_user.id


def test_delete_auth_token(sample_site, sample_user):
    """Test deleting an auth token"""
    current_time = int(time.time())
    auth_token = AuthToken(
        token="delete_me_token",
        site_id=sample_site.id,
        user_id=sample_user.id,
        expires_at=current_time + 3600,
        created_at=current_time
    )
    db_manager.create_auth_token(auth_token)

    deleted = db_manager.delete_auth_token("delete_me_token")

    assert deleted is True

    # Verify it's gone
    found_token = db_manager.find_auth_token_by_token("delete_me_token")
    assert found_token is None


def test_delete_auth_tokens_by_user(sample_site, sample_user):
    """Test deleting all auth tokens for a user"""
    current_time = int(time.time())

    # Create multiple tokens
    for i in range(3):
        token = AuthToken(
            token=f"token_{i}",
            site_id=sample_site.id,
            user_id=sample_user.id,
            expires_at=current_time + 3600,
            created_at=current_time
        )
        db_manager.create_auth_token(token)

    deleted_count = db_manager.delete_auth_tokens_by_user(sample_user.id)

    assert deleted_count == 3

    # Verify they're all gone
    for i in range(3):
        found_token = db_manager.find_auth_token_by_token(f"token_{i}")
        assert found_token is None


def test_delete_user(sample_site, sample_user):
    """Test deleting a user and all related data."""
    # First verify user exists
    found_user = db_manager.find_user_by_id(sample_user.id)
    assert found_user is not None

    # Create some auth tokens for the user
    current_time = int(time.time())
    token = AuthToken(
        token="test_token_for_deletion",
        site_id=sample_site.id,
        user_id=sample_user.id,
        expires_at=current_time + 3600,
        created_at=current_time
    )
    db_manager.create_auth_token(token)

    # Delete the user
    deleted = db_manager.delete_user(sample_user.id)
    assert deleted is True

    # Verify user is gone
    found_user = db_manager.find_user_by_id(sample_user.id)
    assert found_user is None

    # Verify auth tokens are also gone
    found_token = db_manager.find_auth_token_by_token("test_token_for_deletion")
    assert found_token is None


def test_delete_user_not_found(clean_database):
    """Test deleting a non-existent user returns False."""
    deleted = db_manager.delete_user(99999)
    assert deleted is False


def test_delete_site(sample_site):
    """Test deleting a site removes it from the database."""
    # Verify site exists first
    found_site = db_manager.find_site_by_id(sample_site.id)
    assert found_site is not None

    deleted = db_manager.delete_site(sample_site.id)
    assert deleted is True

    # Verify site is gone
    found_site = db_manager.find_site_by_id(sample_site.id)
    assert found_site is None


def test_delete_site_cascades_users(sample_site, sample_user):
    """Test deleting a site cascade-deletes its users."""
    # sample_user belongs to sample_site
    found_user = db_manager.find_user_by_id(sample_user.id)
    assert found_user is not None

    deleted = db_manager.delete_site(sample_site.id)
    assert deleted is True

    # The site's user is gone via ON DELETE CASCADE
    found_user = db_manager.find_user_by_id(sample_user.id)
    assert found_user is None


def test_delete_site_not_found(clean_database):
    """Test deleting a non-existent site returns False."""
    deleted = db_manager.delete_site(99999)
    assert deleted is False


# --- Dead-connection recovery (mocked pool, no live Postgres) ---

def _make_db_with_mocked_pool(connections):
    """Build a DatabaseManager whose pool returns the given pre-built mock conns."""
    db = DatabaseManager.__new__(DatabaseManager)  # skip __init__'s real pool
    db.connection_pool = MagicMock()
    db.connection_pool.getconn.side_effect = list(connections)
    db._pool_initialized = True
    return db


def _alive_conn():
    conn = MagicMock()
    conn.cursor.return_value.fetchone.return_value = (1,)
    # Explicit closed=0 is REQUIRED — MagicMock would otherwise auto-create
    # a truthy attribute, making get_connection misclassify every healthy
    # conn as dead.
    conn.closed = 0
    return conn


def _dead_conn():
    conn = MagicMock()
    conn.cursor.return_value.execute.side_effect = psycopg2.OperationalError("dead")
    # psycopg2 sets `closed` to non-zero when the socket is actually broken.
    conn.closed = 2
    return conn


def test_dead_conn_on_first_checkout_retries_and_recovers():
    dead, alive = _dead_conn(), _alive_conn()
    db = _make_db_with_mocked_pool([dead, alive])

    with db.get_connection() as conn:
        assert conn is alive

    # Dead conn was discarded with close=True so the pool refills.
    db.connection_pool.putconn.assert_any_call(dead, close=True)


def test_pool_full_of_corpses_raises_after_max_retries():
    corpses = [_dead_conn() for _ in range(MAX_HEALTH_RETRIES)]
    db = _make_db_with_mocked_pool(corpses)

    with pytest.raises(RuntimeError) as excinfo:
        with db.get_connection():
            pass

    assert isinstance(excinfo.value.__cause__, psycopg2.OperationalError)
    assert db.connection_pool.putconn.call_count == MAX_HEALTH_RETRIES


def test_mid_flight_death_discards_conn_with_close():
    """A conn that dies mid-query (rollback then fails, conn.closed flips) is discarded."""
    alive = _alive_conn()
    db = _make_db_with_mocked_pool([alive])

    with pytest.raises(psycopg2.OperationalError):
        with db.get_connection() as conn:
            # Simulate the conn actually dying mid-query: the subsequent
            # rollback in the mid-flight handler raises, and conn.closed flips.
            conn.rollback.side_effect = psycopg2.OperationalError("conn died")
            conn.closed = 2
            raise psycopg2.OperationalError("query failed on dead conn")

    # Mid-flight death MUST close the conn, not recycle it.
    db.connection_pool.putconn.assert_called_once_with(alive, close=True)


def test_serialization_failure_on_healthy_conn_recycles():
    """SerializationFailure inherits from OperationalError but conn is alive — recycle, don't churn."""
    alive = _alive_conn()
    db = _make_db_with_mocked_pool([alive])

    with pytest.raises(psycopg2.errors.SerializationFailure):
        with db.get_connection() as conn:
            raise psycopg2.errors.SerializationFailure("conflict")

    # Healthy conn — must recycle (close=False), NOT destroy.
    db.connection_pool.putconn.assert_called_once_with(alive, close=False)


def test_value_error_on_silently_dead_conn_discards():
    """Non-DB exception + silently-dead conn → close=True (don't recycle the corpse)."""
    alive = _alive_conn()
    db = _make_db_with_mocked_pool([alive])

    with pytest.raises(ValueError):
        with db.get_connection() as conn:
            conn.rollback.side_effect = psycopg2.OperationalError("dead")
            conn.closed = 2
            raise ValueError("app error while conn was dying")

    db.connection_pool.putconn.assert_called_once_with(alive, close=True)


def test_unexpected_rollback_failure_discards_conn():
    """Rollback failure on an open conn means transaction state is unknown."""
    alive = _alive_conn()
    db = _make_db_with_mocked_pool([alive])

    with pytest.raises(ValueError):
        with db.get_connection() as conn:
            conn.rollback.side_effect = RuntimeError("unexpected rollback failure")
            raise ValueError("app error mid-transaction")

    db.connection_pool.putconn.assert_called_once_with(alive, close=True)
