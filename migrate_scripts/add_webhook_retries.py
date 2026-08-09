#!/usr/bin/env python
"""
Add the webhook delivery outbox, and make webhook_events per-attempt.

Webhook delivery was at-most-once: one POST, no retry, and the log row
written only AFTER the attempt. Two consequences, both silent:

  * a non-2xx (or a timeout, or a DNS failure) lost the event outright;
  * anything queued in the in-process thread pool when the container
    rotated vanished with no row at all, so a deploy was a loss window.

This adds `webhook_deliveries`, an outbox written BEFORE the first attempt,
and gives `webhook_events` an `event_id` so it can hold one row per attempt.

WHY event_id IS A NEW COLUMN. webhook_events.uuid held the event id and was
the primary key, so a second attempt for one event collided on it. Retries
are impossible without separating the two. Existing rows are backfilled
with event_id = uuid, which is exactly what they meant, so anything
grepping by a tenant-reported event id keeps working on old and new rows
alike.

Safe to run against a live database, with one honest caveat:
  * both changes are additive — no column is dropped or retyped, and
    event_id is left NULLABLE precisely so the old image keeps working;
  * indexes are built CONCURRENTLY, outside the transaction, so they do
    not hold a write lock while they build;
  * re-running is a no-op, so a partial run can be resumed.

  * THE CAVEAT: `ALTER TABLE ... ADD COLUMN` takes ACCESS EXCLUSIVE, and
    webhook_events IS written on the delivery path — every webhook logs a
    row. Adding a column with no default (event_id) and one with a
    constant default (attempt) are both metadata-only in PG11+, so the
    lock is held for milliseconds rather than a table rewrite. The
    backfill UPDATE that follows runs in the same transaction and DOES
    scale with row count, so on a large webhook_events expect writers to
    block for the length of that UPDATE. lock_timeout bounds waiting to
    acquire the lock, not holding it.

ORDERING. Run this BEFORE rotating to the new image. The new code reads
webhook_deliveries, which does not exist until this runs. The old code
ignores both additions, so the migrated schema serves it fine — that is
what makes "migrate, then rotate" safe and rollback clean, and it is why
event_id must stay nullable (the old insert does not name it, and the
delivery path swallows the error that NOT NULL would raise).

Usage:
    python migrate_scripts/add_webhook_retries.py            # dry run
    python migrate_scripts/add_webhook_retries.py --apply    # writes

Reads DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD from the
environment, as the other scripts in this directory do.
"""
import os
import sys
from typing import Dict

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


# Built CONCURRENTLY, outside the transaction, so they never hold a write
# lock while building. Named here rather than inline so the "is this already
# migrated?" check and the creation loop cannot drift apart.
INDEXES = (
    ('idx_webhook_events_event_id', 'webhook_events', '(event_id)'),
    ('idx_webhook_deliveries_site_uuid', 'webhook_deliveries', '(site_uuid)'),
    ('idx_webhook_deliveries_status', 'webhook_deliveries', '(status)'),
    ('idx_webhook_deliveries_due', 'webhook_deliveries',
     "(next_attempt_at) WHERE status = 'pending'"),
)


def index_exists(cursor, name: str) -> bool:
    cursor.execute('SELECT 1 FROM pg_class WHERE relname = %s', (name,))
    return cursor.fetchone() is not None


def index_is_valid(cursor, name: str) -> bool:
    """True only for an index that exists AND finished building.

    An interrupted CREATE INDEX CONCURRENTLY leaves a row in pg_class with
    indisvalid = false. Postgres will not use it, and IF NOT EXISTS will
    not replace it, so treating "the name exists" as "the index is there"
    is how a table ends up permanently unindexed.
    """
    cursor.execute(
        """SELECT i.indisvalid FROM pg_index i
           JOIN pg_class c ON c.oid = i.indexrelid
           WHERE c.relname = %s""",
        (name,)
    )
    row = cursor.fetchone()
    return bool(row and row['indisvalid'])


def column_exists(cursor, table: str, column: str) -> bool:
    cursor.execute(
        """SELECT 1 FROM information_schema.columns
           WHERE table_name = %s AND column_name = %s""",
        (table, column)
    )
    return cursor.fetchone() is not None


def table_exists(cursor, table: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
        (table,)
    )
    return cursor.fetchone() is not None


