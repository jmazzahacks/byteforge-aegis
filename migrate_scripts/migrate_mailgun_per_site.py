#!/usr/bin/env python
"""
Migration script to add per-site Mailgun config to sites.

Adds nullable mailgun_domain and mailgun_api_key columns to the sites table.
Soft cutover: existing sites keep working via the global MAILGUN_DOMAIN /
MAILGUN_API_KEY env vars until each tenant's per-site config is set via the
admin dashboard.

This is a one-time migration for existing databases. New databases will have
the columns created automatically via schema.sql.

Usage:
    source bin/activate && python migrate_scripts/migrate_mailgun_per_site.py
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


def run_migration() -> None:
    """Add mailgun_domain and mailgun_api_key columns (nullable)."""
    db_config = get_db_config()

    print(f"Connecting to database '{db_config['dbname']}' on {db_config['host']}:{db_config['port']}...")

    try:
        conn = psycopg2.connect(**db_config)
        cursor = conn.cursor()

        if not column_exists(cursor, 'sites', 'mailgun_domain'):
            print("Adding 'mailgun_domain' column to sites (nullable)...")
            cursor.execute("ALTER TABLE sites ADD COLUMN mailgun_domain VARCHAR(255)")
        else:
            print("Column 'mailgun_domain' already exists on sites. Skipping.")

        if not column_exists(cursor, 'sites', 'mailgun_api_key'):
            print("Adding 'mailgun_api_key' column to sites (nullable)...")
            cursor.execute("ALTER TABLE sites ADD COLUMN mailgun_api_key VARCHAR(255)")
        else:
            print("Column 'mailgun_api_key' already exists on sites. Skipping.")

        conn.commit()
        cursor.close()
        conn.close()

        print("\nMigration completed successfully!")
        print()
        print("Sites without per-site config will fall back to the global")
        print("MAILGUN_DOMAIN / MAILGUN_API_KEY env vars. Configure per-site")
        print("values via the admin dashboard when ready.")

    except psycopg2.Error as e:
        print(f"Database error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def main() -> None:
    print("=" * 60)
    print("Migration: Add per-site Mailgun config to sites")
    print("=" * 60)
    print()

    run_migration()


if __name__ == '__main__':
    main()
