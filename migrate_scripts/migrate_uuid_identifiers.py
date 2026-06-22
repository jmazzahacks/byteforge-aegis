#!/usr/bin/env python
"""
Migration: add UUID identifiers alongside integer ids (int -> UUID, phase 1).

This is the expand step of migrating site/user identifiers from auto-increment
integers to UUIDs so two Aegis installs can later be merged without primary-key
collisions. After this migration every table carries both its legacy integer
id/FK and a UUID equivalent, with the UUID columns acting as the foreign-key
targets (source of truth).

What it does (in a single transaction = one maintenance window):
  1. Adds UUID columns to all tables (nullable).
  2. Backfills entity UUIDs (sites, users, webhook_events) with gen_random_uuid().
     Existing rows get UUIDv4 — fine, since their index ordering is irrelevant;
     NEW rows are minted UUIDv7 application-side for index locality.
  3. TRUNCATEs auth_tokens + refresh_tokens, forcing a one-time re-auth (the
     identifier change invalidates outstanding sessions by design).
  4. Backfills the UUID foreign keys on the remaining child tables by joining
     on the existing integer FKs.
  5. Sets the UUID columns NOT NULL and adds UNIQUE / FK constraints + indexes.

Existing pending email-verification / password-reset / email-change tokens are
preserved (their UUID FKs are backfilled), so those flows survive the migration.

Run during a maintenance window with the backend stopped:
    source bin/activate && python migrate_scripts/migrate_uuid_identifiers.py
"""
import os
import sys
import psycopg2

from dotenv import load_dotenv

load_dotenv()


def get_db_config() -> dict:
    """Get database configuration from environment variables."""
    return {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', 5432)),
        'user': os.getenv('DB_USER', 'aegis_admin'),
        'password': os.getenv('DB_PASSWORD', 'aegis_admin'),
        'dbname': os.getenv('DB_NAME', 'aegis'),
    }


# Tables that own an entity UUID (their own primary identifier).
ENTITY_UUID_TABLES = ['sites', 'users', 'webhook_events']

# Child tables that carry a site_uuid foreign key (table -> has user_uuid too?).
# Each entry: (table, has_user_uuid). All reference sites(uuid); most also
# reference users(uuid). auth_tokens / refresh_tokens are truncated, not
# backfilled, so they are intentionally excluded from the backfill loop.
SITE_FK_TABLES = [
    ('users', False),
    ('email_verification_tokens', True),
    ('password_reset_tokens', True),
    ('email_change_requests', True),
    ('webhook_events', False),
]

# All tables that need a site_uuid column added.
SITE_UUID_COLUMN_TABLES = [
    'users', 'auth_tokens', 'refresh_tokens', 'email_verification_tokens',
    'password_reset_tokens', 'email_change_requests', 'webhook_events',
]

# All tables that need a user_uuid column added.
USER_UUID_COLUMN_TABLES = [
    'auth_tokens', 'refresh_tokens', 'email_verification_tokens',
    'password_reset_tokens', 'email_change_requests',
]


def constraint_exists(cursor: psycopg2.extensions.cursor, name: str) -> bool:
    """Check whether a named constraint already exists."""
    cursor.execute("SELECT EXISTS (SELECT FROM pg_constraint WHERE conname = %s)", (name,))
    return cursor.fetchone()[0]


def add_fk(cursor: psycopg2.extensions.cursor, table: str, column: str,
           ref_table: str, name: str) -> None:
    """Add a NOT NULL + ON DELETE CASCADE foreign key on a uuid column if absent."""
    cursor.execute(f"ALTER TABLE {table} ALTER COLUMN {column} SET NOT NULL")
    if not constraint_exists(cursor, name):
        cursor.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {name} "
            f"FOREIGN KEY ({column}) REFERENCES {ref_table}(uuid) ON DELETE CASCADE"
        )


def run_migration() -> None:
    db_config = get_db_config()
    print(f"Connecting to database '{db_config['dbname']}' on {db_config['host']}:{db_config['port']}...")

    conn = psycopg2.connect(**db_config)
    try:
        cursor = conn.cursor()

        # 1. Add UUID columns (nullable for now).
        print("Adding UUID columns...")
        for table in ENTITY_UUID_TABLES:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS uuid UUID")
        for table in SITE_UUID_COLUMN_TABLES:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS site_uuid UUID")
        for table in USER_UUID_COLUMN_TABLES:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS user_uuid UUID")

        # 2. Backfill entity UUIDs for existing rows.
        print("Backfilling entity UUIDs...")
        for table in ENTITY_UUID_TABLES:
            cursor.execute(f"UPDATE {table} SET uuid = gen_random_uuid() WHERE uuid IS NULL")

        # 3. Force one-time re-auth: drop all outstanding sessions.
        print("Truncating auth_tokens and refresh_tokens (forces re-auth)...")
        cursor.execute("TRUNCATE auth_tokens, refresh_tokens")

        # 4. Backfill UUID foreign keys by joining on the integer FKs.
        print("Backfilling UUID foreign keys...")
        for table, has_user_uuid in SITE_FK_TABLES:
            cursor.execute(
                f"UPDATE {table} t SET site_uuid = s.uuid "
                f"FROM sites s WHERE t.site_id = s.id AND t.site_uuid IS NULL"
            )
            if has_user_uuid:
                cursor.execute(
                    f"UPDATE {table} t SET user_uuid = u.uuid "
                    f"FROM users u WHERE t.user_id = u.id AND t.user_uuid IS NULL"
                )

        # 5. Constraints + indexes.
        print("Adding UNIQUE/NOT NULL constraints and indexes on entity UUIDs...")
        for table in ENTITY_UUID_TABLES:
            cursor.execute(f"ALTER TABLE {table} ALTER COLUMN uuid SET NOT NULL")
            uniq = f"{table}_uuid_key"
            if not constraint_exists(cursor, uniq):
                cursor.execute(f"ALTER TABLE {table} ADD CONSTRAINT {uniq} UNIQUE (uuid)")

        print("Adding foreign keys + indexes on UUID FK columns...")
        for table in SITE_UUID_COLUMN_TABLES:
            add_fk(cursor, table, 'site_uuid', 'sites', f"{table}_site_uuid_fkey")
            cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_site_uuid ON {table}(site_uuid)")
        for table in USER_UUID_COLUMN_TABLES:
            add_fk(cursor, table, 'user_uuid', 'users', f"{table}_user_uuid_fkey")
            cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_user_uuid ON {table}(user_uuid)")

        conn.commit()
        cursor.close()
        print("\nMigration completed successfully!")
        print("Remember: tenants can now use either their integer site_id or their")
        print("new UUID. Retrieve a site's UUID from the admin API/UI to migrate")
        print("AEGIS_SITE_ID at each tenant's own pace.")
    except psycopg2.Error as e:
        conn.rollback()
        print(f"Database error (rolled back): {e}")
        sys.exit(1)
    finally:
        conn.close()


def main() -> None:
    print("=" * 60)
    print("Migration: Add UUID identifiers (int -> UUID, phase 1)")
    print("=" * 60)
    print()
    run_migration()


if __name__ == '__main__':
    main()
