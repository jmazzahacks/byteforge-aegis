#!/usr/bin/env python
"""
Migration script to add refresh_tokens table.

This is a one-time migration for existing databases. New databases will have
the table created automatically via schema.sql.

Usage:
    source bin/activate && python migrate_scripts/migrate_refresh_tokens.py
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
    """Add refresh_tokens table if it doesn't exist."""
    db_config = get_db_config()

    print(f"Connecting to database '{db_config['dbname']}' on {db_config['host']}:{db_config['port']}...")

    try:
        conn = psycopg2.connect(**db_config)
        cursor = conn.cursor()

        if table_exists(cursor, 'refresh_tokens'):
            print("Table 'refresh_tokens' already exists. Nothing to do.")
            cursor.close()
            conn.close()
            return

        print("Creating 'refresh_tokens' table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS refresh_tokens (
                id SERIAL PRIMARY KEY,
                site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token VARCHAR(255) UNIQUE NOT NULL,
                family_id VARCHAR(255) NOT NULL,
                expires_at BIGINT NOT NULL,
                created_at BIGINT NOT NULL,
                used_at BIGINT,
                revoked BOOLEAN DEFAULT FALSE
            )
        """)

        print("Creating indexes...")
        cursor.execute("CREATE INDEX idx_refresh_tokens_token ON refresh_tokens(token)")
        cursor.execute("CREATE INDEX idx_refresh_tokens_user_id ON refresh_tokens(user_id)")
        cursor.execute("CREATE INDEX idx_refresh_tokens_site_id ON refresh_tokens(site_id)")
        cursor.execute("CREATE INDEX idx_refresh_tokens_family_id ON refresh_tokens(family_id)")
        cursor.execute("CREATE INDEX idx_refresh_tokens_expires_at ON refresh_tokens(expires_at)")

        conn.commit()

        print("Migration completed successfully!")
        print("  - Created table: refresh_tokens")
        print("  - Created indexes: token, user_id, site_id, family_id, expires_at")

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
    print("Migration: Add refresh_tokens table")
    print("=" * 60)
    print()

    run_migration()


if __name__ == '__main__':
    main()
