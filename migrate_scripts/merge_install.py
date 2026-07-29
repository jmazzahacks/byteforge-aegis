#!/usr/bin/env python
"""
Merge one Aegis install's data into another.

Copies sites, users and webhook_events from a SOURCE database into a
DESTINATION database of identical schema. Written for retiring the
aegis.reallybadapps.com install by folding it into aegis.mazza.vc, but it is
not specific to those two.

Run it FROM the source host so it can read the local database and write to the
destination over the network.

WHAT IS COPIED
    sites -> users -> webhook_events, in that order (foreign keys require it).

WHAT IS NOT COPIED, DELIBERATELY
    The five token tables (auth_tokens, refresh_tokens,
    email_verification_tokens, password_reset_tokens, email_change_requests).
    They kept integer SERIAL primary keys through the UUID migration, so
    copying them verbatim would collide with the destination's own ids and
    would need renumbering plus a sequence reset — real risk, in the riskiest
    step, to preserve rows that expire within hours or days. Everyone on the
    source install is logged out by the cutover and signs in again. Anyone
    mid-verification or mid-password-reset requests a new email.

RE-RUNNABLE BY DESIGN
    sites and users UPSERT on uuid; webhook_events is append-only so it takes
    ON CONFLICT DO NOTHING. Run it once while the source is still live to move
    the bulk, then again after stopping the source to catch anything written in
    between — the second pass updates rows that changed, not just new ones.
    A failed run leaves the destination untouched: everything is one
    transaction.

Usage:
    # report only, changes nothing (default)
    source bin/activate && python migrate_scripts/merge_install.py

    # actually write
    source bin/activate && python migrate_scripts/merge_install.py --apply

    Source database:      DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME
    Destination database: DEST_DB_HOST / DEST_DB_PORT / DEST_DB_USER /
                          DEST_DB_PASSWORD / DEST_DB_NAME
"""
import argparse
import os
import sys
from typing import Any, Dict, List, Tuple

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

# (table, conflict action) in foreign-key order — sites must exist before users,
# and both before webhook_events.
MERGED_TABLES: List[Tuple[str, str]] = [
    ('sites', 'update'),
    ('users', 'update'),
    ('webhook_events', 'nothing'),
]

# Not copied. See the module docstring — this is a decision, not an oversight.
SKIPPED_TABLES: List[str] = [
    'auth_tokens',
    'refresh_tokens',
    'email_verification_tokens',
    'password_reset_tokens',
    'email_change_requests',
]

BATCH_SIZE = 500


def get_source_config() -> Dict[str, Any]:
    """Source database — the install being retired."""
    return {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', 5432)),
        'user': os.getenv('DB_USER', 'aegis_admin'),
        'password': os.getenv('DB_PASSWORD', 'aegis_admin'),
        'dbname': os.getenv('DB_NAME', 'aegis'),
    }


def get_dest_config() -> Dict[str, Any]:
    """Destination database — the install that survives."""
    return {
        'host': os.getenv('DEST_DB_HOST', ''),
        'port': int(os.getenv('DEST_DB_PORT', 5432)),
        'user': os.getenv('DEST_DB_USER', 'aegis_admin'),
        'password': os.getenv('DEST_DB_PASSWORD', ''),
        'dbname': os.getenv('DEST_DB_NAME', 'aegis'),
    }


def table_columns(cursor: psycopg2.extensions.cursor, table: str) -> List[str]:
    """Column names for a table, in ordinal order.

    Read from the database rather than hardcoded so this script cannot drift
    away from schema.sql as columns are added.
    """
    cursor.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = %s
        ORDER BY ordinal_position
        """,
        (table,)
    )
    return [row[0] for row in cursor.fetchall()]


def row_count(cursor: psycopg2.extensions.cursor, table: str) -> int:
    cursor.execute(f"SELECT COUNT(*) FROM {table}")
    return cursor.fetchone()[0]


def site_values_by_uuid(cursor: psycopg2.extensions.cursor, column: str) -> Dict[Any, str]:
    """Map a sites column's value to the uuid of the site holding it."""
    cursor.execute(f"SELECT {column}, uuid FROM sites WHERE {column} IS NOT NULL")
    return {row[0]: str(row[1]) for row in cursor.fetchall()}


