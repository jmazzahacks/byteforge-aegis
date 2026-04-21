"""
Tests for the GET /api/auth/me endpoint.
"""
import time
from database import db_manager
from byteforge_aegis_models import AuthToken


def test_me_success(test_client, user_auth_token, sample_user):
    """A valid bearer token returns the owning user."""
    response = test_client.get(
        '/api/auth/me',
        headers={'Authorization': f'Bearer {user_auth_token.token}'}
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data['id'] == sample_user.id
    assert data['site_id'] == sample_user.site_id
    assert data['email'] == sample_user.email
    assert data['is_verified'] == sample_user.is_verified
    assert data['role'] == sample_user.role.value
    assert 'password_hash' not in data


def test_me_sets_cache_control_no_store(test_client, user_auth_token):
    """Response must not be cacheable by intermediaries."""
    response = test_client.get(
        '/api/auth/me',
        headers={'Authorization': f'Bearer {user_auth_token.token}'}
    )

    assert response.status_code == 200
    assert response.headers.get('Cache-Control') == 'no-store'


def test_me_missing_auth_header(test_client, clean_database):
    """No Authorization header returns 401."""
    response = test_client.get('/api/auth/me')

    assert response.status_code == 401
    data = response.get_json()
    assert 'error' in data
    assert 'missing' in data['error'].lower()


def test_me_malformed_auth_header(test_client, user_auth_token):
    """Authorization header without 'Bearer ' prefix returns 401."""
    response = test_client.get(
        '/api/auth/me',
        headers={'Authorization': user_auth_token.token}
    )

    assert response.status_code == 401
    data = response.get_json()
    assert 'error' in data
    assert 'invalid' in data['error'].lower()


def test_me_unknown_token(test_client, clean_database):
    """Token not in the database returns 401."""
    response = test_client.get(
        '/api/auth/me',
        headers={'Authorization': 'Bearer totally_bogus_token_does_not_exist'}
    )

    assert response.status_code == 401
    data = response.get_json()
    assert 'error' in data


def test_me_expired_token(test_client, sample_site, sample_user):
    """An expired token returns 401."""
    current_time = int(time.time())
    expired = AuthToken(
        token='expired_me_token',
        site_id=sample_site.id,
        user_id=sample_user.id,
        expires_at=current_time - 3600,
        created_at=current_time - 7200,
    )
    db_manager.create_auth_token(expired)

    response = test_client.get(
        '/api/auth/me',
        headers={'Authorization': 'Bearer expired_me_token'}
    )

    assert response.status_code == 401
    data = response.get_json()
    assert 'error' in data


def test_me_admin_user(test_client, admin_auth_token, admin_user):
    """Endpoint works for admin users too and reports admin role."""
    response = test_client.get(
        '/api/auth/me',
        headers={'Authorization': f'Bearer {admin_auth_token.token}'}
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data['id'] == admin_user.id
    assert data['role'] == 'admin'
