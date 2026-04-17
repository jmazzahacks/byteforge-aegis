import argparse
import os
import sys
import getpass
import psycopg2
from psycopg2 import sql
from dotenv import load_dotenv

load_dotenv()


def get_app_db_config(test_mode: bool) -> dict:
    """Get application database configuration from .env"""
    db_name = os.getenv('DB_NAME', 'auth_service')

    if test_mode:
        db_name = f"{db_name}_test"

    return {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', 5432)),
        'user': os.getenv('DB_USER', 'auth-admin'),
        'password': os.getenv('DB_PASSWORD', 'auth-admin'),
        'dbname': db_name
    }


def user_exists(cursor, username: str) -> bool:
    """Check if PostgreSQL user exists"""
    cursor.execute(
        "SELECT 1 FROM pg_user WHERE usename = %s",
        (username,)
    )
    return cursor.fetchone() is not None


def database_exists(cursor, db_name: str) -> bool:
    """Check if database exists"""
    cursor.execute(
        "SELECT 1 FROM pg_database WHERE datname = %s",
        (db_name,)
    )
    return cursor.fetchone() is not None


def create_user(cursor, username: str, password: str) -> None:
    """Create PostgreSQL user if it doesn't exist"""
    if user_exists(cursor, username):
        print(f"User '{username}' already exists")
    else:
        cursor.execute(
            sql.SQL("CREATE USER {} WITH PASSWORD %s").format(sql.Identifier(username)),
            (password,)
        )
        print(f"User '{username}' created successfully")


def create_database(cursor, db_name: str, owner: str) -> None:
    """Create database if it doesn't exist"""
    if database_exists(cursor, db_name):
        print(f"Database '{db_name}' already exists")
    else:
        cursor.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(db_name),
                sql.Identifier(owner)
            )
        )
        print(f"Database '{db_name}' created successfully with owner '{owner}'")


def run_schema(db_config: dict) -> None:
    """Run schema.sql to create tables"""
    schema_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'database', 'schema.sql')

    if not os.path.exists(schema_path):
        print(f"Error: schema.sql not found at {schema_path}")
        sys.exit(1)

    with open(schema_path, 'r') as f:
        schema_sql = f.read()

    conn = psycopg2.connect(**db_config)
    cursor = conn.cursor()

    try:
        cursor.execute(schema_sql)
        conn.commit()
        print(f"Schema applied successfully to '{db_config['dbname']}'")
    except Exception as e:
        conn.rollback()
        print(f"Error applying schema: {e}")
        sys.exit(1)
    finally:
        cursor.close()
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description='Setup authentication service database')
    parser.add_argument(
        '--test-db',
        action='store_true',
        help='Setup test database instead of production database'
    )
    parser.add_argument(
        '--postgres-user',
        default='postgres',
        help='PostgreSQL superuser username (default: postgres)'
    )
    parser.add_argument(
        '--postgres-password',
        help='PostgreSQL superuser password (will prompt if not provided)'
    )

    args = parser.parse_args()

    # Get postgres superuser password
    postgres_password = args.postgres_password
    if not postgres_password:
        postgres_password = getpass.getpass(f"Enter password for PostgreSQL user '{args.postgres_user}': ")

    # Get application database config from .env
    app_db_config = get_app_db_config(args.test_db)

    mode = "test" if args.test_db else "production"
    print(f"\nSetting up {mode} database: {app_db_config['dbname']}")
    print(f"Application user: {app_db_config['user']}")

    # Connect as postgres superuser to create user and database
    try:
        conn = psycopg2.connect(
            host=app_db_config['host'],
            port=app_db_config['port'],
            user=args.postgres_user,
            password=postgres_password,
            dbname='postgres'
        )
        conn.autocommit = True
        cursor = conn.cursor()

        # Create application user
        create_user(cursor, app_db_config['user'], app_db_config['password'])

        # Create database owned by application user
        create_database(cursor, app_db_config['dbname'], app_db_config['user'])

        cursor.close()
        conn.close()
    except psycopg2.Error as e:
        print(f"Error connecting to PostgreSQL: {e}")
        sys.exit(1)

    # Apply schema as application user
    run_schema(app_db_config)

    print(f"\n{mode.capitalize()} database setup complete!")
    print(f"Database: {app_db_config['dbname']}")
    print(f"Owner: {app_db_config['user']}")


if __name__ == '__main__':
    main()