def conflicting_values(src: Dict[Any, str], dst: Dict[Any, str]) -> List[Any]:
    """Values present on both sides but belonging to DIFFERENT sites.

    A value shared by the same uuid is not a conflict — it is a row this
    script already copied. Without that distinction the second (delta) pass
    would abort on the work the first pass did, which is precisely when it is
    needed most.
    """
    return sorted(value for value, src_uuid in src.items()
                  if value in dst and dst[value] != src_uuid)


def preflight(src_cursor: psycopg2.extensions.cursor,
              dst_cursor: psycopg2.extensions.cursor) -> None:
    """Abort before writing anything if the two databases can't be merged.

    Raises RuntimeError on any condition that would corrupt the destination or
    silently weaken tenant isolation.
    """
    print("Pre-flight checks...")

    # 1. Schema parity. A column present on one side and not the other means
    #    the installs are at different migrations; merging would drop or fail.
    for table, _ in MERGED_TABLES:
        src_cols = set(table_columns(src_cursor, table))
        dst_cols = set(table_columns(dst_cursor, table))
        if not src_cols:
            raise RuntimeError(f"source is missing table {table} — aborting")
        if not dst_cols:
            raise RuntimeError(f"destination is missing table {table} — aborting")
        if src_cols != dst_cols:
            only_src = sorted(src_cols - dst_cols)
            only_dst = sorted(dst_cols - src_cols)
            raise RuntimeError(
                f"{table} schema differs — source-only: {only_src}, "
                f"destination-only: {only_dst}. Bring both installs to the "
                f"same migration before merging."
            )
    print(f"  schema parity OK across {len(MERGED_TABLES)} tables")

    # 2. sites.domain is UNIQUE. This is the one hard collision in the schema.
    #    Compare by owning uuid so an already-copied row is not mistaken for a
    #    clash — that is what makes this script re-runnable.
    domain_clash = conflicting_values(
        site_values_by_uuid(src_cursor, 'domain'),
        site_values_by_uuid(dst_cursor, 'domain'),
    )
    if domain_clash:
        raise RuntimeError(
            f"these domains belong to DIFFERENT sites on each install: "
            f"{domain_clash}. sites.domain is UNIQUE — resolve before merging."
        )
    print("  no sites.domain collisions")

    # 3. tenant_api_key has NO unique constraint, so a duplicate would not
    #    error — it would quietly let one tenant's key authenticate against
    #    another tenant's site. Check it explicitly.
    key_clash = conflicting_values(
        site_values_by_uuid(src_cursor, 'tenant_api_key'),
        site_values_by_uuid(dst_cursor, 'tenant_api_key'),
    )
    if key_clash:
        raise RuntimeError(
            f"{len(key_clash)} tenant_api_key value(s) are shared by DIFFERENT "
            f"sites across the two installs. There is no UNIQUE constraint on "
            f"that column, so this would not raise — it would weaken tenant "
            f"isolation. Rotate before merging."
        )
    print("  no tenant_api_key collisions")


def report_hazards(src_cursor: psycopg2.extensions.cursor) -> None:
    """Print things that merge cleanly but change behaviour afterwards."""
    # A site with NULL mailgun config falls back to the INSTALL's env vars.
    # After the merge that is the destination's Mailgun account, not the
    # source's — so tenant email can start sending from a different domain
    # with no error anywhere.
    src_cursor.execute(
        "SELECT domain FROM sites "
        "WHERE mailgun_domain IS NULL OR mailgun_api_key IS NULL "
        "ORDER BY domain"
    )
    inheriting = [row[0] for row in src_cursor.fetchall()]
    if inheriting:
        print("\n  WARNING: these sites have no per-site Mailgun config and will")
        print("  inherit the DESTINATION install's MAILGUN_* env after the merge:")
        for domain in inheriting:
            print(f"    - {domain}")
        print("  Confirm the destination's Mailgun credentials match, or set")
        print("  per-site values, before cutover.")

    # Admins on the source can't use the destination's console unless they
    # also have an account on its configured AEGIS_ADMIN_DOMAIN site.
    src_cursor.execute(
        "SELECT s.domain, u.email FROM users u "
        "JOIN sites s ON s.uuid = u.site_uuid "
        "WHERE u.role = 'admin' ORDER BY s.domain, u.email"
    )
    admins = src_cursor.fetchall()
    if admins:
        print("\n  NOTE: admin accounts on the source install:")
        for domain, email in admins:
            print(f"    - {email} ({domain})")
        print("  Console access is single-site: anyone who administers the")
        print("  retiring install needs an equivalent account on the surviving")
        print("  console site, or they lose access at cutover.")


