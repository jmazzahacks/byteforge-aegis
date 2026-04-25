"""
Tests that token-based auth flows reject cross-site token submission.

After tenant_api_key gating, the public auth endpoints accept site_id in
the body for routing the gate. Without service-layer enforcement, an
attacker holding the tenant_api_key for site A could submit a verification
or reset token belonging to a user in site B and trigger that user's flow
under site A's gate. The service layer must reject any token whose owning
user is in a different site than the supplied site_id.
"""
import time
from byteforge_aegis_models import Site, UserRole
from models.user import User
from database import db_manager
from services.auth_service import auth_service
from services.token_service import token_service
import pytest


@pytest.fixture
def two_sites(clean_database):
    """Two distinct sites with their own users and tokens."""
    now = int(time.time())
    site_a = db_manager.create_site(Site(
        id=0, name="Site A", domain="a.example.com",
        frontend_url="http://a.example.com",
        email_from="noreply@a.example.com", email_from_name="Site A",
        created_at=now, updated_at=now,
        tenant_api_key="key_a_0000000000000000000000000000000000000000000000000000",
    ))
    site_b = db_manager.create_site(Site(
        id=0, name="Site B", domain="b.example.com",
        frontend_url="http://b.example.com",
        email_from="noreply@b.example.com", email_from_name="Site B",
        created_at=now, updated_at=now,
        tenant_api_key="key_b_0000000000000000000000000000000000000000000000000000",
    ))
    user_b = db_manager.create_user(User(
        id=0, site_id=site_b.id, email="victim@b.example.com",
        password_hash="$2b$12$hashed", is_verified=False, role=UserRole.USER,
        created_at=now, updated_at=now,
    ))
    return site_a, site_b, user_b


def test_verify_email_rejects_token_from_another_site(two_sites):
    site_a, site_b, user_b = two_sites
    token_obj = token_service.create_email_verification_token(
        site_id=site_b.id, user_id=user_b.id
    )

    # Attacker has site A's tenant key but supplies site B's user's token,
    # claiming site_id = A. Must be rejected.
    with pytest.raises(ValueError, match="Invalid or expired"):
        auth_service.verify_email(token_obj.token, site_id=site_a.id)

    # Sanity: the same call with the correct site_id succeeds.
    result = auth_service.verify_email(token_obj.token, site_id=site_b.id)
    assert result.user.id == user_b.id


def test_check_verification_token_rejects_token_from_another_site(two_sites):
    site_a, site_b, user_b = two_sites
    token_obj = token_service.create_email_verification_token(
        site_id=site_b.id, user_id=user_b.id
    )

    with pytest.raises(ValueError, match="Invalid or expired"):
        auth_service.check_verification_token(token_obj.token, site_id=site_a.id)

    status = auth_service.check_verification_token(token_obj.token, site_id=site_b.id)
    assert status.email == user_b.email


def test_reset_password_rejects_token_from_another_site(two_sites):
    site_a, site_b, user_b = two_sites
    token_obj = token_service.create_password_reset_token(
        site_id=site_b.id, user_id=user_b.id
    )

    with pytest.raises(ValueError, match="Invalid or expired"):
        auth_service.reset_password(token_obj.token, site_id=site_a.id, new_password="newpass99")
