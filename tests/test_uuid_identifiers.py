"""
Tests for UUID-only identifier handling (post-contract).

Covers: UUIDv7 generation + persistence in the DB layer, lookup by UUID, and
the API accepting ONLY UUIDs — integer addressing must be rejected with the
same status as any unknown identifier (no special-casing, no 500s).
"""
import time
import uuid as uuid_module

from byteforge_aegis_models import Site, UserRole
from database import db_manager
from models.user import User
from utils.identifiers import resolve_site, resolve_user
from utils.uuid7 import generate_uuid7


def _is_uuid7(value: str) -> bool:
    parsed = uuid_module.UUID(value)
    return parsed.version == 7


class TestDbLayerUuids:
    def test_create_site_generates_uuid7(self, sample_site):
        assert sample_site.uuid is not None
        assert _is_uuid7(sample_site.uuid)

    def test_create_user_carries_site_uuid(self, sample_site, sample_user):
        assert sample_user.uuid is not None
        assert _is_uuid7(sample_user.uuid)
        assert sample_user.site_uuid == sample_site.uuid

    def test_find_site_by_uuid(self, sample_site):
        found = db_manager.find_site_by_uuid(sample_site.uuid)
        assert found is not None
        assert found.uuid == sample_site.uuid

    def test_find_user_by_uuid(self, sample_user):
        found = db_manager.find_user_by_uuid(sample_user.uuid)
        assert found is not None
        assert found.uuid == sample_user.uuid
        assert found.site_uuid == sample_user.site_uuid

    def test_find_site_by_uuid_unknown_returns_none(self, clean_database):
        assert db_manager.find_site_by_uuid(str(uuid_module.uuid4())) is None

    def test_create_site_mints_uuid_when_missing(self, clean_database):
        current_time = int(time.time())
        site = db_manager.create_site(Site(
            uuid='', name="Minted", domain="minted.example.com",
            frontend_url="http://minted.example.com",
            email_from="noreply@minted.example.com", email_from_name="Minted",
            created_at=current_time, updated_at=current_time,
            tenant_api_key="minted_tenant_key_64chars_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ))
        assert site.uuid and _is_uuid7(site.uuid)


class TestResolvers:
    def test_resolve_site_by_uuid(self, sample_site):
        site = resolve_site(sample_site.uuid)
        assert site is not None
        assert site.uuid == sample_site.uuid

    def test_resolve_user_by_uuid(self, sample_user):
        user = resolve_user(sample_user.uuid)
        assert user is not None
        assert user.uuid == sample_user.uuid

    def test_resolve_rejects_integers(self, sample_site, sample_user):
        """Integer ids are no longer identifiers — not even as strings."""
        assert resolve_site(1) is None
        assert resolve_site('1') is None
        assert resolve_user(1) is None
        assert resolve_user('1') is None

    def test_resolve_rejects_garbage(self, clean_database):
        assert resolve_site('not-a-uuid') is None
        assert resolve_site(None) is None
        assert resolve_user('999999999999999999999') is None


class TestGetUserUuidOnly:
    """GET /api/sites/<site>/users/<user> accepts UUIDs only."""

    def _get(self, client, site_id, user_id, key):
        return client.get(
            f'/api/sites/{site_id}/users/{user_id}',
            headers={'X-Tenant-Api-Key': key},
        )

    def test_by_uuid_and_uuid(self, test_client, sample_site, sample_user):
        resp = self._get(test_client, sample_site.uuid, sample_user.uuid, sample_site.tenant_api_key)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['uuid'] == sample_user.uuid
        assert data['site_uuid'] == sample_site.uuid
        assert 'id' not in data
        assert 'site_id' not in data

    def test_int_addressing_rejected(self, test_client, sample_site, sample_user):
        """Legacy integer addressing returns the uniform 401, not a match."""
        resp = self._get(test_client, '1', '1', sample_site.tenant_api_key)
        assert resp.status_code == 401

    def test_malformed_uuid_returns_401_not_500(self, test_client, sample_site):
        resp = self._get(test_client, sample_site.uuid, 'not-a-real-id', sample_site.tenant_api_key)
        assert resp.status_code == 401

    def test_oversized_integer_identifier_returns_401_not_500(self, test_client, sample_site):
        resp = self._get(test_client, sample_site.uuid, '999999999999999999999', sample_site.tenant_api_key)
        assert resp.status_code == 401

    def test_cross_site_uuid_probe_returns_401(self, test_client, sample_site, clean_database):
        """A user from another site, addressed by UUID, is still rejected."""
        current_time = int(time.time())
        other_site = db_manager.create_site(Site(
            uuid=generate_uuid7(), name="Other", domain="other-uuid.example.com",
            frontend_url="http://other-uuid.example.com",
            email_from="noreply@other-uuid.example.com", email_from_name="Other",
            created_at=current_time, updated_at=current_time,
            tenant_api_key="uuidprobe_tenant_key_64chars_aaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ))
        other_user = db_manager.create_user(User(
            uuid=generate_uuid7(), site_uuid=other_site.uuid, email="o@example.com",
            password_hash="$2b$12$x", is_verified=True, role=UserRole.USER,
            created_at=current_time, updated_at=current_time,
        ))
        # sample_site's key, but probing other_site's user by UUID.
        resp = self._get(test_client, sample_site.uuid, other_user.uuid, sample_site.tenant_api_key)
        assert resp.status_code == 401


class TestVerifyEmailResponse:
    def test_no_password_hash_in_response(self, test_client, sample_site):
        """verify-email must dump through the response schema — serializing
        the backend User model directly would leak password_hash."""
        from services.auth_service import auth_service
        user = auth_service.register_user(
            site_uuid=sample_site.uuid, email="verify-leak@example.com", password="password123",
        )
        with db_manager.get_cursor() as cursor:
            cursor.execute(
                "SELECT token FROM email_verification_tokens WHERE user_uuid = %s",
                (user.uuid,)
            )
            token = cursor.fetchone()['token']

        resp = test_client.post(
            '/api/auth/verify-email',
            json={'site_id': sample_site.uuid, 'token': token},
            headers={'X-Tenant-Api-Key': sample_site.tenant_api_key},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'password_hash' not in data['user']
        assert data['user']['uuid'] == user.uuid
        assert data['redirect_url']


class TestAdminRoutesUuidOnly:
    """Master-key routes reject integer addressing with 404."""

    def test_list_sites_returns_uuids(self, test_client, sample_site):
        """Regression: list_sites carries inline SQL that must select uuid,
        not the dropped int id (broke in prod as UndefinedColumn on v50)."""
        from config import get_config
        resp = test_client.get(
            '/api/sites',
            headers={'X-API-Key': get_config().MASTER_API_KEY},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert any(s['uuid'] == sample_site.uuid for s in data)

    def test_get_site_by_int_returns_404(self, test_client, sample_site):
        from config import get_config
        resp = test_client.get(
            '/api/sites/1',
            headers={'X-API-Key': get_config().MASTER_API_KEY},
        )
        assert resp.status_code == 404

    def test_get_site_by_uuid_works(self, test_client, sample_site):
        from config import get_config
        resp = test_client.get(
            f'/api/sites/{sample_site.uuid}',
            headers={'X-API-Key': get_config().MASTER_API_KEY},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['uuid'] == sample_site.uuid
        assert 'id' not in data
