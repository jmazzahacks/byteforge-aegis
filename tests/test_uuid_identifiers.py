"""
Tests for the int -> UUID identifier migration (dual-support phase).

Covers: UUIDv7 generation + persistence in the DB layer, lookup by UUID, and
the API accepting either an integer id or a UUID (and exposing UUIDs in
responses) while integer addressing keeps working.
"""
import time
import uuid as uuid_module

from byteforge_aegis_models import Site, UserRole
from database import db_manager
from models.user import User


def _is_uuid7(value: str) -> bool:
    parsed = uuid_module.UUID(value)
    return parsed.version == 7


class TestDbLayerUuids:
    def test_create_site_generates_uuid7(self, sample_site):
        assert sample_site.uuid is not None
        assert _is_uuid7(sample_site.uuid)

    def test_create_user_generates_uuid_and_site_uuid(self, sample_site, sample_user):
        assert sample_user.uuid is not None
        assert _is_uuid7(sample_user.uuid)
        # The user's site_uuid FK is derived from the owning site.
        assert sample_user.site_uuid == sample_site.uuid

    def test_find_site_by_uuid(self, sample_site):
        found = db_manager.find_site_by_uuid(sample_site.uuid)
        assert found is not None
        assert found.id == sample_site.id
        assert found.uuid == sample_site.uuid

    def test_find_user_by_uuid(self, sample_user):
        found = db_manager.find_user_by_uuid(sample_user.uuid)
        assert found is not None
        assert found.id == sample_user.id
        assert found.site_uuid == sample_user.site_uuid

    def test_find_site_by_uuid_unknown_returns_none(self, clean_database):
        assert db_manager.find_site_by_uuid(str(uuid_module.uuid4())) is None


class TestGetUserDualAddressing:
    """GET /api/sites/<site>/users/<user> must accept int or UUID for both."""

    def _get(self, client, site_id, user_id, key):
        return client.get(
            f'/api/sites/{site_id}/users/{user_id}',
            headers={'X-Tenant-Api-Key': key},
        )

    def test_by_uuid_and_uuid(self, test_client, sample_site, sample_user):
        resp = self._get(test_client, sample_site.uuid, sample_user.uuid, sample_site.tenant_api_key)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['id'] == sample_user.id
        assert data['uuid'] == sample_user.uuid
        assert data['site_uuid'] == sample_site.uuid

    def test_by_int_site_uuid_user(self, test_client, sample_site, sample_user):
        resp = self._get(test_client, sample_site.id, sample_user.uuid, sample_site.tenant_api_key)
        assert resp.status_code == 200
        assert resp.get_json()['uuid'] == sample_user.uuid

    def test_by_uuid_site_int_user(self, test_client, sample_site, sample_user):
        resp = self._get(test_client, sample_site.uuid, sample_user.id, sample_site.tenant_api_key)
        assert resp.status_code == 200
        assert resp.get_json()['id'] == sample_user.id

    def test_int_addressing_still_works(self, test_client, sample_site, sample_user):
        resp = self._get(test_client, sample_site.id, sample_user.id, sample_site.tenant_api_key)
        assert resp.status_code == 200

    def test_response_includes_uuid_fields(self, test_client, sample_site, sample_user):
        resp = self._get(test_client, sample_site.id, sample_user.id, sample_site.tenant_api_key)
        data = resp.get_json()
        assert data['uuid'] == sample_user.uuid
        assert data['site_uuid'] == sample_site.uuid

    def test_uuid_site_wrong_key_returns_401(self, test_client, sample_site, sample_user):
        resp = self._get(test_client, sample_site.uuid, sample_user.uuid, 'wrong_key')
        assert resp.status_code == 401

    def test_malformed_uuid_returns_401_not_500(self, test_client, sample_site):
        """A non-int, non-UUID path segment must not reach a uuid column query."""
        resp = self._get(test_client, sample_site.uuid, 'not-a-real-id', sample_site.tenant_api_key)
        assert resp.status_code == 401

    def test_oversized_integer_identifier_returns_401_not_500(self, test_client, sample_site):
        """An all-digit value beyond INTEGER range must not overflow into a 500."""
        resp = self._get(test_client, sample_site.uuid, '999999999999999999999', sample_site.tenant_api_key)
        assert resp.status_code == 401

    def test_cross_site_uuid_probe_returns_401(self, test_client, sample_site, clean_database):
        """A user from another site, addressed by UUID, is still rejected."""
        current_time = int(time.time())
        other_site = db_manager.create_site(Site(
            id=0, name="Other", domain="other-uuid.example.com",
            frontend_url="http://other-uuid.example.com",
            email_from="noreply@other-uuid.example.com", email_from_name="Other",
            created_at=current_time, updated_at=current_time,
            tenant_api_key="uuidprobe_tenant_key_64chars_aaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ))
        other_user = db_manager.create_user(User(
            id=0, site_id=other_site.id, email="o@example.com",
            password_hash="$2b$12$x", is_verified=True, role=UserRole.USER,
            created_at=current_time, updated_at=current_time,
        ))
        # sample_site's key, but probing other_site's user by UUID.
        resp = self._get(test_client, sample_site.uuid, other_user.uuid, sample_site.tenant_api_key)
        assert resp.status_code == 401
