#!/usr/bin/env python
"""
Migration script to add tenant_api_key to sites.

Adds the tenant_api_key column to the sites table, generates a key for
every existing site, then sets the column NOT NULL. Generated keys are
printed at the end of the run so the admin can record them and
distribute them to tenant operators before enforcement is enabled.

This is a one-time migration for existing databases. New databases
will have the column created automatically via schema.sql.

Usage:
    source bin/activate && python migrate_scripts/migrate_tenant_api_key.py
"""
import os
import secrets
import sys
import psycopg2
from dotenv import load_dotenv

load_dotenv()


def get_db_config() -> dict:
    """Get database configuration from environment variables."""
    return {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', 5432)),
        'user': os.getenv('DB_USER', 'auth-admin'),
        'password': os.getenv('DB_PASSWORD', 'auth-admin'),
        'dbname': os.getenv('DB_NAME', 'auth_service')
    }


def column_exists(cursor: psycopg2.extensions.cursor, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    cursor.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.columns
            WHERE table_name = %s AND column_name = %s
        )
    """, (table_name, column_name))
    return cursor.fetchone()[0]


def column_is_nullable(cursor: psycopg2.extensions.cursor, table_name: str, column_name: str) -> bool:
    """Check whether a column is currently nullable."""
    cursor.execute("""
        SELECT is_nullable
        FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
    """, (table_name, column_name))
    row = cursor.fetchone()
    return row is not None and row[0] == 'YES'


def run_migration() -> None:
    """Add tenant_api_key column, backfill, set NOT NULL."""
    db_config = get_db_config()

    print(f"Connecting to database '{db_config['dbname']}' on {db_config['host']}:{db_config['port']}...")

    generated = []

    try:
        conn = psycopg2.connect(**db_config)
        cursor = conn.cursor()

        if not column_exists(cursor, 'sites', 'tenant_api_key'):
            print("Adding 'tenant_api_key' column to sites (nullable)...")
            cursor.execute("ALTER TABLE sites ADD COLUMN tenant_api_key VARCHAR(64)")
        else:
            print("Column 'tenant_api_key' already exists on sites. Skipping ADD.")

        cursor.execute("SELECT id, domain FROM sites WHERE tenant_api_key IS NULL")
        rows = cursor.fetchall()
        if rows:
            print(f"Backfilling {len(rows)} site(s) with generated keys...")
            for site_id, domain in rows:
                key = secrets.token_hex(32)
                cursor.execute(
                    "UPDATE sites SET tenant_api_key = %s WHERE id = %s",
                    (key, site_id)
                )
                generated.append((site_id, domain, key))
        else:
            print("No sites need backfilling.")

        if column_is_nullable(cursor, 'sites', 'tenant_api_key'):
            print("Setting 'tenant_api_key' NOT NULL...")
            cursor.execute("ALTER TABLE sites ALTER COLUMN tenant_api_key SET NOT NULL")
        else:
            print("Column 'tenant_api_key' is already NOT NULL. Skipping.")

        conn.commit()

        cursor.close()
        conn.close()

        print("\nMigration completed successfully!")

        if generated:
            print()
            print("=" * 72)
            print("Generated tenant API keys — record these and distribute to tenants:")
            print("=" * 72)
            for site_id, domain, key in generated:
                print(f"site_id={site_id}  domain={domain}")
                print(f"  AEGIS_TENANT_API_KEY={key}")
                print()

    except psycopg2.Error as e:
        print(f"Database error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def main() -> None:
    print("=" * 60)
    print("Migration: Add tenant_api_key to sites")
    print("=" * 60)
    print()

    run_migration()


if __name__ == '__main__':
    main()