def main() -> None:
    apply_changes = '--apply' in sys.argv

    print('=' * 60)
    print('Webhook retries: outbox table + per-attempt event log')
    print('=' * 60)
    print()

    config = get_db_config()
    print(f"Database:    {config['dbname']} on {config['host']}:{config['port']}")
    print(f"Mode:        {'APPLY (writes)' if apply_changes else 'DRY RUN (no writes)'}")
    print()
    print('Reminder: run this BEFORE rotating to the new image. New code')
    print('against the old schema fails every webhook delivery.')
    print()

    conn = psycopg2.connect(cursor_factory=RealDictCursor, **config)
    try:
        with conn.cursor() as cursor:
            cursor.execute("SET lock_timeout = '5s'")

            print('Current state...')
            has_deliveries = table_exists(cursor, 'webhook_deliveries')
            has_event_id = column_exists(cursor, 'webhook_events', 'event_id')
            has_attempt = column_exists(cursor, 'webhook_events', 'attempt')

            cursor.execute('SELECT count(*) AS n FROM webhook_events')
            event_rows = cursor.fetchone()['n']

            print(f"  webhook_deliveries table   {'present' if has_deliveries else 'MISSING'}")
            print(f"  webhook_events.event_id    {'present' if has_event_id else 'MISSING'}")
            print(f"  webhook_events.attempt     {'present' if has_attempt else 'MISSING'}")
            print(f"  webhook_events rows        {event_rows} (backfilled with event_id = uuid)")
            print()

            schema_done = has_deliveries and has_event_id and has_attempt
            missing_indexes = (
                [] if not has_deliveries
                else [name for name, _, _ in INDEXES
                      if not index_is_valid(cursor, name)]
            )

            if missing_indexes:
                print(f"  indexes                    MISSING/INVALID: "
                      f"{', '.join(missing_indexes)}")
            print()

            # The index check is part of the done test, not an afterthought
            # below it. A run that commits the DDL and then dies during
            # CREATE INDEX CONCURRENTLY would otherwise report "already
            # migrated" forever, leaving the once-a-minute claim query on a
            # sequential scan with nothing to say so.
            if schema_done and not missing_indexes:
                print('Nothing to do; the schema is already migrated.')
                return

            if not apply_changes:
                print('Dry run complete — nothing was written.')
                print('Re-run with --apply to perform the migration.')
                return

            if not has_deliveries:
                cursor.execute(
                    """
                    CREATE TABLE webhook_deliveries (
                        event_id UUID PRIMARY KEY,
                        site_uuid UUID NOT NULL REFERENCES sites(uuid) ON DELETE CASCADE,
                        event_type VARCHAR(50) NOT NULL,
                        payload TEXT NOT NULL,
                        status VARCHAR(20) NOT NULL DEFAULT 'pending',
                        attempts INTEGER NOT NULL DEFAULT 0,
                        next_attempt_at BIGINT NOT NULL,
                        last_status INTEGER,
                        last_error TEXT,
                        created_at BIGINT NOT NULL,
                        updated_at BIGINT NOT NULL
                    )
                    """
                )
                print('  webhook_deliveries         created')

            if not has_event_id:
                # Deliberately left NULLABLE. The obvious follow-up —
                # backfill then SET NOT NULL — would break the OLD image,
                # whose insert does not name this column: every webhook log
                # write would raise NotNullViolation, and the delivery path
                # swallows that exception, so the entire attempt log would
                # vanish with no signal. That would make the rollback this
                # script advertises a silent data-loss event. Readers fall
                # back to uuid, so a nullable column costs nothing.
                cursor.execute('ALTER TABLE webhook_events ADD COLUMN event_id UUID')
                cursor.execute('UPDATE webhook_events SET event_id = uuid WHERE event_id IS NULL')
                print(f'  webhook_events.event_id    added, {cursor.rowcount} row(s) backfilled')

            if not has_attempt:
                cursor.execute(
                    'ALTER TABLE webhook_events ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1'
                )
                print('  webhook_events.attempt     added (existing rows are attempt 1)')

            conn.commit()

        # Indexes are built OUTSIDE the transaction and CONCURRENTLY, which
        # Postgres refuses to do inside one. A plain CREATE INDEX would hold
        # ACCESS EXCLUSIVE for the whole build, and webhook_events is written
        # by every delivery — on a large table that blocks live traffic for
        # the duration.
        conn.autocommit = True
        with conn.cursor() as cursor:
            for name, table, definition in INDEXES:
                # A failed CREATE INDEX CONCURRENTLY leaves an INVALID index
                # behind, and IF NOT EXISTS then sees the name and skips it
                # forever — the index is never built and the script reports
                # success. Drop the corpse first so a re-run really repairs.
                if index_exists(cursor, name) and not index_is_valid(cursor, name):
                    print(f'  {name}: invalid from an earlier run, rebuilding')
                    cursor.execute(f'DROP INDEX CONCURRENTLY IF EXISTS {name}')

                cursor.execute(
                    f'CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} '
                    f'ON {table}{definition}'
                )
        print('  indexes                    created concurrently')

        print()
        print('Migration committed. Rotate to the new image next.')
    except Exception as error:
        # No blanket rollback message: by the time indexes are being built,
        # autocommit is on and the table/column DDL is already committed.
        # Claiming "rolled back" would send an operator back to square one
        # on a migration that is mostly done.
        if not conn.autocommit:
            conn.rollback()
            print(f'\nFAILED, rolled back — no changes were made: {error}')
        else:
            print(f'\nFAILED during index creation: {error}')
            print('The table and column changes ARE committed. Re-run this '
                  'script; it will rebuild only the missing indexes.')
        sys.exit(1)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
