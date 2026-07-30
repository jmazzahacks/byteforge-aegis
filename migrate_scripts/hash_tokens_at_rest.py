#!/usr/bin/env python
"""
Replace stored bearer tokens with their SHA-256 digests.

Tokens were stored verbatim, so any read of auth_tokens or refresh_tokens
was immediate takeover of every live session across every tenant.

Existing rows are hashed IN PLACE rather than truncated, so nobody is logged
out: the digest of a stored plaintext is exactly what the new lookup path
computes from the token the client still holds. Sessions carry on working
across the deploy.

ORDERING MATTERS. This must run while the OLD code is live, or during a
brief window with the app stopped — never after the new code is serving:

    old app + unhashed rows   -> works (old code compares plaintext)
    new app + hashed rows     -> works (new code compares digests)
    new app + unhashed rows   -> EVERY request 401s; nothing matches
    old app + hashed rows     -> EVERY request 401s; nothing matches

So the safe sequences are "migrate, then rotate" or "stop, migrate, start".
Rotating first leaves every session broken until this runs.

Re-running is safe: a row whose token is already a 64-character lowercase
hex digest is skipped, so a partial run can be resumed. The one thing that
would corrupt data is a real token that happens to look like a digest, and
secrets.token_urlsafe(32) produces 43 characters with a different alphabet,
so that cannot occur.

Usage:
    python migrate_scripts/hash_tokens_at_rest.py            # dry run
    python migrate_scripts/hash_tokens_at_rest.py --apply    # writes

Reads DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD from the
environment, as the other scripts in this directory do.
"""
import os
import sys
from typing import Dict

import psycopg2
from psycopg2.extras import RealDictCursor

# Tables whose `token` column holds a bearer credential.
TOKEN_TABLES = ('auth_tokens', 'refresh_tokens')

# A row already migrated: 64 lowercase hex characters. A live token is 43
# characters of URL-safe base64, so the two cannot be confused.
ALREADY_HASHED = "token ~ '^[0-9a-f]{64}$'"


def get_db_config() -> Dict[str, object]:
    """Connection settings for the database being migrated."""
    return {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', 5432)),
        'dbname': os.getenv('DB_NAME', 'aegis'),
        'user': os.getenv('DB_USER', 'aegis_admin'),
        'password': os.getenv('DB_PASSWORD', ''),
    }


def count_rows(cursor, table: str, predicate: str) -> int:
    cursor.execute(f'SELECT count(*) AS n FROM {table} WHERE {predicate}')
    return cursor.fetchone()['n']


def main() -> None:
    apply_changes = '--apply' in sys.argv

    print('=' * 60)
    print('Hash bearer tokens at rest')
    print('=' * 60)
    print()

    config = get_db_config()
    print(f"Database:    {config['dbname']} on {config['host']}:{config['port']}")
    print(f"Mode:        {'APPLY (writes)' if apply_changes else 'DRY RUN (no writes)'}")
    print()
    print('Reminder: run this BEFORE rotating to the new image, or with the')
    print('app stopped. New code against unhashed rows 401s every request.')
    print()

    conn = psycopg2.connect(cursor_factory=RealDictCursor, **config)
    try:
        with conn.cursor() as cursor:
            cursor.execute("SET lock_timeout = '5s'")

            print('Current state...')
            plan = {}
            for table in TOKEN_TABLES:
                total = count_rows(cursor, table, 'TRUE')
                done = count_rows(cursor, table, ALREADY_HASHED)
                plan[table] = total - done
                print(f'  {table:18} {total} row(s), {done} already hashed, '
                      f'{total - done} to migrate')
            print()

            if not any(plan.values()):
                print('Nothing to do; every token is already hashed.')
                return

            if not apply_changes:
                print('Dry run complete — nothing was written.')
                print('Re-run with --apply to perform the migration.')
                return

            for table in TOKEN_TABLES:
                # sha256() is built in from Postgres 11; encode() renders it
                # as the same lowercase hex the application produces.
                cursor.execute(
                    f"""UPDATE {table}
                        SET token = encode(sha256(token::bytea), 'hex')
                        WHERE NOT ({ALREADY_HASHED})"""
                )
                print(f'  {table:18} {cursor.rowcount} row(s) hashed')

            conn.commit()
            print()
            print('Migration committed. Existing sessions keep working.')
    except Exception as error:
        conn.rollback()
        print(f'\nFAILED, rolled back: {error}')
        sys.exit(1)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