def build_upsert(table: str, columns: List[str], action: str) -> str:
    """INSERT ... ON CONFLICT statement for one table."""
    col_list = ', '.join(columns)
    if action == 'nothing':
        return f"INSERT INTO {table} ({col_list}) VALUES %s ON CONFLICT (uuid) DO NOTHING"

    updates = ', '.join(f"{col} = EXCLUDED.{col}" for col in columns if col != 'uuid')
    return (
        f"INSERT INTO {table} ({col_list}) VALUES %s "
        f"ON CONFLICT (uuid) DO UPDATE SET {updates}"
    )


def copy_table(src_cursor: psycopg2.extensions.cursor,
               dst_cursor: psycopg2.extensions.cursor,
               table: str,
               action: str,
               apply_changes: bool) -> None:
    """Copy every row of one table from source to destination."""
    columns = table_columns(src_cursor, table)
    before = row_count(dst_cursor, table)

    src_cursor.execute(f"SELECT {', '.join(columns)} FROM {table}")
    rows = src_cursor.fetchall()

    if not apply_changes:
        print(f"  {table}: would copy {len(rows)} row(s) "
              f"(destination currently has {before})")
        return

    if rows:
        execute_values(dst_cursor, build_upsert(table, columns, action), rows,
                       page_size=BATCH_SIZE)

    after = row_count(dst_cursor, table)
    print(f"  {table}: copied {len(rows)} row(s), "
          f"destination {before} -> {after}")


def run_merge(apply_changes: bool) -> None:
    src_config = get_source_config()
    dst_config = get_dest_config()

    if not dst_config['host']:
        raise RuntimeError("DEST_DB_HOST is not set — refusing to guess the destination")

    print(f"Source:      {src_config['dbname']} on {src_config['host']}:{src_config['port']}")
    print(f"Destination: {dst_config['dbname']} on {dst_config['host']}:{dst_config['port']}")
    print(f"Mode:        {'APPLY (writes)' if apply_changes else 'DRY RUN (no writes)'}\n")

    src_conn = psycopg2.connect(**src_config)
    dst_conn = psycopg2.connect(**dst_config)
    try:
        src_cursor = src_conn.cursor()
        dst_cursor = dst_conn.cursor()

        # Fail fast rather than block behind another connection's lock.
        dst_cursor.execute("SET lock_timeout = '10s'")

        preflight(src_cursor, dst_cursor)
        report_hazards(src_cursor)

        print("\nCopying tables...")
        for table, action in MERGED_TABLES:
            copy_table(src_cursor, dst_cursor, table, action, apply_changes)

        print("\nSkipped, by design (see docstring):")
        for table in SKIPPED_TABLES:
            print(f"  {table}: {row_count(src_cursor, table)} row(s) left behind")

        if apply_changes:
            dst_conn.commit()
            print("\nMerge committed.")
        else:
            dst_conn.rollback()
            print("\nDry run complete — nothing was written.")
            print("Re-run with --apply to perform the merge.")
    except (psycopg2.Error, RuntimeError) as e:
        dst_conn.rollback()
        print(f"\nFAILED (destination rolled back, nothing written): {e}")
        sys.exit(1)
    finally:
        src_conn.close()
        dst_conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge one Aegis install's data into another."
    )
    parser.add_argument(
        '--apply',
        action='store_true',
        help='Actually write to the destination. Without this, reports only.'
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Merge: fold one Aegis install into another")
    print("=" * 60)
    print()

    run_merge(args.apply)


if __name__ == '__main__':
    main()
