#!/usr/bin/env python
"""
Migration script to add deletion protection to users.

Adds a deletion_protected BOOLEAN NOT NULL DEFAULT FALSE column to the users
table. When true, the admin delete endpoint refuses to delete that user.

Every existing user defaults to FALSE, so behavior is unchanged until an
operator marks an account. Requested by the Arcana tenant (HiveMake ticket
f483e977) for accounts whose downstream records custody real assets — losing
the Aegis identity would leave those assets unattributable.

This is a one-time migration for existing databases. New databases will have
the column created automatically via schema.sql.

Usage:
    source bin/activate && python migrate_scripts/migrate_deletion_protected.py
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
    """Add the deletion_protected column to users."""
    db_config = get_db_config()

    print(f"Connecting to database '{db_config['dbname']}' on {db_config['host']}:{db_config['port']}...")

    try:
        conn = psycopg2.connect(**db_config)
        cursor = conn.cursor()

        if not column_exists(cursor, 'users', 'deletion_protected'):
            print("Adding 'deletion_protected' column to users (NOT NULL DEFAULT FALSE)...")
            cursor.execute(
                "ALTER TABLE users ADD COLUMN deletion_protected BOOLEAN NOT NULL DEFAULT FALSE"
            )
        else:
            print("Column 'deletion_protected' already exists on users. Skipping.")

        conn.commit()

        cursor.execute("SELECT COUNT(*) FROM users WHERE deletion_protected = TRUE")
        protected_count = cursor.fetchone()[0]

        cursor.close()
        conn.close()

        print("\nMigration completed successfully!")
        print()
        print(f"Users currently protected from deletion: {protected_count}")
        print("All existing users default to unprotected — delete behavior is")
        print("unchanged until an account is marked via the admin API:")
        print("  PATCH /api/admin/users/<uuid>  {\"deletion_protected\": true}")

    except psycopg2.Error as e:
        print(f"Database error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def main() -> None:
    print("=" * 60)
    print("Migration: Add deletion protection to users")
    print("=" * 60)
    print()

    run_migration()


if __name__ == '__main__':
    main()
