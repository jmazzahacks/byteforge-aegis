import pytest
import time
import os
import sys

# Set test database BEFORE importing any modules that use it
os.environ['DB_NAME'] = 'aegis_test'

# Add src to path so we can import our modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from database import db_manager
from models.site import Site
from models.user import User
from models.user_role import UserRole
from models.auth_token import AuthToken
from app import create_app


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
        id=0,
        name="Test Site",
        domain="test.example.com",
        frontend_url="http://test.example.com",
        email_from="noreply@test.example.com",
        email_from_name="Test Site",
        created_at=current_time,
        updated_at=current_time
    )
    return db_manager.create_site(site)


@pytest.fixture
def sample_user(sample_site):
    """Create a sample user for testing"""
    current_time = int(time.time())
    user = User(
        id=0,
        site_id=sample_site.id,
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
        id=0,
        site_id=sample_site.id,
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
        site_id=sample_site.id,
        user_id=admin_user.id,
        expires_at=current_time + 3600,
        created_at=current_time
    )
    return db_manager.create_auth_token(token)


@pytest.fixture
def user_auth_token(sample_site, sample_user):
    """Create an auth token for a regular user"""
    current_time = int(time.time())
    token = AuthToken(
        token="user_test_token_456",
        site_id=sample_site.id,
        user_id=sample_user.id,
        expires_at=current_time + 3600,
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
