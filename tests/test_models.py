import time
from byteforge_aegis_models import AuthToken, Site, UserRole
from models.user import User
from models.email_verification_token import EmailVerificationToken
from models.password_reset_token import PasswordResetToken
from models.email_change_request import EmailChangeRequest

SITE_UUID = "0191e1a0-0000-7000-8000-000000000001"
USER_UUID = "0191e1a0-0000-7000-8000-0000000000aa"


def test_site_to_dict():
    """Test Site model to_dict method"""
    current_time = int(time.time())
    site = Site(
        uuid=SITE_UUID,
        name="Test Site",
        domain="test.example.com",
        frontend_url="http://test.example.com",
        email_from="noreply@test.example.com",
        email_from_name="Test Site",
        created_at=current_time,
        updated_at=current_time
    )

    site_dict = site.to_dict()

    assert site_dict['uuid'] == SITE_UUID
    assert site_dict['name'] == "Test Site"
    assert site_dict['domain'] == "test.example.com"
    assert site_dict['frontend_url'] == "http://test.example.com"
    assert site_dict['email_from'] == "noreply@test.example.com"
    assert site_dict['email_from_name'] == "Test Site"
    assert site_dict['created_at'] == current_time
    assert site_dict['updated_at'] == current_time
    assert 'id' not in site_dict


def test_site_from_dict():
    """Test Site model from_dict method"""
    current_time = int(time.time())
    site_dict = {
        'uuid': SITE_UUID,
        'name': "Test Site",
        'domain': "test.example.com",
        'frontend_url': "http://test.example.com",
        'email_from': "noreply@test.example.com",
        'email_from_name': "Test Site",
        'created_at': current_time,
        'updated_at': current_time
    }

    site = Site.from_dict(site_dict)

    assert site.uuid == SITE_UUID
    assert site.name == "Test Site"
    assert site.domain == "test.example.com"
    assert site.frontend_url == "http://test.example.com"
    assert site.email_from == "noreply@test.example.com"
    assert site.email_from_name == "Test Site"
    assert site.created_at == current_time
    assert site.updated_at == current_time


def test_user_to_dict():
    """Test User model to_dict method"""
    current_time = int(time.time())
    user = User(
        uuid=USER_UUID,
        site_uuid=SITE_UUID,
        email="test@example.com",
        password_hash="hashed_password",
        is_verified=True,
        role=UserRole.USER,
        created_at=current_time,
        updated_at=current_time
    )

    user_dict = user.to_dict()

    assert user_dict['uuid'] == USER_UUID
    assert user_dict['site_uuid'] == SITE_UUID
    assert user_dict['email'] == "test@example.com"
    assert user_dict['password_hash'] == "hashed_password"
    assert user_dict['is_verified'] is True
    assert user_dict['role'] == 'user'
    assert user_dict['created_at'] == current_time
    assert user_dict['updated_at'] == current_time
    assert 'id' not in user_dict
    assert 'site_id' not in user_dict


def test_user_from_dict():
    """Test User model from_dict method"""
    current_time = int(time.time())
    user_dict = {
        'uuid': USER_UUID,
        'site_uuid': SITE_UUID,
        'email': "test@example.com",
        'password_hash': "hashed_password",
        'is_verified': True,
        'role': 'user',
        'created_at': current_time,
        'updated_at': current_time
    }

    user = User.from_dict(user_dict)

    assert user.uuid == USER_UUID
    assert user.site_uuid == SITE_UUID
    assert user.email == "test@example.com"
    assert user.password_hash == "hashed_password"
    assert user.is_verified is True
    assert user.role == UserRole.USER
    assert user.created_at == current_time
    assert user.updated_at == current_time


def test_auth_token_to_dict():
    """Test AuthToken model to_dict method"""
    current_time = int(time.time())
    token = AuthToken(
        token="test_token",
        user_uuid=USER_UUID,
        expires_at=current_time + 3600,
        site_uuid=SITE_UUID,
        created_at=current_time
    )

    token_dict = token.to_dict()

    assert token_dict['token'] == "test_token"
    assert token_dict['user_uuid'] == USER_UUID
    assert token_dict['site_uuid'] == SITE_UUID
    assert token_dict['expires_at'] == current_time + 3600
    assert token_dict['created_at'] == current_time


def test_auth_token_from_dict():
    """Test AuthToken model from_dict method"""
    current_time = int(time.time())
    token_dict = {
        'token': "test_token",
        'user_uuid': USER_UUID,
        'site_uuid': SITE_UUID,
        'expires_at': current_time + 3600,
        'created_at': current_time
    }

    token = AuthToken.from_dict(token_dict)

    assert token.token == "test_token"
    assert token.user_uuid == USER_UUID
    assert token.site_uuid == SITE_UUID
    assert token.expires_at == current_time + 3600
    assert token.created_at == current_time
