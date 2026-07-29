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
from models.token_cleanup_result import TokenCleanupResult


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

    def create_auth_token(self, site_uuid: str, user_uuid: str) -> AuthToken:
        """
        Create a new authentication token for user session management.

        Args:
            site_uuid: The UUID of the site this token belongs to
            user_uuid: The UUID of the user to create the token for

        Returns:
            AuthToken: The created auth token model
        """
        token_str = self.generate_token()
        created_at = int(time.time())
        expires_at = created_at + self.config.AUTH_TOKEN_EXPIRATION

        auth_token = AuthToken(
            token=token_str,
            user_uuid=user_uuid,
            expires_at=expires_at,
            site_uuid=site_uuid,
            created_at=created_at
        )

        return db_manager.create_auth_token(auth_token)

    def validate_auth_token(self, token: str) -> Optional[str]:
        """
        Validate an authentication token and check if it's still valid.

        Args:
            token: The auth token string to validate

        Returns:
            Optional[str]: The user_uuid if token is valid, None if invalid or expired
        """
        auth_token = db_manager.find_auth_token_by_token(token)

        if not auth_token:
            return None

        current_time = int(time.time())
        if auth_token.expires_at < current_time:
            return None

        return auth_token.user_uuid

    def invalidate_auth_token(self, token: str) -> bool:
        """
        Invalidate (delete) a specific authentication token.

        Args:
            token: The auth token string to invalidate

        Returns:
            bool: True if token was found and deleted, False otherwise
        """
        return db_manager.delete_auth_token(token)

    def revoke_refresh_family_for_user(self, refresh_token: str, user_uuid: str) -> bool:
        """
        Revoke the family a refresh token belongs to, for one specific user.

        The ownership check is not a formality. Without it, any authenticated
        caller could end an arbitrary user's session by presenting that
        user's refresh token — turning logout into a denial-of-service
        primitive against other accounts, and one reachable across tenants
        since refresh tokens are looked up by value alone.

        Args:
            refresh_token: The refresh token whose family should be revoked
            user_uuid: The authenticated caller, who must own that token

        Returns:
            bool: True if a family was revoked, False if the token is
                  unknown or belongs to somebody else.
        """
        stored = db_manager.find_refresh_token_by_token(refresh_token)

        if stored is None or stored.user_uuid != user_uuid:
            return False

        db_manager.revoke_refresh_token_family(stored.family_id)
        return True

    def invalidate_user_tokens(self, user_uuid: str) -> None:
        """
        Invalidate all authentication tokens for a specific user.

        Args:
            user_uuid: The UUID of the user whose tokens should be invalidated
        """
        db_manager.delete_auth_tokens_by_user(user_uuid)

    def create_refresh_token(self, site_uuid: str, user_uuid: str, family_id: Optional[str] = None) -> RefreshToken:
        """
        Create a new refresh token for long-lived session management.

        Args:
            site_uuid: The UUID of the site this token belongs to
            user_uuid: The UUID of the user to create the token for
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
            site_uuid=site_uuid,
            user_uuid=user_uuid,
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
            Optional[RefreshTokenResult]: Result containing user_uuid, site_uuid, and new token if valid

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

        if not self.config.REFRESH_TOKEN_ROTATION:
            return RefreshTokenResult(
                user_uuid=refresh_token.user_uuid,
                site_uuid=refresh_token.site_uuid,
                new_refresh_token=None
            )

        # The claim decides the outcome, not the used_at we read a moment
        # ago. Testing the value we read and then writing was a race: two
        # concurrent requests both saw it unused and both rotated, forking
        # the family so reuse detection never fired again.
        if refresh_token.used_at is None and db_manager.claim_refresh_token(token, current_time):
            new_token = self.create_refresh_token(
                refresh_token.site_uuid,
                refresh_token.user_uuid,
                refresh_token.family_id
            )
            return RefreshTokenResult(
                user_uuid=refresh_token.user_uuid,
                site_uuid=refresh_token.site_uuid,
                new_refresh_token=new_token
            )

        # Either it was already used when we read it, or a concurrent
        # request claimed it first. Re-read so the grace window is measured
        # against the authoritative used_at rather than a stale copy.
        return self._handle_used_refresh_token(token, current_time)

    def _handle_used_refresh_token(self, token: str, current_time: int) -> Optional[RefreshTokenResult]:
        """
        Resolve a refresh token that was already consumed.

        Inside the grace window this converges concurrent refreshes onto the
        family's current token instead of failing one of them. Past it, the
        only innocent explanation is gone and the presentation is treated as
        theft.

        Raises:
            ValueError: If reuse is detected outside the grace window.
        """
        refresh_token = db_manager.find_refresh_token_by_token(token)
        if not refresh_token or refresh_token.revoked:
            return None

        grace_period_end = (refresh_token.used_at or current_time) + self.config.REFRESH_TOKEN_GRACE_PERIOD

        if current_time > grace_period_end:
            db_manager.revoke_refresh_token_family(refresh_token.family_id)
            # The thief's auth token is the credential they are actually
            # holding; revoking only the refresh family would leave it
            # working for up to AUTH_TOKEN_EXPIRATION after we have already
            # concluded the session is compromised.
            db_manager.delete_auth_tokens_by_user(refresh_token.user_uuid)
            raise ValueError("Refresh token reuse detected - all sessions revoked")

        latest = db_manager.find_latest_refresh_token_in_family(refresh_token.family_id)
        if latest and latest.token != refresh_token.token:
            return RefreshTokenResult(
                user_uuid=latest.user_uuid,
                site_uuid=latest.site_uuid,
                new_refresh_token=latest
            )

        return RefreshTokenResult(
            user_uuid=refresh_token.user_uuid,
            site_uuid=refresh_token.site_uuid,
            new_refresh_token=None
        )

    def invalidate_user_refresh_tokens(self, user_uuid: str) -> None:
        """
        Invalidate all refresh tokens for a specific user.

        Args:
            user_uuid: The UUID of the user whose refresh tokens should be invalidated
        """
        db_manager.delete_refresh_tokens_by_user(user_uuid)

    def create_email_verification_token(self, site_uuid: str, user_uuid: str) -> EmailVerificationToken:
        """
        Create an email verification token for confirming user email ownership.

        Args:
            site_uuid: The UUID of the site this token belongs to
            user_uuid: The UUID of the user to create the verification token for

        Returns:
            EmailVerificationToken: The created verification token model
        """
        token_str = self.generate_token()
        created_at = int(time.time())
        expires_at = created_at + self.config.EMAIL_VERIFICATION_EXPIRATION

        email_token = EmailVerificationToken(
            token=token_str,
            site_uuid=site_uuid,
            user_uuid=user_uuid,
            expires_at=expires_at,
            created_at=created_at
        )

        return db_manager.create_email_verification_token(email_token)

    def check_email_verification_token(self, token: str) -> Optional[str]:
        """
        Check an email verification token without consuming it.

        Non-destructive check - the token remains valid for later use.
        Use this to check if a password is required before verification.

        Args:
            token: The email verification token string to check

        Returns:
            Optional[str]: The user_uuid if token is valid, None if invalid or expired
        """
        email_token = db_manager.find_email_verification_token(token)

        if not email_token:
            return None

        current_time = int(time.time())
        if email_token.expires_at < current_time:
            return None

        return email_token.user_uuid

    def validate_email_verification_token(self, token: str) -> Optional[str]:
        """
        Validate an email verification token and mark it as used by deleting it.

        Args:
            token: The email verification token string to validate

        Returns:
            Optional[str]: The user_uuid if token is valid, None if invalid or expired
        """
        email_token = db_manager.find_email_verification_token(token)

        if not email_token:
            return None

        current_time = int(time.time())
        if email_token.expires_at < current_time:
            return None

        # Delete token after successful validation (one-time use)
        db_manager.delete_email_verification_token(token)

        return email_token.user_uuid

    def create_password_reset_token(self, site_uuid: str, user_uuid: str) -> PasswordResetToken:
        """
        Create a password reset token for forgotten password recovery.

        Args:
            site_uuid: The UUID of the site this token belongs to
            user_uuid: The UUID of the user requesting password reset

        Returns:
            PasswordResetToken: The created password reset token model
        """
        token_str = self.generate_token()
        created_at = int(time.time())
        expires_at = created_at + self.config.PASSWORD_RESET_EXPIRATION

        reset_token = PasswordResetToken(
            token=token_str,
            site_uuid=site_uuid,
            user_uuid=user_uuid,
            expires_at=expires_at,
            created_at=created_at,
            used=False
        )

        return db_manager.create_password_reset_token(reset_token)

    def validate_password_reset_token(self, token: str) -> Optional[str]:
        """
        Validate a password reset token and mark it as used.

        Args:
            token: The password reset token string to validate

        Returns:
            Optional[str]: The user_uuid if token is valid, None if invalid, expired, or already used
        """
        reset_token = db_manager.find_password_reset_token(token)

        if not reset_token:
            return None

        current_time = int(time.time())
        if reset_token.used or reset_token.expires_at < current_time:
            return None

        # Mark token as used
        db_manager.mark_password_reset_token_used(token)

        return reset_token.user_uuid

    def create_email_change_token(self, site_uuid: str, user_uuid: str, new_email: str) -> EmailChangeRequest:
        """
        Create an email change request token for updating user email address.

        Args:
            site_uuid: The UUID of the site this token belongs to
            user_uuid: The UUID of the user requesting email change
            new_email: The new email address to be verified

        Returns:
            EmailChangeRequest: The created email change request model
        """
        token_str = self.generate_token()
        created_at = int(time.time())
        expires_at = created_at + self.config.EMAIL_CHANGE_EXPIRATION

        change_request = EmailChangeRequest(
            token=token_str,
            site_uuid=site_uuid,
            user_uuid=user_uuid,
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

    def cleanup_expired_tokens(self) -> TokenCleanupResult:
        """
        Remove all expired tokens from the database.

        Driven by POST /api/admin/cleanup-expired-tokens, which a scheduler
        calls periodically. Nothing expires the rows on its own: expiry is
        enforced at validation time by comparing expires_at, so a token that
        is never presented again is never noticed and the table grows without
        bound. Both installs had accumulated thousands of dead rows before
        this had any caller at all.

        Each table is deleted in its own transaction, so a failure part-way
        keeps the tables it already drained rather than rolling back the lot.
        Re-running is always safe.

        Returns:
            TokenCleanupResult: Rows removed, per table.
        """
        current_time = int(time.time())

        return TokenCleanupResult(
            auth_tokens=db_manager.delete_expired_auth_tokens(current_time),
            refresh_tokens=db_manager.delete_expired_refresh_tokens(current_time),
            email_verification_tokens=db_manager.delete_expired_email_verification_tokens(current_time),
            password_reset_tokens=db_manager.delete_expired_password_reset_tokens(current_time),
            email_change_requests=db_manager.delete_expired_email_change_requests(current_time),
        )


# Global token service instance
token_service = TokenService()
