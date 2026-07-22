"""
Tests for GET /api/sites/<site_id>/users/<user_id>.

Tenant-key-gated single-user lookup. The handler enforces site scoping
(user.site_uuid must match path site_uuid) and returns the same uniform 401
as the middleware on any failure mode (anti-enumeration).
"""
import time

from byteforge_aegis_models import Site, UserRole
from database import db_manager
from models.user import User
from utils.uuid7 import generate_uuid7


UNIFORM_ERROR_FRAGMENT = 'tenant api key'


def _path(site_id: str, user_id: str) -> str:
    return f'/api/sites/{site_id}/users/{user_id}'


def test_get_user_success(test_client, sample_site, sample_user):
    """Valid tenant key + matching site returns the user record."""
    response = test_client.get(
        _path(sample_site.uuid, sample_user.uuid),
        headers={'X-Tenant-Api-Key': sample_site.tenant_api_key},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['uuid'] == sample_user.uuid
    assert data['site_uuid'] == sample_site.uuid
    assert data['email'] == sample_user.email
    assert data['role'] == 'user'
    assert 'is_verified' in data
    assert 'created_at' in data
    assert 'updated_at' in data


def test_get_user_admin_role_returned(test_client, sample_site, admin_user):
    """Admin users return role='admin' so callers can do authz checks."""
    response = test_client.get(
        _path(sample_site.uuid, admin_user.uuid),
        headers={'X-Tenant-Api-Key': sample_site.tenant_api_key},
    )
    assert response.status_code == 200
    assert response.get_json()['role'] == 'admin'


def test_get_user_missing_header_returns_401(test_client, sample_site, sample_user):
    """No X-Tenant-Api-Key returns uniform 401."""
    response = test_client.get(_path(sample_site.uuid, sample_user.uuid))
    assert response.status_code == 401
    assert UNIFORM_ERROR_FRAGMENT in response.get_json()['error'].lower()


def test_get_user_wrong_key_returns_401(test_client, sample_site, sample_user):
    """Wrong tenant key returns uniform 401."""
    response = test_client.get(
        _path(sample_site.uuid, sample_user.uuid),
        headers={'X-Tenant-Api-Key': 'completely_wrong_key'},
    )
    assert response.status_code == 401
    assert UNIFORM_ERROR_FRAGMENT in response.get_json()['error'].lower()


def test_get_user_unknown_user_returns_401(test_client, sample_site):
    """Unknown user_id returns uniform 401 (not 404 — anti-enumeration)."""
    response = test_client.get(
        _path(sample_site.uuid, '01980000-0000-7000-8000-00000000dead'),
        headers={'X-Tenant-Api-Key': sample_site.tenant_api_key},
    )
    assert response.status_code == 401
    assert UNIFORM_ERROR_FRAGMENT in response.get_json()['error'].lower()


def test_get_user_cross_site_probe_returns_401(test_client, sample_site):
    """Tenant A's key cannot read tenant B's users — uniform 401."""
    current_time = int(time.time())
    other_site = db_manager.create_site(Site(
        uuid=generate_uuid7(),
        name="Other Site",
        domain="other.example.com",
        frontend_url="http://other.example.com",
        email_from="noreply@other.example.com",
        email_from_name="Other Site",
        created_at=current_time,
        updated_at=current_time,
        tenant_api_key="other_site_tenant_key_64chars_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    ))
    other_user = db_manager.create_user(User(
        uuid=generate_uuid7(),
        site_uuid=other_site.uuid,
        email="other@example.com",
        password_hash="$2b$12$hashed_password",
        is_verified=True,
        role=UserRole.USER,
        created_at=current_time,
        updated_at=current_time,
    ))

    # sample_site's key trying to read other_site's user via sample_site's path
    # — middleware passes (key matches sample_site), handler rejects (user belongs to other_site).
    response = test_client.get(
        _path(sample_site.uuid, other_user.uuid),
        headers={'X-Tenant-Api-Key': sample_site.tenant_api_key},
    )
    assert response.status_code == 401
    assert UNIFORM_ERROR_FRAGMENT in response.get_json()['error'].lower()


def test_get_user_uniform_error_across_failure_modes(test_client, sample_site, sample_user):
    """Unknown user and cross-site probe must return byte-identical bodies."""
    current_time = int(time.time())
    other_site = db_manager.create_site(Site(
        uuid=generate_uuid7(),
        name="Other Site 2",
        domain="other2.example.com",
        frontend_url="http://other2.example.com",
        email_from="noreply@other2.example.com",
        email_from_name="Other Site 2",
        created_at=current_time,
        updated_at=current_time,
        tenant_api_key="another_tenant_key_64chars_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    ))
    other_user = db_manager.create_user(User(
        uuid=generate_uuid7(),
        site_uuid=other_site.uuid,
        email="other2@example.com",
        password_hash="$2b$12$hashed_password",
        is_verified=True,
        role=UserRole.USER,
        created_at=current_time,
        updated_at=current_time,
    ))

    headers = {'X-Tenant-Api-Key': sample_site.tenant_api_key}
    cases = [
        # No header
        (_path(sample_site.uuid, sample_user.uuid), {}),
        # Wrong key
        (_path(sample_site.uuid, sample_user.uuid), {'X-Tenant-Api-Key': 'wrong'}),
        # Unknown user
        (_path(sample_site.uuid, '01980000-0000-7000-8000-00000000dead'), headers),
        # Cross-site probe
        (_path(sample_site.uuid, other_user.uuid), headers),
    ]
    response_bodies = set()
    for path, hdrs in cases:
        response = test_client.get(path, headers=hdrs)
        assert response.status_code == 401
        response_bodies.add(response.get_data())
    assert len(response_bodies) == 1, (
        f"Expected identical response bodies across failure modes, got: {response_bodies}"
    )
