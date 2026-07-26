import pytest
import time
import os
import sys
from typing import Optional

# Set test database BEFORE importing any modules that use it
os.environ['DB_NAME'] = 'aegis_test'

# Blank the global Mailgun fallback BEFORE config loads. Test fixtures create
# sites with no per-site Mailgun keys, so a configured fallback (e.g. a real
# key in the local .env) silently routes every test email through a REAL
# production Mailgun account — which happened on 2026-07-26 and looked like a
# leaked API key. The autouse no_outbound_email fixture below is the primary
# guard; this makes any unstubbed path fail closed instead of sending.
os.environ['MAILGUN_API_KEY'] = ''
os.environ['MAILGUN_DOMAIN'] = ''

# Add src to path so we can import our modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from database import db_manager
from byteforge_aegis_models import AuthToken, Site, UserRole
from models.user import User
from services import email_service as email_service_module
from services.email_service import EmailService
from utils.uuid7 import generate_uuid7
from app import create_app


@pytest.fixture(autouse=True)
def no_outbound_email(monkeypatch):
    """Tests must never send real email or reach the network.

    Layer 1 stubs EmailService.send_email — every email flow (verification,
    password reset, email change, registration attempt) funnels through it.
    Layer 2 makes any requests.post that still slips through fail loudly
    instead of silently hitting a live API; tests that need an HTTP call
    stub requests.post themselves (see test_webhook_service.py).
    """
    def fake_send_email(self, to_email: str, subject: str, html_content: str,
                        from_email: str, from_name: str,
                        mailgun_domain: Optional[str] = None,
                        mailgun_api_key: Optional[str] = None,
                        text_content: Optional[str] = None) -> bool:
        return True

    def forbid_outbound_post(*args, **kwargs):
        raise AssertionError(
            "Test attempted an outbound HTTP POST — stub the service instead"
        )

    monkeypatch.setattr(EmailService, 'send_email', fake_send_email)
    monkeypatch.setattr(email_service_module.requests, 'post', forbid_outbound_post)


_real_send_email = EmailService.send_email


@pytest.fixture
def real_send_email(monkeypatch):
    """Opt-out for tests that exercise EmailService.send_email internals.

    Restores the real method; the outbound-POST guard stays active, so such
    tests must still stub requests.post themselves.
    """
    monkeypatch.setattr(EmailService, 'send_email', _real_send_email)


@pytest.fixture(scope='function')
def clean_database():
    """Clean all tables before each test"""
    with db_manager.get_cursor(commit=True) as cursor:
        cursor.execute("TRUNCATE sites, users, auth_tokens, refresh_tokens, email_verification_tokens, password_reset_tokens, email_change_requests, webhook_events CASCADE")
    yield
    with db_manager.get_cursor(commit=True) as cursor:
        cursor.execute("TRUNCATE sites, users, auth_tokens, refresh_tokens, email_verification_tokens, password_reset_tokens, email_change_requests, webhook_events CASCADE")


@pytest.fixture
def sample_site(clean_database):
    """Create a sample site for testing"""
    current_time = int(time.time())
    site = Site(
        uuid=generate_uuid7(),
        name="Test Site",
        domain="test.example.com",
        frontend_url="http://test.example.com",
        email_from="noreply@test.example.com",
        email_from_name="Test Site",
        created_at=current_time,
        updated_at=current_time,
        tenant_api_key="test_tenant_api_key_64chars_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )
    return db_manager.create_site(site)


@pytest.fixture
def sample_user(sample_site):
    """Create a sample user for testing"""
    current_time = int(time.time())
    user = User(
        uuid=generate_uuid7(),
        site_uuid=sample_site.uuid,
        email="test@example.com",
        password_hash="$2b$12$hashed_password",
        is_verified=False,
        role=UserRole.USER,
        created_at=current_time,
        updated_at=current_time
    )
    return db_manager.create_user(user)


@pytest.fixture
def admin_user(sample_site):
    """Create an admin user for testing"""
    current_time = int(time.time())
    user = User(
        uuid=generate_uuid7(),
        site_uuid=sample_site.uuid,
        email="admin@example.com",
        password_hash="$2b$12$hashed_password",
        is_verified=True,
        role=UserRole.ADMIN,
        created_at=current_time,
        updated_at=current_time
    )
    return db_manager.create_user(user)


@pytest.fixture
def admin_auth_token(sample_site, admin_user):
    """Create an auth token for the admin user"""
    current_time = int(time.time())
    token = AuthToken(
        token="admin_test_token_123",
        user_uuid=admin_user.uuid,
        expires_at=current_time + 3600,
        site_uuid=sample_site.uuid,
        created_at=current_time
    )
    return db_manager.create_auth_token(token)


@pytest.fixture
def user_auth_token(sample_site, sample_user):
    """Create an auth token for a regular user"""
    current_time = int(time.time())
    token = AuthToken(
        token="user_test_token_456",
        user_uuid=sample_user.uuid,
        expires_at=current_time + 3600,
        site_uuid=sample_site.uuid,
        created_at=current_time
    )
    return db_manager.create_auth_token(token)


@pytest.fixture
def test_client():
    """Create a Flask test client"""
    app = create_app()
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client
