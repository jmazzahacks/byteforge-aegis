"""
UUIDv7 generation.

UUIDv7 is time-ordered: the leading 48 bits are a Unix millisecond
timestamp, which gives index locality for primary keys (new rows append to
the end of the B-tree instead of scattering like random UUIDv4). We generate
it in Python because PostgreSQL cannot produce UUIDv7 natively before PG18.

The millisecond timestamp is derived from time.time(); it is an internal
index-ordering property, not a stored temporal field (those remain unix
timestamps in dedicated created_at/updated_at columns).
"""
import os
import time
import uuid


def generate_uuid7() -> str:
    """Generate a time-ordered UUIDv7 and return it as a canonical string."""
    unix_ms = int(time.time() * 1000)
    timestamp_bytes = unix_ms.to_bytes(6, byteorder='big')
    random_bytes = os.urandom(10)

    raw = bytearray(timestamp_bytes + random_bytes)
    # Set the version nibble (byte 6, high nibble) to 7.
    raw[6] = (raw[6] & 0x0F) | 0x70
    # Set the variant bits (byte 8, two high bits) to 0b10.
    raw[8] = (raw[8] & 0x3F) | 0x80

    return str(uuid.UUID(bytes=bytes(raw)))
