import logging
import psycopg2
from psycopg2 import pool
from psycopg2.extensions import connection
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
from typing import Generator, List, Optional
from byteforge_aegis_models import WebhookEvent, UserRole
from models.user import User
from config import get_config
from utils.uuid7 import generate_uuid7


logger = logging.getLogger(__name__)


# Errors that mean "this socket is unusable — discard, do not recycle."
_DEAD_CONN_ERRORS = (psycopg2.OperationalError, psycopg2.InterfaceError)

# How many times to retry checkout when pre-ping fails. Enough to drain
# a pool full of corpses after Postgres restarts; not so high that we
# spin forever if Postgres is genuinely down.
MAX_HEALTH_RETRIES = 3


class DatabaseManager:
    """Manages PostgreSQL database connections with connection pooling.

    Survives upstream Postgres restarts: a naive pool hands out dead
    sockets after a restart and wedges every worker until redeploy. This
    pre-pings each checkout, retries past corpses, and discards (rather
    than recycles) connections whose socket is actually dead.
    """

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
            self.connection_pool = psycopg2.pool.ThreadedConnectionPool(
                self.min_conn,
                self.max_conn,
                host=self.config.DB_HOST,
                port=self.config.DB_PORT,
                database=self.config.DB_NAME,
                user=self.config.DB_USER,
                password=self.config.DB_PASSWORD,
                connect_timeout=5,
                # TCP keepalives — let the OS detect a silently dropped
                # conn within ~80s instead of "until next reboot."
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=5,
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

    @staticmethod
    def _check_alive(conn: connection) -> None:
        """Cheap `SELECT 1` pre-ping. Raises on dead conn."""
        cur = conn.cursor()
        try:
            cur.execute("SELECT 1")
            cur.fetchone()
        finally:
            try:
                cur.close()
            except Exception:
                logger.exception("Pre-ping cursor close failed; ignoring")
        # Reset transaction state so the caller gets a clean slate.
        conn.rollback()

    @staticmethod
    def _is_dead_conn_error(conn: Optional[connection], exc: BaseException) -> bool:
        """
        Decide whether `exc` indicates the underlying socket is dead.

        InterfaceError → always dead (operations on a closed conn/cursor).
        OperationalError → ambiguous: it's the parent class of
            SerializationFailure, DeadlockDetected, QueryCanceled, and
            LockNotAvailable, all of which fire on perfectly healthy
            conns. Use `conn.closed` as the discriminator: psycopg2 sets
            it to non-zero only when the socket is actually broken.
        Anything else → not a dead-conn signal.
        """
        if isinstance(exc, psycopg2.InterfaceError):
            return True
        if isinstance(exc, psycopg2.OperationalError):
            return conn is None or getattr(conn, "closed", 0) != 0
        return False

    def _safe_putback(self, conn: Optional[connection], close: bool) -> None:
        """Best-effort return-to-pool. Falls back to conn.close() on pool error."""
        if conn is None:
            return
        try:
            self.connection_pool.putconn(conn, close=close)
        except Exception:
            try:
                conn.close()
            except Exception:
                logger.exception("Pool putback fallback conn.close failed; dropping conn reference")

    @contextmanager
    def get_connection(self) -> Generator:
        """
        Context manager yielding a healthy pooled connection.

        Pre-pings before yielding. If the pool hands out a dead conn,
        discards it (so the pool refills with a fresh socket) and retries
        up to MAX_HEALTH_RETRIES times. Mid-flight, truly-dead sockets
        (rollback fails or `conn.closed != 0`) are discarded with
        close=True; app-level errors on healthy conns (e.g.
        SerializationFailure) recycle into the pool to avoid TCP+auth churn.
        """
        # Lazy initialization: try to connect if not already connected
        if not self._pool_initialized:
            if not self._try_initialize_pool():
                raise Exception("Database connection not available. Please check DB_HOST, DB_PORT, DB_NAME, DB_USER, and DB_PASSWORD.")

        if not self.connection_pool:
            raise Exception("Connection pool not initialized")

        last_err = None
        for attempt in range(MAX_HEALTH_RETRIES):
            conn = None
            try:
                conn = self.connection_pool.getconn()
                self._check_alive(conn)
            except BaseException as e:
                # Checkout/probe failed. Always discard with close=True —
                # we don't trust a conn that failed pre-ping. Retry only
                # if the error means "dead socket"; otherwise propagate.
                dead = self._is_dead_conn_error(conn, e)
                self._safe_putback(conn, close=True)
                if dead:
                    last_err = e
                    print(f"DB checkout pre-ping failed (attempt {attempt + 1}/{MAX_HEALTH_RETRIES}): {e}")
                    continue
                raise

            # Healthy conn — yield it. Mid-flight handling decides
            # discard-vs-recycle without trusting the exception class
            # alone (SerializationFailure / DeadlockDetected all inherit
            # from OperationalError but the conn is alive).
            try:
                yield conn
            except BaseException:
                conn_dead = False
                try:
                    conn.rollback()
                except _DEAD_CONN_ERRORS:
                    conn_dead = True
                except Exception:
                    # Rollback failed for an unexpected reason. The socket
                    # may still look open, but transaction state is unknown.
                    # Narrowed to Exception (not BaseException) so a signal
                    # raised mid-rollback (KeyboardInterrupt / SystemExit)
                    # propagates instead of being silently swallowed by the
                    # outer bare `raise`, which only re-raises the caller's
                    # original exception.
                    logger.exception(
                        "Unexpected DB rollback failure during mid-flight "
                        "cleanup; discarding conn"
                    )
                    conn_dead = True
                if not conn_dead:
                    conn_dead = getattr(conn, "closed", 0) != 0
                self._safe_putback(conn, close=conn_dead)
                raise
            else:
                # Symmetry with the exception branch: even on a clean exit
                # the socket may have been closed underneath us, so discard
                # rather than recycle a dead conn back into the pool.
                conn_dead = getattr(conn, "closed", 0) != 0
                self._safe_putback(conn, close=conn_dead)
            return

        # Retries exhausted.
        raise RuntimeError(
            f"Could not acquire a healthy DB connection after "
            f"{MAX_HEALTH_RETRIES} attempts"
        ) from last_err

    @contextmanager
    def get_cursor(self, commit: bool = False) -> Generator:
        """Context manager for getting a cursor with automatic commit/rollback.

        Cleanup (rollback, cursor.close) suppresses cleanup errors so the
        caller sees the original exception, not a follow-on.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            try:
                yield cursor
                if commit:
                    conn.commit()
            except BaseException:
                try:
                    conn.rollback()
                except Exception:
                    logger.exception("Rollback during get_cursor cleanup failed; ignoring")
                raise
            finally:
                try:
                    cursor.close()
                except Exception:
                    logger.exception("Cursor close during get_cursor cleanup failed; ignoring")

    # Site operations
    def create_site(self, site: 'Site') -> 'Site':
        """
        Create a new site in the database.

        Args:
            site: Site model; a missing/empty uuid is minted here (UUIDv7)

        Returns:
            Site: The created site
        """
        if not site.uuid:
            site.uuid = generate_uuid7()
        with self.get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO sites (uuid, name, domain, frontend_url, verification_redirect_url, email_from, email_from_name, created_at, updated_at, allow_self_registration, webhook_url, webhook_secret, tenant_api_key, mailgun_domain, mailgun_api_key)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (site.uuid, site.name, site.domain, site.frontend_url, site.verification_redirect_url, site.email_from, site.email_from_name, site.created_at, site.updated_at, site.allow_self_registration, site.webhook_url, site.webhook_secret, site.tenant_api_key, site.mailgun_domain, site.mailgun_api_key)
            )
        return site

    def find_site_by_uuid(self, site_uuid: str) -> Optional['Site']:
        """
        Find a site by its UUID.

        Args:
            site_uuid: The site's UUID

        Returns:
            Optional[Site]: The site if found, None otherwise
        """
        from byteforge_aegis_models import Site

        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT uuid, name, domain, frontend_url, verification_redirect_url, email_from, email_from_name, created_at, updated_at, allow_self_registration, webhook_url, webhook_secret, tenant_api_key, mailgun_domain, mailgun_api_key FROM sites WHERE uuid = %s",
                (site_uuid,)
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
        from byteforge_aegis_models import Site

        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT uuid, name, domain, frontend_url, verification_redirect_url, email_from, email_from_name, created_at, updated_at, allow_self_registration, webhook_url, webhook_secret, tenant_api_key, mailgun_domain, mailgun_api_key FROM sites WHERE domain = %s",
                (domain,)
            )
            row = cursor.fetchone()
            return Site.from_dict(row) if row else None

    def update_site(self, site: 'Site') -> 'Site':
        """
        Update an existing site in the database.

        Args:
            site: Site model with all fields including uuid

        Returns:
            Site: The updated site model
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                UPDATE sites
                SET name = %s, domain = %s, frontend_url = %s, verification_redirect_url = %s, email_from = %s, email_from_name = %s, updated_at = %s, allow_self_registration = %s, webhook_url = %s, webhook_secret = %s, tenant_api_key = %s, mailgun_domain = %s, mailgun_api_key = %s
                WHERE uuid = %s
                """,
                (site.name, site.domain, site.frontend_url, site.verification_redirect_url, site.email_from, site.email_from_name, site.updated_at, site.allow_self_registration, site.webhook_url, site.webhook_secret, site.tenant_api_key, site.mailgun_domain, site.mailgun_api_key, site.uuid)
            )
        return site

    def delete_site(self, site_uuid: str) -> bool:
        """
        Delete a site and ALL of its data from the database.

        Every dependent table (users, auth_tokens, refresh_tokens,
        email_verification_tokens, password_reset_tokens, email_change_requests,
        webhook_events) has an ON DELETE CASCADE foreign key to sites, so a
        single DELETE removes the entire tenant. This is irreversible.

        Args:
            site_uuid: The UUID of the site to delete

        Returns:
            bool: True if a site was deleted, False if the site was not found
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM sites WHERE uuid = %s", (site_uuid,))
            return cursor.rowcount > 0

    # User operations
    def create_user(self, user: 'User') -> 'User':
        """
        Create a new user in the database.

        Args:
            user: User model with site_uuid, email, password_hash, is_verified,
                role, created_at, updated_at; a missing/empty uuid is minted
                here (UUIDv7)

        Returns:
            User: The created user
        """
        if not user.uuid:
            user.uuid = generate_uuid7()
        with self.get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO users (uuid, site_uuid, email, password_hash, is_verified, role, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (user.uuid, user.site_uuid, user.email, user.password_hash, user.is_verified, user.role.value, user.created_at, user.updated_at)
            )
        return user

    def find_user_by_uuid(self, user_uuid: str) -> Optional['User']:
        """
        Find a user by their UUID.

        Args:
            user_uuid: The user's UUID

        Returns:
            Optional[User]: The user if found, None otherwise
        """
        from models.user import User

        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT uuid, site_uuid, email, password_hash, is_verified, role, created_at, updated_at FROM users WHERE uuid = %s",
                (user_uuid,)
            )
            row = cursor.fetchone()
            return User.from_dict(row) if row else None

    def find_user_by_email(self, site_uuid: str, email: str) -> Optional['User']:
        """
        Find a user by their email address within a specific site.

        Args:
            site_uuid: The site UUID to search within
            email: The user's email address

        Returns:
            Optional[User]: The user if found, None otherwise
        """
        from models.user import User

        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT uuid, site_uuid, email, password_hash, is_verified, role, created_at, updated_at FROM users WHERE site_uuid = %s AND email = %s",
                (site_uuid, email)
            )
            row = cursor.fetchone()
            return User.from_dict(row) if row else None

    def list_users_by_site(self, site_uuid: str) -> List[User]:
        """
        List all users for a specific site.

        Args:
            site_uuid: The UUID of the site

        Returns:
            List of User models
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT uuid, site_uuid, email, password_hash, is_verified, role, created_at, updated_at FROM users WHERE site_uuid = %s ORDER BY created_at, uuid",
                (site_uuid,)
            )
            rows = cursor.fetchall()
            return [User.from_dict(row) for row in rows]

    def count_site_admins(self, site_uuid: str) -> int:
        """
        Count admin-role users on a site.

        Used to prevent deleting the last admin of a site, which would orphan
        that site's admin access.

        Args:
            site_uuid: The UUID of the site

        Returns:
            Number of users with the admin role on the site
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) AS count FROM users WHERE site_uuid = %s AND role = %s",
                (site_uuid, UserRole.ADMIN.value)
            )
            row = cursor.fetchone()
            return row['count'] if row else 0

    def update_user(self, user: 'User') -> 'User':
        """
        Update an existing user in the database.

        Args:
            user: User model with all fields including uuid

        Returns:
            User: The updated user model
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                UPDATE users
                SET email = %s, password_hash = %s, is_verified = %s, role = %s, updated_at = %s
                WHERE uuid = %s
                """,
                (user.email, user.password_hash, user.is_verified, user.role.value, user.updated_at, user.uuid)
            )
        return user

    def delete_user(self, user_uuid: str) -> bool:
        """
        Delete a user and all related data from the database.

        Args:
            user_uuid: The UUID of the user to delete

        Returns:
            bool: True if user was deleted, False if user not found
        """
        with self.get_cursor(commit=True) as cursor:
            # Delete related tokens first (foreign key constraints)
            cursor.execute("DELETE FROM auth_tokens WHERE user_uuid = %s", (user_uuid,))
            cursor.execute("DELETE FROM refresh_tokens WHERE user_uuid = %s", (user_uuid,))
            cursor.execute("DELETE FROM email_verification_tokens WHERE user_uuid = %s", (user_uuid,))
            cursor.execute("DELETE FROM password_reset_tokens WHERE user_uuid = %s", (user_uuid,))
            cursor.execute("DELETE FROM email_change_requests WHERE user_uuid = %s", (user_uuid,))

            # Delete the user
            cursor.execute("DELETE FROM users WHERE uuid = %s", (user_uuid,))
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
                INSERT INTO auth_tokens (site_uuid, user_uuid, token, expires_at, created_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (auth_token.site_uuid, auth_token.user_uuid, auth_token.token, auth_token.expires_at, auth_token.created_at)
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
        from byteforge_aegis_models import AuthToken

        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT site_uuid, user_uuid, token, expires_at, created_at FROM auth_tokens WHERE token = %s",
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

    def delete_auth_tokens_by_user(self, user_uuid: str) -> int:
        """
        Delete all auth tokens for a user.

        Args:
            user_uuid: The user's UUID

        Returns:
            int: Number of tokens deleted
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM auth_tokens WHERE user_uuid = %s", (user_uuid,))
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
                INSERT INTO refresh_tokens (site_uuid, user_uuid, token, family_id, expires_at, created_at, used_at, revoked)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (refresh_token.site_uuid, refresh_token.user_uuid, refresh_token.token,
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
        from byteforge_aegis_models import RefreshToken

        with self.get_cursor() as cursor:
            cursor.execute(
                """SELECT site_uuid, user_uuid, token, family_id, expires_at, created_at, used_at, revoked
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
        from byteforge_aegis_models import RefreshToken

        with self.get_cursor() as cursor:
            cursor.execute(
                """SELECT site_uuid, user_uuid, token, family_id, expires_at, created_at, used_at, revoked
                   FROM refresh_tokens WHERE family_id = %s ORDER BY created_at DESC LIMIT 1""",
                (family_id,)
            )
            row = cursor.fetchone()
            return RefreshToken.from_dict(row) if row else None

    def delete_refresh_tokens_by_user(self, user_uuid: str) -> int:
        """
        Delete all refresh tokens for a user.

        Args:
            user_uuid: The user's UUID

        Returns:
            int: Number of tokens deleted
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM refresh_tokens WHERE user_uuid = %s", (user_uuid,))
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
                INSERT INTO email_verification_tokens (site_uuid, user_uuid, token, expires_at, created_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (token.site_uuid, token.user_uuid, token.token, token.expires_at, token.created_at)
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
                "SELECT site_uuid, user_uuid, token, expires_at, created_at FROM email_verification_tokens WHERE token = %s",
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
                INSERT INTO password_reset_tokens (site_uuid, user_uuid, token, expires_at, created_at, used)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (token.site_uuid, token.user_uuid, token.token, token.expires_at, token.created_at, token.used)
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
                "SELECT site_uuid, user_uuid, token, expires_at, created_at, used FROM password_reset_tokens WHERE token = %s",
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
                INSERT INTO email_change_requests (site_uuid, user_uuid, new_email, token, expires_at, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (request.site_uuid, request.user_uuid, request.new_email, request.token, request.expires_at, request.created_at)
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
                "SELECT site_uuid, user_uuid, new_email, token, expires_at, created_at FROM email_change_requests WHERE token = %s",
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
            WebhookEvent: The created event
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO webhook_events (uuid, site_uuid, event_type, payload, response_status, response_body, success, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (event.uuid, event.site_uuid, event.event_type, event.payload, event.response_status, event.response_body, event.success, event.created_at)
            )
        return event

    def list_webhook_events_by_site(self, site_uuid: str) -> List[WebhookEvent]:
        """
        List all webhook events for a specific site.

        Args:
            site_uuid: The site UUID

        Returns:
            List of WebhookEvent models ordered by most recent first
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT uuid, site_uuid, event_type, payload, response_status, response_body, success, created_at FROM webhook_events WHERE site_uuid = %s ORDER BY created_at DESC",
                (site_uuid,)
            )
            rows = cursor.fetchall()
            return [WebhookEvent.from_dict(row) for row in rows]


# Global database manager instance
db_manager = DatabaseManager()
