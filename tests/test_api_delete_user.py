"""
Tests for the delete user endpoint, focused on the last-admin guard.
"""
import time
from config import get_config
from database import db_manager
from byteforge_aegis_models import UserRole
from models.user import User


def _master_headers() -> dict:
    return {'X-API-Key': get_config().MASTER_API_KEY}


def _make_admin(site_id: int, email: str) -> User:
    current_time = int(time.time())
    return db_manager.create_user(User(
        id=0,
        site_id=site_id,
        email=email,
        password_hash="$2b$12$hashed_password",
        is_verified=True,
        role=UserRole.ADMIN,
        created_at=current_time,
        updated_at=current_time
    ))


def test_delete_last_admin_blocked(test_client, sample_site, admin_user):
    """Deleting the only admin of a site is refused with 409, admin preserved."""
    response = test_client.delete(
        f'/api/admin/users/{admin_user.id}',
        headers=_master_headers()
    )

    assert response.status_code == 409
    assert 'last admin' in response.get_json()['error'].lower()
    assert db_manager.find_user_by_id(admin_user.id) is not None


def test_delete_admin_with_another_admin_succeeds(test_client, sample_site, admin_user):
    """With two admins on the site, deleting one is allowed."""
    second_admin = _make_admin(sample_site.id, "admin2@example.com")

    response = test_client.delete(
        f'/api/admin/users/{admin_user.id}',
        headers=_master_headers()
    )

    assert response.status_code == 200
    assert db_manager.find_user_by_id(admin_user.id) is None
    assert db_manager.find_user_by_id(second_admin.id) is not None


def test_delete_regular_user_not_blocked(test_client, sample_site, sample_user, admin_user):
    """The last-admin guard only applies to admins; a regular user is deletable."""
    response = test_client.delete(
        f'/api/admin/users/{sample_user.id}',
        headers=_master_headers()
    )

    assert response.status_code == 200
    assert db_manager.find_user_by_id(sample_user.id) is None


def test_delete_user_not_found(test_client, clean_database):
    """Deleting a non-existent user returns 404."""
    response = test_client.delete('/api/admin/users/99999', headers=_master_headers())

    assert response.status_code == 404


def test_delete_user_missing_api_key(test_client, sample_site, admin_user):
    """Missing master API key returns 401 (guard never runs)."""
    response = test_client.delete(f'/api/admin/users/{admin_user.id}')

    assert response.status_code == 401
    assert db_manager.find_user_by_id(admin_user.id) is not None
