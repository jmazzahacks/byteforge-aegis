#!/usr/bin/env python
"""
Lowercase every users.email so one mailbox is one account.

Matching was exact and UNIQUE(site_uuid, email) is case sensitive, so
Victim@example.com and victim@example.com were two separate accounts
delivering to the same real mailbox.

This is destructive in the sense that it rewrites stored addresses, and it
CANNOT run if any site already holds two rows differing only by case —
lowercasing those would violate the unique constraint. In that situation the
script reports the offending pairs and stops, because which account survives
is a judgement call about real users' data, not something a migration should
decide.

Usage:
    python migrate_scripts/normalize_user_emails.py            # dry run
    python migrate_scripts/normalize_user_emails.py --apply    # writes

Reads DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD from the
environment, as the other scripts in this directory do.
"""
import os
import sys
from typing import Dict, List, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor


def get_db_config() -> Dict[str, object]:
    """Connection settings for the database being migrated."""
    return {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', 5432)),
        'dbname': os.getenv('DB_NAME', 'aegis'),
        'user': os.getenv('DB_USER', 'aegis_admin'),
        'password': os.getenv('DB_PASSWORD', ''),
    }


def find_collisions(cursor) -> List[Tuple[str, str, int]]:
    """
    Sites where two or more rows differ only by case.

    Returns (site_uuid, lowercased_email, row_count) for each clash.
    """
    cursor.execute(
        """
        SELECT site_uuid, lower(email) AS normalized, count(*) AS n
        FROM users
        GROUP BY site_uuid, lower(email)
        HAVING count(*) > 1
        ORDER BY site_uuid, normalized
        """
    )
    return [(row['site_uuid'], row['normalized'], row['n']) for row in cursor.fetchall()]


def find_rows_needing_change(cursor) -> List[Tuple[str, str]]:
    """(uuid, email) for every row whose address is not already normalised."""
    cursor.execute(
        """
        SELECT uuid, email
        FROM users
        WHERE email <> lower(btrim(email))
        ORDER BY email
        """
    )
    return [(row['uuid'], row['email']) for row in cursor.fetchall()]


def main() -> None:
    apply_changes = '--apply' in sys.argv

    print('=' * 60)
    print('Normalize user emails to lowercase')
    print('=' * 60)
    print()

    config = get_db_config()
    print(f"Database:    {config['dbname']} on {config['host']}:{config['port']}")
    print(f"Mode:        {'APPLY (writes)' if apply_changes else 'DRY RUN (no writes)'}")
    print()

    conn = psycopg2.connect(cursor_factory=RealDictCursor, **config)
    try:
        with conn.cursor() as cursor:
            cursor.execute("SET lock_timeout = '5s'")

            print('Pre-flight checks...')
            collisions = find_collisions(cursor)
            if collisions:
                print()
                print(f'  ABORT: {len(collisions)} address(es) exist twice, differing only by case.')
                print('  Lowercasing would violate UNIQUE(site_uuid, email).')
                print('  Merge or remove the duplicates first — which account survives')
                print('  is a decision about real users, not one this script should make.')
                print()
                for site_uuid, normalized, count in collisions:
                    print(f'    site {site_uuid}  {normalized}  ({count} rows)')
                sys.exit(1)

            print('  no case-collisions — safe to normalize')

            rows = find_rows_needing_change(cursor)
            print(f'  {len(rows)} row(s) need changing')
            print()

            if not rows:
                print('Nothing to do; every address is already normalized.')
                return

            for user_uuid, email in rows[:20]:
                print(f'  {email}  ->  {email.strip().lower()}')
            if len(rows) > 20:
                print(f'  ... and {len(rows) - 20} more')
            print()

            if not apply_changes:
                print('Dry run complete — nothing was written.')
                print('Re-run with --apply to perform the migration.')
                return

            cursor.execute("UPDATE users SET email = lower(btrim(email)) WHERE email <> lower(btrim(email))")
            changed = cursor.rowcount
            conn.commit()
            print(f'Migration committed — {changed} row(s) updated.')
    except Exception as error:
        conn.rollback()
        print(f'\nFAILED: {error}')
        sys.exit(1)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
