#!/usr/bin/env python
"""
Migration: drop integer identifiers, UUID-only (int -> UUID, phase 3: contract).

This is the contract step of the int -> UUID identifier migration. It runs only
after (a) every tenant has flipped to UUID addressing and (b) the phase-3 bake
confirmed zero integer-identifier traffic. It is IRREVERSIBLE: the integer
columns — and with them the int->UUID mapping — are dropped.

Before running, export the mapping as a safety net (per install):
    psql ... -c "COPY (SELECT id, uuid FROM sites) TO STDOUT WITH CSV HEADER"
    psql ... -c "COPY (SELECT id, uuid, site_id, site_uuid FROM users) TO STDOUT WITH CSV HEADER"

What it does (in a single transaction = one maintenance window):
  1. Drops every FOREIGN KEY that references sites(uuid)/users(uuid) — they pin
     the uuid UNIQUE constraints, which must be replaced by PRIMARY KEYs.
  2. Drops the int FK columns (site_id/user_id) from the five token tables
     (auth_tokens, refresh_tokens, email_verification_tokens,
     password_reset_tokens, email_change_requests). Their surrogate int `id`
     PKs are kept — they are never exposed and carry no merge risk.
  3. users: drops site_id (taking UNIQUE(site_id, email) with it) and adds
     UNIQUE(site_uuid, email).
  4. sites, users, webhook_events: drop the int id PRIMARY KEY and the uuid
     UNIQUE constraint, then make uuid the PRIMARY KEY.
  5. Recreates all uuid FOREIGN KEYs with ON DELETE CASCADE (tables are small;
     revalidation is instant).

Outstanding sessions survive: auth/refresh tokens already carry their UUID
FKs, so no truncation and no forced re-auth this time.

Run during a maintenance window with the backend stopped:
    source bin/activate && python migrate_scripts/contract_uuid_identifiers.py
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


# Token tables: drop both int FK columns, keep the surrogate int id PK.
TOKEN_TABLES = [
    'auth_tokens', 'refresh_tokens', 'email_verification_tokens',
    'password_reset_tokens', 'email_change_requests',
]

# Entity tables whose int id PK is replaced by the uuid column.
ENTITY_TABLES = ['sites', 'users', 'webhook_events']

# Every uuid FK in the schema: (table, column, referenced table).
# Names follow PostgreSQL's default `{table}_{column}_fkey`, which both the
# phase-1 migration and fresh schema.sql installs produce.
UUID_FOREIGN_KEYS = [
    ('users', 'site_uuid', 'sites'),
    ('auth_tokens', 'site_uuid', 'sites'),
    ('auth_tokens', 'user_uuid', 'users'),
    ('refresh_tokens', 'site_uuid', 'sites'),
    ('refresh_tokens', 'user_uuid', 'users'),
    ('email_verification_tokens', 'site_uuid', 'sites'),
    ('email_verification_tokens', 'user_uuid', 'users'),
    ('password_reset_tokens', 'site_uuid', 'sites'),
    ('password_reset_tokens', 'user_uuid', 'users'),
    ('email_change_requests', 'site_uuid', 'sites'),
    ('email_change_requests', 'user_uuid', 'users'),
    ('webhook_events', 'site_uuid', 'sites'),
]


def constraint_exists(cursor, name: str) -> bool:
    """Check whether a named constraint already exists."""
    cursor.execute("SELECT EXISTS (SELECT FROM pg_constraint WHERE conname = %s)", (name,))
    return cursor.fetchone()[0]


def column_exists(cursor, table: str, column: str) -> bool:
    """Check whether a column exists on a table."""
    cursor.execute(
        "SELECT EXISTS (SELECT FROM information_schema.columns "
        "WHERE table_name = %s AND column_name = %s)",
        (table, column)
    )
    return cursor.fetchone()[0]


def drop_column(cursor, table: str, column: str) -> None:
    """Drop a column if present (indexes/constraints on it go with it)."""
    if column_exists(cursor, table, column):
        cursor.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        print(f"  dropped {table}.{column}")
    else:
        print(f"  {table}.{column} already gone, skipping")


def promote_uuid_to_pk(cursor, table: str) -> None:
    """Drop the int id PK and the uuid UNIQUE constraint; make uuid the PK."""
    if not column_exists(cursor, table, 'id'):
        print(f"  {table}.id already gone, skipping")
        return
    cursor.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_pkey")
    cursor.execute(f"ALTER TABLE {table} DROP COLUMN id")
    if constraint_exists(cursor, f"{table}_uuid_key"):
        cursor.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_uuid_key")
    cursor.execute(f"ALTER TABLE {table} ADD CONSTRAINT {table}_pkey PRIMARY KEY (uuid)")
    print(f"  {table}: dropped int id, uuid is now PRIMARY KEY")


def run_migration() -> None:
    db_config = get_db_config()
    print(f"Connecting to database '{db_config['dbname']}' on {db_config['host']}:{db_config['port']}...")

    conn = psycopg2.connect(**db_config)
    try:
        cursor = conn.cursor()

        # Fail fast rather than block behind a stray connection's lock — the
        # runbook says the backend is stopped, so nothing should hold one.
        cursor.execute("SET lock_timeout = '10s'")

        # Safety gate: every row must carry its UUIDs before ints are dropped.
        print("Verifying UUID completeness...")
        for table in ENTITY_TABLES:
            cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE uuid IS NULL")
            nulls = cursor.fetchone()[0]
            if nulls:
                raise RuntimeError(f"{table} has {nulls} rows with NULL uuid — aborting")
        for table, column, _ in UUID_FOREIGN_KEYS:
            cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} IS NULL")
            nulls = cursor.fetchone()[0]
            if nulls:
                raise RuntimeError(f"{table} has {nulls} rows with NULL {column} — aborting")

        # 1. Drop uuid FKs so the uuid UNIQUE constraints can be replaced by PKs.
        print("Dropping uuid foreign keys (recreated at the end)...")
        for table, column, _ in UUID_FOREIGN_KEYS:
            name = f"{table}_{column}_fkey"
            if constraint_exists(cursor, name):
                cursor.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")

        # 2. Token tables: drop int FK columns.
        print("Dropping int FK columns from token tables...")
        for table in TOKEN_TABLES:
            drop_column(cursor, table, 'site_id')
            drop_column(cursor, table, 'user_id')

        # 3. users: re-point the per-site email uniqueness, then drop ints.
        print("Contracting users...")
        if not constraint_exists(cursor, 'users_site_uuid_email_key'):
            cursor.execute(
                "ALTER TABLE users ADD CONSTRAINT users_site_uuid_email_key "
                "UNIQUE (site_uuid, email)"
            )
            print("  added UNIQUE(site_uuid, email)")
        drop_column(cursor, 'users', 'site_id')
        promote_uuid_to_pk(cursor, 'users')

        # 4. webhook_events, then sites (nothing references sites(id) by now).
        print("Contracting webhook_events...")
        drop_column(cursor, 'webhook_events', 'site_id')
        promote_uuid_to_pk(cursor, 'webhook_events')

        print("Contracting sites...")
        promote_uuid_to_pk(cursor, 'sites')

        # 5. Recreate the uuid FKs against the new primary keys.
        print("Recreating uuid foreign keys...")
        for table, column, ref_table in UUID_FOREIGN_KEYS:
            name = f"{table}_{column}_fkey"
            cursor.execute(
                f"ALTER TABLE {table} ADD CONSTRAINT {name} "
                f"FOREIGN KEY ({column}) REFERENCES {ref_table}(uuid) ON DELETE CASCADE"
            )

        conn.commit()
        cursor.close()
        print("\nContract migration completed successfully!")
        print("UUID is now the sole identifier. Integer addressing is gone;")
        print("deploy the UUID-only backend image alongside this migration.")
    except (psycopg2.Error, RuntimeError) as e:
        conn.rollback()
        print(f"Error (rolled back): {e}")
        sys.exit(1)
    finally:
        conn.close()


def main() -> None:
    print("=" * 60)
    print("Migration: Drop integer identifiers (int -> UUID, phase 3)")
    print("=" * 60)
    print()
    run_migration()


if __name__ == '__main__':
    main()
