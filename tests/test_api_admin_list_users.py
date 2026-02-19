"""
Tests for the admin list users endpoint.
"""
import time
from database import db_manager
from byteforge_aegis_models import Site, UserRole
from models.user import User


def test_admin_list_users_success(test_client, admin_auth_token, sample_user, admin_user):
    """Test that an admin can successfully list users for their site"""
    response = test_client.get(
        '/api/admin/users',
        headers={'Authorization': f'Bearer {admin_auth_token.token}'}
    )

    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    assert len(data) == 2  # admin_user and sample_user

    emails = [user['email'] for user in data]
    assert 'admin@example.com' in emails
    assert 'test@example.com' in emails

    # Verify password_hash is not exposed
    for user in data:
        assert 'password_hash' not in user


def test_admin_list_users_non_admin_forbidden(test_client, user_auth_token):
    """Test that a non-admin user gets 403 forbidden"""
    response = test_client.get(
        '/api/admin/users',
        headers={'Authorization': f'Bearer {user_auth_token.token}'}
    )

    assert response.status_code == 403
    data = response.get_json()
    assert 'error' in data
    assert 'insufficient permissions' in data['error'].lower()


def test_admin_list_users_missing_auth_header(test_client, clean_database):
    """Test that missing auth header returns 401"""
    response = test_client.get('/api/admin/users')

    assert response.status_code == 401
    data = response.get_json()
    assert 'error' in data
    assert 'missing' in data['error'].lower()


def test_admin_list_users_invalid_token(test_client, clean_database):
    """Test that an invalid token returns 401"""
    response = test_client.get(
        '/api/admin/users',
        headers={'Authorization': 'Bearer invalid_token_xyz'}
    )

    assert response.status_code == 401
    data = response.get_json()
    assert 'error' in data


def test_admin_list_users_invalid_auth_format(test_client, admin_auth_token):
    """Test that invalid auth header format returns 401"""
    response = test_client.get(
        '/api/admin/users',
        headers={'Authorization': admin_auth_token.token}  # Missing 'Bearer ' prefix
    )

    assert response.status_code == 401
    data = response.get_json()
    assert 'error' in data
    assert 'invalid' in data['error'].lower()


def test_admin_list_users_site_isolation(test_client, sample_site, admin_user, admin_auth_token):
    """Test that admin only sees users from their own site, not other sites"""
    # Create another site with users
    current_time = int(time.time())
    other_site = Site(
        id=0,
        name="Other Site",
        domain="other.example.com",
        frontend_url="http://other.example.com",
        email_from="noreply@other.example.com",
        email_from_name="Other Site",
        created_at=current_time,
        updated_at=current_time
    )
    other_site = db_manager.create_site(other_site)

    # Create a user on the other site
    other_user = User(
        id=0,
        site_id=other_site.id,
        email="other@example.com",
        password_hash="$2b$12$hashed_password",
        is_verified=True,
        role=UserRole.USER,
        created_at=current_time,
        updated_at=current_time
    )
    db_manager.create_user(other_user)

    # Admin from sample_site should only see users from sample_site
    response = test_client.get(
        '/api/admin/users',
        headers={'Authorization': f'Bearer {admin_auth_token.token}'}
    )

    assert response.status_code == 200
    data = response.get_json()

    # Should only see admin user from sample_site (not other_user)
    emails = [user['email'] for user in data]
    assert 'admin@example.com' in emails
    assert 'other@example.com' not in emails


def test_admin_list_users_returns_user_fields(test_client, admin_auth_token, admin_user):
    """Test that the response contains expected user fields"""
    response = test_client.get(
        '/api/admin/users',
        headers={'Authorization': f'Bearer {admin_auth_token.token}'}
    )

    assert response.status_code == 200
    data = response.get_json()
    assert len(data) >= 1

    user = data[0]
    expected_fields = ['id', 'site_id', 'email', 'is_verified', 'role', 'created_at', 'updated_at']
    for field in expected_fields:
        assert field in user, f"Expected field '{field}' not found in response"


def test_admin_list_users_expired_token(test_client, sample_site, admin_user):
    """Test that an expired token returns 401"""
    from byteforge_aegis_models import AuthToken

    current_time = int(time.time())
    expired_token = AuthToken(
        token="expired_admin_token",
        site_id=sample_site.id,
        user_id=admin_user.id,
        expires_at=current_time - 3600,  # Expired 1 hour ago
        created_at=current_time - 7200
    )
    db_manager.create_auth_token(expired_token)

    response = test_client.get(
        '/api/admin/users',
        headers={'Authorization': 'Bearer expired_admin_token'}
    )

    assert response.status_code == 401
    data = response.get_json()
    assert 'error' in data
