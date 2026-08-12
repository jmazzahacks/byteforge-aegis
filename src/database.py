import logging
import threading

import psycopg2
from psycopg2 import pool
from psycopg2.extensions import connection
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
from typing import Generator, List, Optional
from byteforge_aegis_models import WebhookEvent, UserRole
from models.user import User
from models.webhook_delivery import WebhookDelivery
from config import get_config
from utils.email_normalize import normalize_email
from utils.token_hash import token_digest
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

    # max_conn is a per-WORKER ceiling, so the real cost is max_conn x
    # gunicorn --workers, against a Postgres shared with other services. It
    # must be >= gunicorn --threads PLUS the webhook delivery workers (each
    # holds at most one connection), which is why they are documented in the
    # Dockerfile: raising threads without raising this exhausts the pool and
    # raising this without checking the shared budget starves everything else.
    def __init__(self, min_conn: int = 3, max_conn: int = 5):
        self.config = get_config()
        self.connection_pool = None
        self.min_conn = min_conn
        self.max_conn = max_conn
        self._pool_initialized = False
        self._init_lock = threading.Lock()

        # Try to initialize, but don't fail if database isn't available yet
        self._try_initialize_pool()

    def _try_initialize_pool(self) -> bool:
        """Try to initialize the connection pool. Returns True if successful.

        Locked and double-checked: this is the lazy path taken when Postgres
        was down at boot, and it is now reachable from several request threads
        at once. Unsynchronized, two threads would each build a pool — one
        orphaned with its sockets leaked — and a thread whose attempt failed
        would null out the pool another thread had just built, leaving the
        winner calling getconn() on None.
        """
        if self._pool_initialized:
            return True

        with self._init_lock:
            return self._initialize_pool_locked()

    def _initialize_pool_locked(self) -> bool:
        """Build the pool. Caller must hold _init_lock."""
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
                # Statement cap for DIRECT Postgres connections. Kept because
                # it is correct and free where it applies — but be aware it is
                # SILENTLY DISCARDED when a transaction-pooling proxy sits in
                # front (pgcat on athena drops it; the app's own pool reads
                # back statement_timeout = 0). Do not treat this line as the
                # guarantee. The bound that actually holds is _bound_statement_time's
                # per-transaction SET LOCAL on the destructive paths, plus a
                # role-level default where the DBA has set one.
                options='-c statement_timeout=30000',
                # TCP keepalives — let the OS detect a silently dropped
                # conn within ~80s instead of "until next reboot."
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=5,
            )
            self._pool_initialized = True
            logger.info("Database connection pool initialized successfully")
            return True
        except Exception as e:
            logger.warning("Database not available yet: %s", e)
            # Only clear what this attempt created. Blindly nulling would
            # discard a pool built by an earlier successful attempt, orphaning
            # every connection checked out from it.
            if not self._pool_initialized:
                self.connection_pool = None
            return False

    def close_pool(self) -> None:
        """Close all connections in the pool"""
        if self.connection_pool:
            self.connection_pool.closeall()
            logger.info("Database connection pool closed")

    def __del__(self):
        """Cleanup connection pool when instance is destroyed"""
        self.close_pool()

    @staticmethod
    def _bound_statement_time(cursor) -> None:
        """Cap how long the current transaction's statements may run.

        Applied to the destructive paths only — the cascade deletes, which are
        the realistic candidates for a long-running statement or for blocking
        on a lock. Everything else is a single-row lookup by UUID.

        This exists because the connect-time statement_timeout does NOT reach
        Postgres: pgcat fronts athena in TRANSACTION pooling mode, where server
        connections are shared between clients, so per-connection startup GUCs
        are silently discarded (verified 2026-07-28 — the app's own pool read
        back statement_timeout = 0). SET LOCAL is transaction-scoped, so it
        survives that.

        The durable fix is a role-level default (ALTER ROLE ... SET
        statement_timeout), requested from athena-admin. This stays regardless:
        it travels with the application, so the protection does not depend on
        remembering to configure whichever database this is pointed at next.
        """
        cursor.execute("SET LOCAL statement_timeout = '30s'")

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
            # Checkout and pre-ping are separate arms because they fail for
            # different reasons and only one of them can be classified.
            try:
                conn = self.connection_pool.getconn()
            except Exception as e:
                # Nothing to hand back: the assignment never ran, so conn is
                # still None and there is no socket to discard. That also
                # means `closed` cannot be consulted here, so this reduces to
                # "an OperationalError means the pool could not reach the
                # database — retry; anything else, such as
                # PoolError('exhausted'), is a condition retrying would only
                # repeat — propagate."
                if self._is_dead_conn_error(None, e):
                    last_err = e
                    logger.warning("DB pool getconn failed (attempt %s/%s): %s",
                                   attempt + 1, MAX_HEALTH_RETRIES, e)
                    continue
                raise

            try:
                self._check_alive(conn)
            except BaseException as e:
                # BaseException, not Exception: by this point the conn is
                # checked OUT of the pool, so whatever unwinds through here
                # must hand it back first. A gevent/eventlet Timeout — or a
                # Ctrl-C — derives from BaseException and fires exactly where
                # a pre-ping on a stale socket blocks, so catching only
                # Exception would strand one pooled slot per occurrence until
                # the pool is exhausted and the worker wedges. Discard first,
                # then decide; the mid-flight handler below is BaseException
                # for the same reason.
                self._safe_putback(conn, close=True)
                if not isinstance(e, Exception):
                    raise

                # `SELECT 1` has no legitimate non-dead failure mode, so the
                # exception class is NOT consulted beyond that — any ordinary
                # exception means retry. Classifying this arm is what broke:
                # psycopg2 can raise the bare DatabaseError parent (not
                # OperationalError) when it detects EOF via PQgetResult()
                # returning NULL before PQstatus flips conn.closed, so
                # _is_dead_conn_error scored a genuinely dead socket as alive
                # and the error escaped as a raw 500 — a broken login for the
                # end user. Retrying a hypothetical live-conn failure costs
                # two wasted pre-pings; misclassifying a dead one costs a
                # request.
                last_err = e
                logger.warning("DB checkout pre-ping failed (attempt %s/%s): %s",
                               attempt + 1, MAX_HEALTH_RETRIES, e)
                continue

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
                INSERT INTO sites (uuid, name, domain, frontend_url, verification_redirect_url, email_from, email_from_name, created_at, updated_at, allow_self_registration, webhook_url, webhook_secret, tenant_api_key, mailgun_domain, mailgun_api_key, deletion_protected)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (site.uuid, site.name, site.domain, site.frontend_url, site.verification_redirect_url, site.email_from, site.email_from_name, site.created_at, site.updated_at, site.allow_self_registration, site.webhook_url, site.webhook_secret, site.tenant_api_key, site.mailgun_domain, site.mailgun_api_key, site.deletion_protected)
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
                "SELECT uuid, name, domain, frontend_url, verification_redirect_url, email_from, email_from_name, created_at, updated_at, allow_self_registration, webhook_url, webhook_secret, tenant_api_key, mailgun_domain, mailgun_api_key, deletion_protected FROM sites WHERE uuid = %s",
                (site_uuid,)
            )
            row = cursor.fetchone()
            return Site.from_dict(row) if row else None

    def list_site_frontend_urls(self) -> List[str]:
        """
        Every site's frontend_url, and nothing else.

        Deliberately narrow: this feeds the CORS allow-list, which needs one
        column. The master-key site listing selects tenant_api_key,
        webhook_secret and mailgun_api_key — no reason to pull every
        tenant's secrets into memory on a cache refresh.

        Returns:
            List[str]: frontend_url for each site, nulls excluded
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT frontend_url FROM sites WHERE frontend_url IS NOT NULL"
            )
            return [row['frontend_url'] for row in cursor.fetchall()]

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
                "SELECT uuid, name, domain, frontend_url, verification_redirect_url, email_from, email_from_name, created_at, updated_at, allow_self_registration, webhook_url, webhook_secret, tenant_api_key, mailgun_domain, mailgun_api_key, deletion_protected FROM sites WHERE domain = %s",
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
                SET name = %s, domain = %s, frontend_url = %s, verification_redirect_url = %s, email_from = %s, email_from_name = %s, updated_at = %s, allow_self_registration = %s, webhook_url = %s, webhook_secret = %s, tenant_api_key = %s, mailgun_domain = %s, mailgun_api_key = %s, deletion_protected = %s
                WHERE uuid = %s
                """,
                (site.name, site.domain, site.frontend_url, site.verification_redirect_url, site.email_from, site.email_from_name, site.updated_at, site.allow_self_registration, site.webhook_url, site.webhook_secret, site.tenant_api_key, site.mailgun_domain, site.mailgun_api_key, site.deletion_protected, site.uuid)
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
            self._bound_statement_time(cursor)
            # Both protection guards live here as well as in the route, so a
            # protection set concurrently with an in-flight delete wins and a
            # future caller reaching this method directly can't cascade away
            # protected users. Mirrors the predicate on delete_user.
            cursor.execute(
                """
                DELETE FROM sites s
                WHERE s.uuid = %s
                  AND s.deletion_protected = FALSE
                  AND NOT EXISTS (
                      SELECT 1 FROM users u
                      WHERE u.site_uuid = s.uuid AND u.deletion_protected = TRUE
                  )
                """,
                (site_uuid,)
            )
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
        # Normalised here rather than at each caller: this is the choke point
        # every write passes through, so a new code path cannot forget.
        user.email = normalize_email(user.email)
        with self.get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO users (uuid, site_uuid, email, password_hash, is_verified, role, created_at, updated_at, deletion_protected)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (user.uuid, user.site_uuid, user.email, user.password_hash, user.is_verified, user.role.value, user.created_at, user.updated_at, user.deletion_protected)
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
                "SELECT uuid, site_uuid, email, password_hash, is_verified, role, created_at, updated_at, deletion_protected FROM users WHERE uuid = %s",
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
                "SELECT uuid, site_uuid, email, password_hash, is_verified, role, created_at, updated_at, deletion_protected FROM users WHERE site_uuid = %s AND email = %s",
                (site_uuid, normalize_email(email))
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
                "SELECT uuid, site_uuid, email, password_hash, is_verified, role, created_at, updated_at, deletion_protected FROM users WHERE site_uuid = %s ORDER BY created_at, uuid",
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

    def count_protected_users(self, site_uuid: str) -> int:
        """
        Count deletion-protected users on a site.

        Used to refuse site deletion, which would otherwise cascade away
        protected users and silently defeat the per-user guard.

        Args:
            site_uuid: The UUID of the site

        Returns:
            Number of users on the site with deletion_protected set
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) AS count FROM users WHERE site_uuid = %s AND deletion_protected = TRUE",
                (site_uuid,)
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
                SET email = %s, password_hash = %s, is_verified = %s, role = %s,
                    deletion_protected = %s, updated_at = %s
                WHERE uuid = %s
                """,
                (normalize_email(user.email), user.password_hash, user.is_verified,
                 user.role.value, user.deletion_protected, user.updated_at, user.uuid)
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
            self._bound_statement_time(cursor)
            # Delete related tokens first (foreign key constraints)
            cursor.execute("DELETE FROM auth_tokens WHERE user_uuid = %s", (user_uuid,))
            cursor.execute("DELETE FROM refresh_tokens WHERE user_uuid = %s", (user_uuid,))
            cursor.execute("DELETE FROM email_verification_tokens WHERE user_uuid = %s", (user_uuid,))
            cursor.execute("DELETE FROM password_reset_tokens WHERE user_uuid = %s", (user_uuid,))
            cursor.execute("DELETE FROM email_change_requests WHERE user_uuid = %s", (user_uuid,))

            # Delete the user. Both protection predicates live here as well as
            # in the route: belt-and-suspenders against protection set
            # concurrently with an in-flight delete, and against any future
            # caller that reaches this method without the route's guards.
            cursor.execute(
                """
                DELETE FROM users u
                USING sites s
                WHERE u.uuid = %s
                  AND u.site_uuid = s.uuid
                  AND u.deletion_protected = FALSE
                  AND s.deletion_protected = FALSE
                """,
                (user_uuid,)
            )
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
                (auth_token.site_uuid, auth_token.user_uuid,
                 token_digest(auth_token.token), auth_token.expires_at,
                 auth_token.created_at)
            )
        # Returned with the PLAINTEXT intact — the caller has to hand it to
        # the client, and after this it exists nowhere else.
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
                "SELECT site_uuid, user_uuid, token, expires_at, created_at "
                "FROM auth_tokens WHERE token = %s",
                (token_digest(token),)
            )
            row = cursor.fetchone()
            if not row:
                return None
            # Hand back the plaintext the caller gave us rather than the
            # stored digest, so callers and tests see the token they asked
            # about. The digest is an at-rest detail, not part of the model.
            row = dict(row)
            row['token'] = token
            return AuthToken.from_dict(row)

    def delete_auth_token(self, token: str) -> bool:
        """
        Delete an auth token by its token string.

        Args:
            token: The token string to delete

        Returns:
            bool: True if token was deleted, False if not found
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM auth_tokens WHERE token = %s",
                           (token_digest(token),))
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
                (refresh_token.site_uuid, refresh_token.user_uuid,
                 token_digest(refresh_token.token),
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
                (token_digest(token),)
            )
            row = cursor.fetchone()
            if not row:
                return None
            # Plaintext restored for the caller — see find_auth_token_by_token.
            row = dict(row)
            row['token'] = token
            return RefreshToken.from_dict(row)

    def claim_refresh_token(self, token: str, used_at: int) -> bool:
        """
        Atomically claim a refresh token, succeeding for exactly one caller.

        The `used_at IS NULL` guard is what makes rotation safe under
        concurrency. Without it the caller had to read the row, test used_at,
        and then write — three separate transactions, since each cursor takes
        its own pooled connection. Two requests presenting the same token
        both saw NULL, both wrote, and both minted a successor in the same
        family. The family forked into two live branches, neither of which
        ever presents an already-used token, so reuse detection could never
        fire again on either. A thief racing the legitimate client got a
        permanent parallel session that revocation would never reach.

        Args:
            token: The token string to claim
            used_at: Unix timestamp of the claim

        Returns:
            bool: True if this caller claimed it, False if it was already
                  claimed (by a concurrent request or an earlier one).
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute(
                "UPDATE refresh_tokens SET used_at = %s "
                "WHERE token = %s AND used_at IS NULL",
                (used_at, token_digest(token))
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

    def delete_password_reset_tokens_by_user(self, user_uuid: str) -> int:
        """
        Delete all password reset tokens for a user.

        Args:
            user_uuid: The user's UUID

        Returns:
            int: Number of tokens deleted
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM password_reset_tokens WHERE user_uuid = %s", (user_uuid,))
            return cursor.rowcount

    def delete_email_verification_tokens_by_user(self, user_uuid: str) -> int:
        """
        Delete all email verification tokens for a user.

        Used when re-issuing an invitation, so the superseded link stops
        working rather than leaving several live tokens for one account.

        Args:
            user_uuid: The user's UUID

        Returns:
            int: Number of tokens deleted
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute(
                "DELETE FROM email_verification_tokens WHERE user_uuid = %s",
                (user_uuid,)
            )
            return cursor.rowcount

    def delete_email_change_requests_by_user(self, user_uuid: str) -> int:
        """
        Delete all pending email change requests for a user.

        Args:
            user_uuid: The user's UUID

        Returns:
            int: Number of requests deleted
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM email_change_requests WHERE user_uuid = %s", (user_uuid,))
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
                INSERT INTO webhook_events (uuid, event_id, site_uuid, event_type, payload, response_status, response_body, success, attempt, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (event.uuid, event.event_id or event.uuid, event.site_uuid, event.event_type, event.payload, event.response_status, event.response_body, event.success, event.attempt, event.created_at)
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
                "SELECT uuid, event_id, site_uuid, event_type, payload, response_status, response_body, success, attempt, created_at FROM webhook_events WHERE site_uuid = %s ORDER BY created_at DESC",
                (site_uuid,)
            )
            rows = cursor.fetchall()
            return [WebhookEvent.from_dict(row) for row in rows]

    # WebhookDelivery operations (the outbox)
    _DELIVERY_COLUMNS = (
        'event_id, site_uuid, event_type, payload, status, attempts, '
        'next_attempt_at, last_status, last_error, created_at, updated_at'
    )

    def create_webhook_delivery(self, delivery: WebhookDelivery) -> WebhookDelivery:
        """
        Record that a webhook is owed, before any attempt is made.

        Args:
            delivery: WebhookDelivery to persist

        Returns:
            WebhookDelivery: The persisted delivery
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute(
                f"""
                INSERT INTO webhook_deliveries ({self._DELIVERY_COLUMNS})
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (delivery.event_id, delivery.site_uuid, delivery.event_type,
                 delivery.payload, delivery.status, delivery.attempts,
                 delivery.next_attempt_at, delivery.last_status,
                 delivery.last_error, delivery.created_at, delivery.updated_at)
            )
        return delivery

    def claim_webhook_deliveries(
        self, now: int, lease_seconds: int, limit: int
    ) -> List[WebhookDelivery]:
        """
        Take ownership of up to `limit` deliveries that are due.

        Claiming is a single UPDATE ... RETURNING rather than a SELECT FOR
        UPDATE held across the attempt. That matters twice over: the HTTP
        POST must not happen inside an open transaction (it would pin a
        pooled connection for up to the request timeout, against a pgcat
        pool in transaction mode), and SKIP LOCKED lets several workers
        claim disjoint rows without blocking on each other.

        The claim pushes next_attempt_at forward by lease_seconds, so the
        lease IS the row's next due time. A worker that dies mid-attempt
        therefore releases its rows by doing nothing at all — there is no
        in-flight state to reap, at the cost of the row being retried no
        sooner than the lease.

        Claiming deliberately does NOT increment attempts; that happens in
        finish_webhook_delivery, when an attempt has actually been made.
        Counting at claim time meant a sweep killed partway through (a
        rotation, a DB blip) burned attempts on rows it never POSTed, and
        six such interruptions would retire an event as undeliverable
        without it having been sent once.

        Args:
            now: Current unix time
            lease_seconds: How long a claim holds a row
            limit: Maximum rows to claim

        Returns:
            The claimed deliveries. `attempts` is the count BEFORE this one.
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute(
                f"""
                UPDATE webhook_deliveries
                SET next_attempt_at = %s,
                    updated_at = %s
                WHERE event_id IN (
                    SELECT event_id FROM webhook_deliveries
                    WHERE status = 'pending' AND next_attempt_at <= %s
                    ORDER BY next_attempt_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                )
                RETURNING {self._DELIVERY_COLUMNS}
                """,
                (now + lease_seconds, now, now, limit)
            )
            return [WebhookDelivery.from_dict(row) for row in cursor.fetchall()]

    def claim_webhook_delivery(
        self, event_id: str, now: int, lease_seconds: int
    ) -> Optional[WebhookDelivery]:
        """
        Claim one specific delivery, for the immediate first attempt.

        Returns None when the row is already claimed or no longer pending,
        which is what keeps the inline attempt and a concurrent sweep from
        both delivering the same event.

        Args:
            event_id: The delivery to claim
            now: Current unix time
            lease_seconds: How long the claim holds the row

        Returns:
            The claimed delivery, or None if it was not claimable.
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute(
                f"""
                UPDATE webhook_deliveries
                SET next_attempt_at = %s,
                    updated_at = %s
                WHERE event_id = (
                    SELECT event_id FROM webhook_deliveries
                    WHERE event_id = %s AND status = 'pending'
                        AND next_attempt_at <= %s
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING {self._DELIVERY_COLUMNS}
                """,
                (now + lease_seconds, now, event_id, now)
            )
            row = cursor.fetchone()
            return WebhookDelivery.from_dict(row) if row else None

    def finish_webhook_delivery(
        self, event_id: str, status: str, now: int,
        next_attempt_at: int, last_status: Optional[int],
        last_error: Optional[str], held_lease: int
    ) -> bool:
        """
        Record the outcome of an attempt, and count it.

        Fenced on the lease this worker holds, not merely on the row still
        being pending. Status alone is not enough: the common lost-lease
        race is between two FAILING attempts, which leaves the row pending,
        so a status-only guard would accept the loser's late write. It
        would then double-count the attempt, overwrite the winner's
        last_status/last_error with stale values, and drag next_attempt_at
        backwards — making a further duplicate delivery more likely, which
        is the opposite of what the guard is for.

        Claiming sets next_attempt_at to the lease, and re-claiming moves
        it, so an unchanged value is proof nobody else has taken the row.

        Args:
            event_id: The delivery that was attempted
            status: 'pending' to retry later, 'delivered', or 'exhausted'
            now: Current unix time
            next_attempt_at: When to retry (ignored unless still pending)
            last_status: HTTP status of this attempt, if there was one
            last_error: Transport error from this attempt, if any
            held_lease: The next_attempt_at value this worker's claim
                returned. The write is refused if the row no longer carries
                it.

        Returns:
            bool: True if this worker still owned the delivery and the
                outcome was recorded; False if it had lost the race.
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                UPDATE webhook_deliveries
                SET status = %s, next_attempt_at = %s, updated_at = %s,
                    last_status = %s, last_error = %s,
                    attempts = attempts + 1
                WHERE event_id = %s AND status = 'pending'
                    AND next_attempt_at = %s
                """,
                (status, next_attempt_at, now, last_status, last_error,
                 event_id, held_lease)
            )
            return cursor.rowcount > 0

    def find_webhook_delivery(self, event_id: str) -> Optional[WebhookDelivery]:
        """
        Look up a single delivery by the event id a tenant would report.

        Args:
            event_id: The event id from the payload

        Returns:
            The delivery, or None if unknown.
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                f'SELECT {self._DELIVERY_COLUMNS} FROM webhook_deliveries WHERE event_id = %s',
                (event_id,)
            )
            row = cursor.fetchone()
            return WebhookDelivery.from_dict(row) if row else None

    def delete_settled_webhook_deliveries(self, before: int, limit: int) -> int:
        """
        Drop old deliveries that nobody is waiting on any more.

        The outbox holds a full JSON payload per webhook ever sent, so
        without this it grows without bound. Only `delivered` rows are
        removed: `exhausted` ones are the record of events a tenant never
        received, which is exactly what someone will want to read later.
        The per-attempt log in webhook_events is untouched.

        ORDER BY plus SKIP LOCKED because sweeps overlap: two runs picking
        the same rows in different orders is a deadlock, and a deadlock in
        the prune would abort a sweep whose deliveries had all succeeded.

        Args:
            before: Delete rows settled before this unix time
            limit: Ceiling on one pass, so a large backlog is spread over
                several runs rather than one long-held lock

        Returns:
            int: Number of rows removed
        """
        with self.get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                DELETE FROM webhook_deliveries
                WHERE event_id IN (
                    SELECT event_id FROM webhook_deliveries
                    WHERE status = 'delivered' AND updated_at < %s
                    ORDER BY event_id
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                )
                """,
                (before, limit)
            )
            return cursor.rowcount

    def count_pending_webhook_deliveries(self, now: int) -> int:
        """
        How many deliveries are due right now. For the cron response.

        Args:
            now: Current unix time

        Returns:
            int: Count of pending, due deliveries
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                """SELECT count(*) AS n FROM webhook_deliveries
                   WHERE status = 'pending' AND next_attempt_at <= %s""",
                (now,)
            )
            return cursor.fetchone()['n']


# Global database manager instance
db_manager = DatabaseManager()
