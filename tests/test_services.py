import time
from services.password_service import password_service
from services.token_service import token_service
from services.auth_service import auth_service
from byteforge_aegis_models import UserRole


def test_password_hashing():
    """Test password hashing and verification"""
    password = "my_secure_password"

    hashed = password_service.hash_password(password)

    assert hashed != password
    assert password_service.verify_password(password, hashed) is True
    assert password_service.verify_password("wrong_password", hashed) is False


def test_token_generation():
    """Test secure token generation"""
    token1 = token_service.generate_token()
    token2 = token_service.generate_token()

    assert len(token1) > 20
    assert len(token2) > 20
    assert token1 != token2


def test_create_auth_token(sample_site, sample_user):
    """Test creating an auth token"""
    auth_token = token_service.create_auth_token(sample_site.id, sample_user.id)

    assert auth_token.token is not None
    assert auth_token.site_id == sample_site.id
    assert auth_token.user_id == sample_user.id
    assert auth_token.expires_at > int(time.time())


def test_validate_auth_token(sample_site, sample_user):
    """Test validating an auth token"""
    auth_token = token_service.create_auth_token(sample_site.id, sample_user.id)

    user_id = token_service.validate_auth_token(auth_token.token)

    assert user_id == sample_user.id


def test_validate_expired_auth_token(sample_site, sample_user):
    """Test that expired tokens are invalid"""
    from byteforge_aegis_models import AuthToken
    from database import db_manager

    # Create an expired token
    current_time = int(time.time())
    expired_token = AuthToken(
        token="expired_token",
        site_id=sample_site.id,
        user_id=sample_user.id,
        expires_at=current_time - 3600,  # Expired 1 hour ago
        created_at=current_time - 7200
    )
    db_manager.create_auth_token(expired_token)

    user_id = token_service.validate_auth_token("expired_token")

    assert user_id is None


def test_register_user(sample_site):
    """Test user registration"""
    user = auth_service.register_user(
        site_id=sample_site.id,
        email="newuser@example.com",
        password="secure_password123"
    )

    assert user.id > 0
    assert user.site_id == sample_site.id
    assert user.email == "newuser@example.com"
    assert user.is_verified is False
    assert user.role == UserRole.USER
    assert user.password_hash != "secure_password123"


def test_register_duplicate_email(sample_site):
    """Test that duplicate emails return None for self-registration (prevents enumeration)"""
    auth_service.register_user(
        site_id=sample_site.id,
        email="duplicate@example.com",
        password="password1"
    )

    # Self-registration with duplicate email should return None (not raise error)
    # This prevents email enumeration attacks
    result = auth_service.register_user(
        site_id=sample_site.id,
        email="duplicate@example.com",
        password="password2"
    )
    assert result is None


def test_register_duplicate_email_admin(sample_site):
    """Test that admin registration raises error for duplicate emails"""
    auth_service.register_user(
        site_id=sample_site.id,
        email="admin_dup@example.com",
        password="password1"
    )

    # Admin registration should still raise error for duplicate
    try:
        auth_service.register_user(
            site_id=sample_site.id,
            email="admin_dup@example.com",
            password="password2",
            is_admin_registration=True
        )
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "already registered" in str(e).lower()


def test_login_success(sample_site):
    """Test successful login"""
    # Register user
    auth_service.register_user(
        site_id=sample_site.id,
        email="login@example.com",
        password="mypassword"
    )

    # Mark user as verified
    from database import db_manager
    user = db_manager.find_user_by_email(sample_site.id, "login@example.com")
    user.is_verified = True
    db_manager.update_user(user)

    # Login
    login_result = auth_service.login(
        site_id=sample_site.id,
        email="login@example.com",
        password="mypassword"
    )

    assert login_result.auth_token.token is not None
    assert login_result.auth_token.user_id == user.id
    assert login_result.refresh_token is not None
    assert login_result.refresh_token.user_id == user.id


def test_login_wrong_password(sample_site):
    """Test login with wrong password"""
    # Register user
    auth_service.register_user(
        site_id=sample_site.id,
        email="wrongpw@example.com",
        password="correct_password"
    )

    # Mark as verified
    from database import db_manager
    user = db_manager.find_user_by_email(sample_site.id, "wrongpw@example.com")
    user.is_verified = True
    db_manager.update_user(user)

    # Try login with wrong password
    try:
        auth_service.login(
            site_id=sample_site.id,
            email="wrongpw@example.com",
            password="wrong_password"
        )
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "invalid credentials" in str(e).lower()


def test_login_unverified_email(sample_site):
    """Test that unverified users cannot login"""
    auth_service.register_user(
        site_id=sample_site.id,
        email="unverified@example.com",
        password="password"
    )

    try:
        auth_service.login(
            site_id=sample_site.id,
            email="unverified@example.com",
            password="password"
        )
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "not verified" in str(e).lower()


def test_verify_email(sample_site):
    """Test email verification"""
    user = auth_service.register_user(
        site_id=sample_site.id,
        email="verify@example.com",
        password="password"
    )

    # Get verification token
    from database import db_manager
    with db_manager.get_cursor() as cursor:
        cursor.execute(
            "SELECT token FROM email_verification_tokens WHERE user_id = %s",
            (user.id,)
        )
        result = cursor.fetchone()
        verification_token = result['token']

    # Verify email
    result = auth_service.verify_email(verification_token)

    assert result.user.is_verified is True
    assert result.user.id == user.id
    assert result.redirect_url == sample_site.frontend_url


def test_change_password(sample_site):
    """Test password change"""
    user = auth_service.register_user(
        site_id=sample_site.id,
        email="changepw@example.com",
        password="old_password"
    )

    updated_user = auth_service.change_password(
        user_id=user.id,
        old_password="old_password",
        new_password="new_password"
    )

    assert updated_user.id == user.id

    # Verify new password works
    assert password_service.verify_password("new_password", updated_user.password_hash) is True
    assert password_service.verify_password("old_password", updated_user.password_hash) is False


def test_password_reset_flow(sample_site):
    """Test complete password reset flow"""
    user = auth_service.register_user(
        site_id=sample_site.id,
        email="reset@example.com",
        password="original_password"
    )

    # Request password reset
    reset_token = auth_service.request_password_reset(sample_site.id, "reset@example.com")

    assert reset_token is not None

    # Reset password
    updated_user = auth_service.reset_password(reset_token, "new_reset_password")

    assert updated_user.id == user.id
    assert password_service.verify_password("new_reset_password", updated_user.password_hash) is True
