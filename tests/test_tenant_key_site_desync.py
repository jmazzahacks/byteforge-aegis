"""
Regression tests: the tenant-key gate and the handler must agree on which
site is being addressed.

The gate reads site_id from the JSON body first and only falls back to the
path, while /api/sites/<site_id>/users/<user_id> authorizes against the path.
A caller who supplies both can therefore authenticate against their own site
and be authorized against someone else's — a cross-tenant read.
"""
import time

import pytest

from byteforge_aegis_models import Site, UserRole
from database import db_manager
from models import User
from utils.uuid7 import generate_uuid7


@pytest.fixture
def victim_site(sample_site):
    """A second site with a different tenant API key."""
    now = int(time.time())
    site = Site(
        uuid=generate_uuid7(),
        name="Victim Site",
        domain="victim.example.com",
        frontend_url="http://victim.example.com",
        email_from="noreply@victim.example.com",
        email_from_name="Victim Site",
        created_at=now,
        updated_at=now,
        tenant_api_key="victim_tenant_api_key_64chars_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    )
    return db_manager.create_site(site)


@pytest.fixture
def victim_user(victim_site):
    now = int(time.time())
    user = User(
        uuid=generate_uuid7(),
        site_uuid=victim_site.uuid,
        email="ceo@victim.example.com",
        password_hash="$2b$12$hashed_password",
        is_verified=True,
        role=UserRole.ADMIN,
        created_at=now,
        updated_at=now,
    )
    return db_manager.create_user(user)


def test_body_site_id_cannot_override_path_site_id(
    test_client, sample_site, victim_site, victim_user
):
    """Attacker holds site A's key; targets site B's user via the path."""
    response = test_client.get(
        f'/api/sites/{victim_site.uuid}/users/{victim_user.uuid}',
        headers={'X-Tenant-Api-Key': sample_site.tenant_api_key},
        json={'site_id': sample_site.uuid},
    )

    assert response.status_code == 401, (
        f"cross-tenant read succeeded: {response.get_json()}"
    )


def test_non_ascii_key_does_not_500_or_reveal_site_existence(
    test_client, sample_site
):
    """hmac.compare_digest raises TypeError on non-ASCII str.

    The site is resolved before the comparison, so the crash fired only when
    the site existed — an unauthenticated caller could tell a real site UUID
    from a bogus one by 500 vs 401.
    """
    real = test_client.post(
        '/api/auth/login',
        headers={'X-Tenant-Api-Key': 'keé'},
        json={'site_id': sample_site.uuid, 'email': 'a@b.com', 'password': 'whatever8'},
    )
    bogus = test_client.post(
        '/api/auth/login',
        headers={'X-Tenant-Api-Key': 'keé'},
        json={'site_id': '019fae10-0000-0000-0000-000000000000',
              'email': 'a@b.com', 'password': 'whatever8'},
    )

    assert real.status_code == 401, f"expected 401, got {real.status_code}"
    assert real.status_code == bogus.status_code
    assert real.get_json() == bogus.get_json()


def test_own_site_lookup_still_works(test_client, sample_site, sample_user):
    """The legitimate path must keep working after the fix."""
    response = test_client.get(
        f'/api/sites/{sample_site.uuid}/users/{sample_user.uuid}',
        headers={'X-Tenant-Api-Key': sample_site.tenant_api_key},
    )

    assert response.status_code == 200
    assert response.get_json()['email'] == sample_user.email
