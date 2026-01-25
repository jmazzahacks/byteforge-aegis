#!/usr/bin/env python
"""
Migration script to add allow_self_registration column to sites table.

This is a one-time migration for existing databases. New databases will have
the column created automatically via schema.sql.

Usage:
    source bin/activate && python scripts/migrate_allow_self_registration.py
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


def run_migration() -> None:
    """Add allow_self_registration column if it doesn't exist."""
    db_config = get_db_config()

    print(f"Connecting to database '{db_config['dbname']}' on {db_config['host']}:{db_config['port']}...")

    try:
        conn = psycopg2.connect(**db_config)
        cursor = conn.cursor()

        # Check if column already exists
        cursor.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'sites' AND column_name = 'allow_self_registration'
        """)

        if cursor.fetchone():
            print("Column 'allow_self_registration' already exists. Nothing to do.")
            cursor.close()
            conn.close()
            return

        # Add the column
        print("Adding 'allow_self_registration' column to sites table...")
        cursor.execute("""
            ALTER TABLE sites
            ADD COLUMN IF NOT EXISTS allow_self_registration BOOLEAN DEFAULT TRUE NOT NULL
        """)
        conn.commit()

        print("Migration completed successfully!")
        print("  - Added column: allow_self_registration BOOLEAN DEFAULT TRUE NOT NULL")

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
    print("Migration: Add allow_self_registration to sites table")
    print("=" * 60)
    print()

    run_migration()


if __name__ == '__main__':
    main()
