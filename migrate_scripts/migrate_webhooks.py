#!/usr/bin/env python
"""
Migration script to add webhook support to sites and create webhook_events table.

This is a one-time migration for existing databases. New databases will have
the columns and table created automatically via schema.sql.

Usage:
    source bin/activate && python migrate_scripts/migrate_webhooks.py
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


def table_exists(cursor: psycopg2.extensions.cursor, table_name: str) -> bool:
    """Check if a table exists in the database."""
    cursor.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_name = %s
        )
    """, (table_name,))
    return cursor.fetchone()[0]


def run_migration() -> None:
    """Add webhook columns to sites and create webhook_events table."""
    db_config = get_db_config()

    print(f"Connecting to database '{db_config['dbname']}' on {db_config['host']}:{db_config['port']}...")

    try:
        conn = psycopg2.connect(**db_config)
        cursor = conn.cursor()

        changes_made = False

        # Add webhook_url column to sites
        if not column_exists(cursor, 'sites', 'webhook_url'):
            print("Adding 'webhook_url' column to sites...")
            cursor.execute("ALTER TABLE sites ADD COLUMN webhook_url VARCHAR(512)")
            changes_made = True
        else:
            print("Column 'webhook_url' already exists on sites. Skipping.")

        # Add webhook_secret column to sites
        if not column_exists(cursor, 'sites', 'webhook_secret'):
            print("Adding 'webhook_secret' column to sites...")
            cursor.execute("ALTER TABLE sites ADD COLUMN webhook_secret VARCHAR(255)")
            changes_made = True
        else:
            print("Column 'webhook_secret' already exists on sites. Skipping.")

        # Create webhook_events table
        if not table_exists(cursor, 'webhook_events'):
            print("Creating 'webhook_events' table...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS webhook_events (
                    id SERIAL PRIMARY KEY,
                    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
                    event_type VARCHAR(50) NOT NULL,
                    payload TEXT NOT NULL,
                    response_status INTEGER,
                    response_body TEXT,
                    success BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at BIGINT NOT NULL
                )
            """)

            print("Creating indexes...")
            cursor.execute("CREATE INDEX idx_webhook_events_site_id ON webhook_events(site_id)")
            cursor.execute("CREATE INDEX idx_webhook_events_event_type ON webhook_events(event_type)")
            cursor.execute("CREATE INDEX idx_webhook_events_created_at ON webhook_events(created_at)")
            changes_made = True
        else:
            print("Table 'webhook_events' already exists. Skipping.")

        conn.commit()

        if changes_made:
            print("\nMigration completed successfully!")
        else:
            print("\nNothing to migrate. All changes already applied.")

        cursor.close()
        conn.close()

    except psycopg2.Error as e:
        print(f"Database error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def main() -> None:
    print("=" * 60)
    print("Migration: Add webhook support")
    print("=" * 60)
    print()

    run_migration()


if __name__ == '__main__':
    main()
