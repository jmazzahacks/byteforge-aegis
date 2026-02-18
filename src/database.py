import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
from typing import Generator, List, Optional
from models.user import User
from models.webhook_event import WebhookEvent
from config import get_config


class DatabaseManager:
    """Manages PostgreSQL database connections with connection pooling"""

    def __init__(self, min_conn: int = 1, max_conn: int = 10):
        self.config = get_config()
        self.connection_pool = None
        self.min_conn = min_conn
        self.max_conn = max_conn
        self._pool_initialized = False

        # Try to initialize, but don't fail if database isn't available yet
        self._try_initialize_pool()

    def _try_initialize_pool(self) -> bool:
        """Try to initialize the connection pool. Returns True if successful."""
        if self._pool_initialized:
            return True

        try:
            self.connection_pool = psycopg2.pool.SimpleConnectionPool(
                self.min_conn,
                self.max_conn,
                host=self.config.DB_HOST,
                port=self.config.DB_PORT,
                database=self.config.DB_NAME,
                user=self.config.DB_USER,
                password=self.config.DB_PASSWORD
            )
            self._pool_initialized = True
            print("Database connection pool initialized successfully")
            return True
        except Exception as e:
            print(f"Warning: Database not available yet: {e}")
            self.connection_pool = None
            self._pool_initialized = False
            return False

    def close_pool(self) -> None:
        """Close all connections in the pool"""
        if self.connection_pool:
            self.connection_pool.closeall()
            print("Database connection pool closed")

    def __del__(self):
        """Cleanup connection pool when instance is destroyed"""
        self.close_pool()

    @contextmanager
    def get_connection(self) -> Generator:
        """Context manager for getting a database connection from the pool"""
        # Lazy initialization: try to connect if not already connected
        if not self._pool_initialized:
            if not self._try_initialize_pool():
                raise Exception("Database connection not available. Please check DB_HOST, DB_PORT, DB_NAME, DB_USER, and DB_PASSWORD.")

        if not self.connection_pool:
            raise Exception("Connection pool not initialized")

        conn = self.connection_pool.getconn()
        try:
            yield conn
        finally:
            self.connection_pool.putconn(conn)

    @contextmanager
    def get_cursor(self, commit: bool = False) -> Generator:
        """Context manager for getting a cursor with automatic commit/rollback"""
        with self.get_connection() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            try:
                yield cursor
                if commit:
                    conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                cursor.close()

    # Site operations
    def create_site(self, site: 'Site') -> 'Site':
        """
        Create a new site in the database.

        Args:
            site: Site model with name, domain, frontend_url, verification_redirect_url, email_from, email_from_name, created_at, updated_at, allow_self_registration

        Returns:
            Site: The created site with auto-generated id
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO sites (name, domain, frontend_url, verification_redirect_url, email_from, email_from_name, created_at, updated_at, allow_self_registration, webhook_url, webhook_secret)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (site.name, site.domain, site.frontend_url, site.verification_redirect_url, site.email_from, site.email_from_name, site.created_at, site.updated_at, site.allow_self_registration, site.webhook_url, site.webhook_secret)
            )
            site.id = cursor.fetchone()['id']
        return site

    def find_site_by_id(self, site_id: int) -> Optional['Site']:
        """
        Find a site by its ID.

        Args:
            site_id: The site's ID

        Returns:
            Optional[Site]: The site if found, None otherwise
        """
        from models.site import Site

        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT id, name, domain, frontend_url, verification_redirect_url, email_from, email_from_name, created_at, updated_at, allow_self_registration, webhook_url, webhook_secret FROM sites WHERE id = %s",
                (site_id,)
            )
            row = cursor.fetchone()
            return Site.from_dict(row) if row else None

    def find_site_by_domain(self, domain: str) -> Optional['Site']:
        """
        Find a site by its domain.

        Args:
            domain: The site's domain

        Returns:
            Optional[Site]: The site if found, None otherwise
        """
        from models.site import Site

        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT id, name, domain, frontend_url, verification_redirect_url, email_from, email_from_name, created_at, updated_at, allow_self_registration, webhook_url, webhook_secret FROM sites WHERE domain = %s",
                (domain,)
            )
            row = cursor.fetchone()
            return Site.from_dict(row) if row else None

    def update_site(self, site: 'Site') -> 'Site':
        """
        Update an existing site in the database.

        Args:
            site: Site model with all fields including id

        Returns:
            Site: The updated site model
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                UPDATE sites
                SET name = %s, domain = %s, frontend_url = %s, verification_redirect_url = %s, email_from = %s, email_from_name = %s, updated_at = %s, allow_self_registration = %s, webhook_url = %s, webhook_secret = %s
                WHERE id = %s
                """,
                (site.name, site.domain, site.frontend_url, site.verification_redirect_url, site.email_from, site.email_from_name, site.updated_at, site.allow_self_registration, site.webhook_url, site.webhook_secret, site.id)
            )
        return site

    # User operations
    def create_user(self, user: 'User') -> 'User':
        """
        Create a new user in the database.

        Args:
            user: User model with site_id, email, password_hash, is_verified, role, created_at, updated_at

        Returns:
            User: The created user with auto-generated id
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO users (site_id, email, password_hash, is_verified, role, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (user.site_id, user.email, user.password_hash, user.is_verified, user.role.value, user.created_at, user.updated_at)
            )
            user.id = cursor.fetchone()['id']
        return user

    def find_user_by_id(self, user_id: int) -> Optional['User']:
        """
        Find a user by their ID.

        Args:
            user_id: The user's ID

        Returns:
            Optional[User]: The user if found, None otherwise
        """
        from models.user import User

        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT id, site_id, email, password_hash, is_verified, role, created_at, updated_at FROM users WHERE id = %s",
                (user_id,)
            )
            row = cursor.fetchone()
            return User.from_dict(row) if row else None

    def find_user_by_email(self, site_id: int, email: str) -> Optional['User']:
        """
        Find a user by their email address within a specific site.

        Args:
            site_id: The site ID to search within
            email: The user's email address

        Returns:
            Optional[User]: The user if found, None otherwise
        """
        from models.user import User

        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT id, site_id, email, password_hash, is_verified, role, created_at, updated_at FROM users WHERE site_id = %s AND email = %s",
                (site_id, email)
            )
            row = cursor.fetchone()
            return User.from_dict(row) if row else None

    def list_users_by_site(self, site_id: int) -> List[User]:
        """
        List all users for a specific site.

        Args:
            site_id: The ID of the site

        Returns:
            List of User models
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT id, site_id, email, password_hash, is_verified, role, created_at, updated_at FROM users WHERE site_id = %s ORDER BY id",
                (site_id,)
            )
            rows = cursor.fetchall()
            return [User.from_dict(row) for row in rows]

    def update_user(self, user: 'User') -> 'User':
        """
        Update an existing user in the database.

        Args:
            user: User model with all fields including id

        Returns:
            User: The updated user model
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                UPDATE users
                SET email = %s, password_hash = %s, is_verified = %s, role = %s, updated_at = %s
                WHERE id = %s
                """,
                (user.email, user.password_hash, user.is_verified, user.role.value, user.updated_at, user.id)
            )
        return user

    def delete_user(self, user_id: int) -> bool:
        """
        Delete a user and all related data from the database.

        Args:
            user_id: The ID of the user to delete

        Returns:
            bool: True if user was deleted, False if user not found
        """
        with self.get_cursor(commit=True) as cursor:
            # Delete related tokens first (foreign key constraints)
            cursor.execute("DELETE FROM auth_tokens WHERE user_id = %s", (user_id,))
            cursor.execute("DELETE FROM refresh_tokens WHERE user_id = %s", (user_id,))
            cursor.execute("DELETE FROM email_verification_tokens WHERE user_id = %s", (user_id,))
            cursor.execute("DELETE FROM password_reset_tokens WHERE user_id = %s", (user_id,))
            cursor.execute("DELETE FROM email_change_requests WHERE user_id = %s", (user_id,))

            # Delete the user
            cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
            return cursor.rowcount > 0

    # AuthToken operations
    def create_auth_token(self, auth_token: 'AuthToken') -> 'AuthToken':
        """
        Create a new auth token in the database.

        Args:
            auth_token: AuthToken model with all fields

        Returns:
            AuthToken: The created auth token
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO auth_tokens (site_id, user_id, token, expires_at, created_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (auth_token.site_id, auth_token.user_id, auth_token.token, auth_token.expires_at, auth_token.created_at)
            )
        return auth_token

    def find_auth_token_by_token(self, token: str) -> Optional['AuthToken']:
        """
        Find an auth token by its token string.

        Args:
            token: The token string to search for

        Returns:
            Optional[AuthToken]: The auth token if found, None otherwise
        """
        from models.auth_token import AuthToken

        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT site_id, user_id, token, expires_at, created_at FROM auth_tokens WHERE token = %s",
                (token,)
            )
            row = cursor.fetchone()
            return AuthToken.from_dict(row) if row else None

    def delete_auth_token(self, token: str) -> bool:
        """
        Delete an auth token by its token string.

        Args:
            token: The token string to delete

        Returns:
            bool: True if token was deleted, False if not found
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM auth_tokens WHERE token = %s", (token,))
            return cursor.rowcount > 0

    def delete_auth_tokens_by_user(self, user_id: int) -> int:
        """
        Delete all auth tokens for a user.

        Args:
            user_id: The user's ID

        Returns:
            int: Number of tokens deleted
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM auth_tokens WHERE user_id = %s", (user_id,))
            return cursor.rowcount

    def delete_expired_auth_tokens(self, current_time: int) -> int:
        """
        Delete all expired auth tokens.

        Args:
            current_time: Unix timestamp to compare against

        Returns:
            int: Number of tokens deleted
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM auth_tokens WHERE expires_at < %s", (current_time,))
            return cursor.rowcount

    # RefreshToken operations
    def create_refresh_token(self, refresh_token: 'RefreshToken') -> 'RefreshToken':
        """
        Create a new refresh token in the database.

        Args:
            refresh_token: RefreshToken model with all fields

        Returns:
            RefreshToken: The created refresh token
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO refresh_tokens (site_id, user_id, token, family_id, expires_at, created_at, used_at, revoked)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (refresh_token.site_id, refresh_token.user_id, refresh_token.token,
                 refresh_token.family_id, refresh_token.expires_at, refresh_token.created_at,
                 refresh_token.used_at, refresh_token.revoked)
            )
        return refresh_token

    def find_refresh_token_by_token(self, token: str) -> Optional['RefreshToken']:
        """
        Find a refresh token by its token string.

        Args:
            token: The token string to search for

        Returns:
            Optional[RefreshToken]: The refresh token if found, None otherwise
        """
        from models.refresh_token import RefreshToken

        with self.get_cursor() as cursor:
            cursor.execute(
                """SELECT site_id, user_id, token, family_id, expires_at, created_at, used_at, revoked
                   FROM refresh_tokens WHERE token = %s""",
                (token,)
            )
            row = cursor.fetchone()
            return RefreshToken.from_dict(row) if row else None

    def mark_refresh_token_used(self, token: str, used_at: int) -> bool:
        """
        Mark a refresh token as used with timestamp.

        Args:
            token: The token string to mark as used
            used_at: Unix timestamp when the token was used

        Returns:
            bool: True if updated, False if not found
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute(
                "UPDATE refresh_tokens SET used_at = %s WHERE token = %s",
                (used_at, token)
            )
            return cursor.rowcount > 0

    def revoke_refresh_token_family(self, family_id: str) -> int:
        """
        Revoke all tokens in a family (for theft detection).

        Args:
            family_id: The family ID to revoke

        Returns:
            int: Number of tokens revoked
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute(
                "UPDATE refresh_tokens SET revoked = TRUE WHERE family_id = %s",
                (family_id,)
            )
            return cursor.rowcount

    def find_latest_refresh_token_in_family(self, family_id: str) -> Optional['RefreshToken']:
        """
        Find the most recently created refresh token in a family.

        Args:
            family_id: The family ID to search for

        Returns:
            Optional[RefreshToken]: The most recent token in the family, None if none found
        """
        from models.refresh_token import RefreshToken

        with self.get_cursor() as cursor:
            cursor.execute(
                """SELECT site_id, user_id, token, family_id, expires_at, created_at, used_at, revoked
                   FROM refresh_tokens WHERE family_id = %s ORDER BY created_at DESC LIMIT 1""",
                (family_id,)
            )
            row = cursor.fetchone()
            return RefreshToken.from_dict(row) if row else None

    def delete_refresh_tokens_by_user(self, user_id: int) -> int:
        """
        Delete all refresh tokens for a user.

        Args:
            user_id: The user's ID

        Returns:
            int: Number of tokens deleted
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM refresh_tokens WHERE user_id = %s", (user_id,))
            return cursor.rowcount

    def delete_expired_refresh_tokens(self, current_time: int) -> int:
        """
        Delete all expired refresh tokens.

        Args:
            current_time: Unix timestamp to compare against

        Returns:
            int: Number of tokens deleted
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM refresh_tokens WHERE expires_at < %s", (current_time,))
            return cursor.rowcount

    # EmailVerificationToken operations
    def create_email_verification_token(self, token: 'EmailVerificationToken') -> 'EmailVerificationToken':
        """
        Create a new email verification token in the database.

        Args:
            token: EmailVerificationToken model with all fields

        Returns:
            EmailVerificationToken: The created token
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO email_verification_tokens (site_id, user_id, token, expires_at, created_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (token.site_id, token.user_id, token.token, token.expires_at, token.created_at)
            )
        return token

    def find_email_verification_token(self, token: str) -> Optional['EmailVerificationToken']:
        """
        Find an email verification token by its token string.

        Args:
            token: The token string to search for

        Returns:
            Optional[EmailVerificationToken]: The token if found, None otherwise
        """
        from models.email_verification_token import EmailVerificationToken

        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT site_id, user_id, token, expires_at, created_at FROM email_verification_tokens WHERE token = %s",
                (token,)
            )
            row = cursor.fetchone()
            return EmailVerificationToken.from_dict(row) if row else None

    def delete_email_verification_token(self, token: str) -> bool:
        """
        Delete an email verification token.

        Args:
            token: The token string to delete

        Returns:
            bool: True if deleted, False if not found
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM email_verification_tokens WHERE token = %s", (token,))
            return cursor.rowcount > 0

    def delete_expired_email_verification_tokens(self, current_time: int) -> int:
        """
        Delete all expired email verification tokens.

        Args:
            current_time: Unix timestamp to compare against

        Returns:
            int: Number of tokens deleted
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM email_verification_tokens WHERE expires_at < %s", (current_time,))
            return cursor.rowcount

    # PasswordResetToken operations
    def create_password_reset_token(self, token: 'PasswordResetToken') -> 'PasswordResetToken':
        """
        Create a new password reset token in the database.

        Args:
            token: PasswordResetToken model with all fields

        Returns:
            PasswordResetToken: The created token
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO password_reset_tokens (site_id, user_id, token, expires_at, created_at, used)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (token.site_id, token.user_id, token.token, token.expires_at, token.created_at, token.used)
            )
        return token

    def find_password_reset_token(self, token: str) -> Optional['PasswordResetToken']:
        """
        Find a password reset token by its token string.

        Args:
            token: The token string to search for

        Returns:
            Optional[PasswordResetToken]: The token if found, None otherwise
        """
        from models.password_reset_token import PasswordResetToken

        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT site_id, user_id, token, expires_at, created_at, used FROM password_reset_tokens WHERE token = %s",
                (token,)
            )
            row = cursor.fetchone()
            return PasswordResetToken.from_dict(row) if row else None

    def mark_password_reset_token_used(self, token: str) -> bool:
        """
        Mark a password reset token as used.

        Args:
            token: The token string to mark as used

        Returns:
            bool: True if updated, False if not found
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute("UPDATE password_reset_tokens SET used = TRUE WHERE token = %s", (token,))
            return cursor.rowcount > 0

    def delete_expired_password_reset_tokens(self, current_time: int) -> int:
        """
        Delete all expired password reset tokens.

        Args:
            current_time: Unix timestamp to compare against

        Returns:
            int: Number of tokens deleted
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM password_reset_tokens WHERE expires_at < %s", (current_time,))
            return cursor.rowcount

    # EmailChangeRequest operations
    def create_email_change_request(self, request: 'EmailChangeRequest') -> 'EmailChangeRequest':
        """
        Create a new email change request in the database.

        Args:
            request: EmailChangeRequest model with all fields

        Returns:
            EmailChangeRequest: The created request
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO email_change_requests (site_id, user_id, new_email, token, expires_at, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (request.site_id, request.user_id, request.new_email, request.token, request.expires_at, request.created_at)
            )
        return request

    def find_email_change_request(self, token: str) -> Optional['EmailChangeRequest']:
        """
        Find an email change request by its token string.

        Args:
            token: The token string to search for

        Returns:
            Optional[EmailChangeRequest]: The request if found, None otherwise
        """
        from models.email_change_request import EmailChangeRequest

        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT site_id, user_id, new_email, token, expires_at, created_at FROM email_change_requests WHERE token = %s",
                (token,)
            )
            row = cursor.fetchone()
            return EmailChangeRequest.from_dict(row) if row else None

    def delete_email_change_request(self, token: str) -> bool:
        """
        Delete an email change request.

        Args:
            token: The token string to delete

        Returns:
            bool: True if deleted, False if not found
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM email_change_requests WHERE token = %s", (token,))
            return cursor.rowcount > 0

    def delete_expired_email_change_requests(self, current_time: int) -> int:
        """
        Delete all expired email change requests.

        Args:
            current_time: Unix timestamp to compare against

        Returns:
            int: Number of requests deleted
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM email_change_requests WHERE expires_at < %s", (current_time,))
            return cursor.rowcount

    # WebhookEvent operations
    def create_webhook_event(self, event: WebhookEvent) -> WebhookEvent:
        """
        Create a webhook event record in the database.

        Args:
            event: WebhookEvent model with delivery details

        Returns:
            WebhookEvent: The created event with auto-generated id
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO webhook_events (site_id, event_type, payload, response_status, response_body, success, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (event.site_id, event.event_type, event.payload, event.response_status, event.response_body, event.success, event.created_at)
            )
            event.id = cursor.fetchone()['id']
        return event

    def list_webhook_events_by_site(self, site_id: int) -> List[WebhookEvent]:
        """
        List all webhook events for a specific site.

        Args:
            site_id: The site ID

        Returns:
            List of WebhookEvent models ordered by most recent first
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT id, site_id, event_type, payload, response_status, response_body, success, created_at FROM webhook_events WHERE site_id = %s ORDER BY created_at DESC",
                (site_id,)
            )
            rows = cursor.fetchall()
            return [WebhookEvent.from_dict(row) for row in rows]


# Global database manager instance
db_manager = DatabaseManager()
