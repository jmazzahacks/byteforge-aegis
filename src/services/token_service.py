import secrets
import time
from typing import Optional
from database import db_manager
from config import get_config
from byteforge_aegis_models import AuthToken, RefreshToken
from models.refresh_token_result import RefreshTokenResult
from models.email_verification_token import EmailVerificationToken
from models.password_reset_token import PasswordResetToken
from models.email_change_request import EmailChangeRequest


class TokenService:
    """Service for managing authentication and verification tokens"""

    def __init__(self):
        self.config = get_config()

    def generate_token(self) -> str:
        """
        Generate a secure random token using URL-safe base64 encoding.

        Returns:
            str: A cryptographically secure random token string
        """
        return secrets.token_urlsafe(32)

    def create_auth_token(self, site_id: int, user_id: int) -> AuthToken:
        """
        Create a new authentication token for user session management.

        Args:
            site_id: The ID of the site this token belongs to
            user_id: The ID of the user to create the token for

        Returns:
            AuthToken: The created auth token model
        """
        token_str = self.generate_token()
        created_at = int(time.time())
        expires_at = created_at + self.config.AUTH_TOKEN_EXPIRATION

        auth_token = AuthToken(
            token=token_str,
            site_id=site_id,
            user_id=user_id,
            expires_at=expires_at,
            created_at=created_at
        )

        return db_manager.create_auth_token(auth_token)

    def validate_auth_token(self, token: str) -> Optional[int]:
        """
        Validate an authentication token and check if it's still valid.

        Args:
            token: The auth token string to validate

        Returns:
            Optional[int]: The user_id if token is valid, None if invalid or expired
        """
        auth_token = db_manager.find_auth_token_by_token(token)

        if not auth_token:
            return None

        current_time = int(time.time())
        if auth_token.expires_at < current_time:
            return None

        return auth_token.user_id

    def invalidate_auth_token(self, token: str) -> bool:
        """
        Invalidate (delete) a specific authentication token.

        Args:
            token: The auth token string to invalidate

        Returns:
            bool: True if token was found and deleted, False otherwise
        """
        return db_manager.delete_auth_token(token)

    def invalidate_user_tokens(self, user_id: int) -> None:
        """
        Invalidate all authentication tokens for a specific user.

        Args:
            user_id: The ID of the user whose tokens should be invalidated
        """
        db_manager.delete_auth_tokens_by_user(user_id)

    def create_refresh_token(self, site_id: int, user_id: int, family_id: Optional[str] = None) -> RefreshToken:
        """
        Create a new refresh token for long-lived session management.

        Args:
            site_id: The ID of the site this token belongs to
            user_id: The ID of the user to create the token for
            family_id: Optional family ID for token rotation (generates new if None)

        Returns:
            RefreshToken: The created refresh token model
        """
        token_str = self.generate_token()
        created_at = int(time.time())
        expires_at = created_at + self.config.REFRESH_TOKEN_EXPIRATION

        if family_id is None:
            family_id = self.generate_token()

        refresh_token = RefreshToken(
            token=token_str,
            site_id=site_id,
            user_id=user_id,
            family_id=family_id,
            expires_at=expires_at,
            created_at=created_at,
            used_at=None,
            revoked=False
        )

        return db_manager.create_refresh_token(refresh_token)

    def validate_and_rotate_refresh_token(self, token: str) -> Optional[RefreshTokenResult]:
        """
        Validate a refresh token and optionally rotate it.

        Handles concurrent request race conditions with grace period.
        Detects potential token theft when used token is presented after grace period.

        Args:
            token: The refresh token string to validate

        Returns:
            Optional[RefreshTokenResult]: Result containing user_id, site_id, and new token if valid

        Raises:
            ValueError: If token reuse detected (potential theft)
        """
        refresh_token = db_manager.find_refresh_token_by_token(token)

        if not refresh_token:
            return None

        current_time = int(time.time())

        if refresh_token.revoked:
            return None

        if refresh_token.expires_at < current_time:
            return None

        if self.config.REFRESH_TOKEN_ROTATION:
            if refresh_token.used_at is not None:
                grace_period_end = refresh_token.used_at + self.config.REFRESH_TOKEN_GRACE_PERIOD

                if current_time <= grace_period_end:
                    latest = db_manager.find_latest_refresh_token_in_family(refresh_token.family_id)
                    if latest and latest.token != refresh_token.token:
                        return RefreshTokenResult(
                            user_id=latest.user_id,
                            site_id=latest.site_id,
                            new_refresh_token=latest
                        )
                    return RefreshTokenResult(
                        user_id=refresh_token.user_id,
                        site_id=refresh_token.site_id,
                        new_refresh_token=None
                    )
                else:
                    db_manager.revoke_refresh_token_family(refresh_token.family_id)
                    raise ValueError("Refresh token reuse detected - all sessions revoked")

            db_manager.mark_refresh_token_used(token, current_time)
            new_token = self.create_refresh_token(
                refresh_token.site_id,
                refresh_token.user_id,
                refresh_token.family_id
            )
            return RefreshTokenResult(
                user_id=refresh_token.user_id,
                site_id=refresh_token.site_id,
                new_refresh_token=new_token
            )
        else:
            return RefreshTokenResult(
                user_id=refresh_token.user_id,
                site_id=refresh_token.site_id,
                new_refresh_token=None
            )

    def invalidate_user_refresh_tokens(self, user_id: int) -> None:
        """
        Invalidate all refresh tokens for a specific user.

        Args:
            user_id: The ID of the user whose refresh tokens should be invalidated
        """
        db_manager.delete_refresh_tokens_by_user(user_id)

    def create_email_verification_token(self, site_id: int, user_id: int) -> EmailVerificationToken:
        """
        Create an email verification token for confirming user email ownership.

        Args:
            site_id: The ID of the site this token belongs to
            user_id: The ID of the user to create the verification token for

        Returns:
            EmailVerificationToken: The created verification token model
        """
        token_str = self.generate_token()
        created_at = int(time.time())
        expires_at = created_at + self.config.EMAIL_VERIFICATION_EXPIRATION

        email_token = EmailVerificationToken(
            token=token_str,
            site_id=site_id,
            user_id=user_id,
            expires_at=expires_at,
            created_at=created_at
        )

        return db_manager.create_email_verification_token(email_token)

    def check_email_verification_token(self, token: str) -> Optional[int]:
        """
        Check an email verification token without consuming it.

        Non-destructive check - the token remains valid for later use.
        Use this to check if a password is required before verification.

        Args:
            token: The email verification token string to check

        Returns:
            Optional[int]: The user_id if token is valid, None if invalid or expired
        """
        email_token = db_manager.find_email_verification_token(token)

        if not email_token:
            return None

        current_time = int(time.time())
        if email_token.expires_at < current_time:
            return None

        return email_token.user_id

    def validate_email_verification_token(self, token: str) -> Optional[int]:
        """
        Validate an email verification token and mark it as used by deleting it.

        Args:
            token: The email verification token string to validate

        Returns:
            Optional[int]: The user_id if token is valid, None if invalid or expired
        """
        email_token = db_manager.find_email_verification_token(token)

        if not email_token:
            return None

        current_time = int(time.time())
        if email_token.expires_at < current_time:
            return None

        # Delete token after successful validation (one-time use)
        db_manager.delete_email_verification_token(token)

        return email_token.user_id

    def create_password_reset_token(self, site_id: int, user_id: int) -> PasswordResetToken:
        """
        Create a password reset token for forgotten password recovery.

        Args:
            site_id: The ID of the site this token belongs to
            user_id: The ID of the user requesting password reset

        Returns:
            PasswordResetToken: The created password reset token model
        """
        token_str = self.generate_token()
        created_at = int(time.time())
        expires_at = created_at + self.config.PASSWORD_RESET_EXPIRATION

        reset_token = PasswordResetToken(
            token=token_str,
            site_id=site_id,
            user_id=user_id,
            expires_at=expires_at,
            created_at=created_at,
            used=False
        )

        return db_manager.create_password_reset_token(reset_token)

    def validate_password_reset_token(self, token: str) -> Optional[int]:
        """
        Validate a password reset token and mark it as used.

        Args:
            token: The password reset token string to validate

        Returns:
            Optional[int]: The user_id if token is valid, None if invalid, expired, or already used
        """
        reset_token = db_manager.find_password_reset_token(token)

        if not reset_token:
            return None

        current_time = int(time.time())
        if reset_token.used or reset_token.expires_at < current_time:
            return None

        # Mark token as used
        db_manager.mark_password_reset_token_used(token)

        return reset_token.user_id

    def create_email_change_token(self, site_id: int, user_id: int, new_email: str) -> EmailChangeRequest:
        """
        Create an email change request token for updating user email address.

        Args:
            site_id: The ID of the site this token belongs to
            user_id: The ID of the user requesting email change
            new_email: The new email address to be verified

        Returns:
            EmailChangeRequest: The created email change request model
        """
        token_str = self.generate_token()
        created_at = int(time.time())
        expires_at = created_at + self.config.EMAIL_CHANGE_EXPIRATION

        change_request = EmailChangeRequest(
            token=token_str,
            site_id=site_id,
            user_id=user_id,
            new_email=new_email,
            expires_at=expires_at,
            created_at=created_at
        )

        return db_manager.create_email_change_request(change_request)

    def validate_email_change_token(self, token: str) -> Optional[EmailChangeRequest]:
        """
        Validate an email change token and retrieve the change request details.

        Args:
            token: The email change token string to validate

        Returns:
            Optional[EmailChangeRequest]: The email change request if valid, None if invalid or expired
        """
        change_request = db_manager.find_email_change_request(token)

        if not change_request:
            return None

        current_time = int(time.time())
        if change_request.expires_at < current_time:
            return None

        # Delete token after successful validation (one-time use)
        db_manager.delete_email_change_request(token)

        return change_request

    def cleanup_expired_tokens(self) -> None:
        """
        Remove all expired tokens from the database.

        Should be run periodically to clean up expired tokens.
        """
        current_time = int(time.time())

        db_manager.delete_expired_auth_tokens(current_time)
        db_manager.delete_expired_refresh_tokens(current_time)
        db_manager.delete_expired_email_verification_tokens(current_time)
        db_manager.delete_expired_password_reset_tokens(current_time)
        db_manager.delete_expired_email_change_requests(current_time)


# Global token service instance
token_service = TokenService()
