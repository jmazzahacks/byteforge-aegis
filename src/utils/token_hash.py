"""
At-rest representation of bearer tokens.
"""
import hashlib


def token_digest(token: str) -> str:
    """
    The value stored in the database for a bearer token.

    Tokens used to be stored verbatim, which made any read of auth_tokens or
    refresh_tokens — a backup, a replica snapshot, a query in a slow-query
    log, SQL injection elsewhere — immediate takeover of every live session
    across every tenant, with no cracking step. Passwords were bcrypted
    while the credential we issue sat in the clear.

    Plain SHA-256, deliberately: unlike a password there is nothing to
    stretch against. The token is 32 bytes from secrets.token_urlsafe, so
    there is no dictionary and no guessing — bcrypt here would add latency
    to every authenticated request and buy nothing.

    The lookup stays a single indexed equality match: hash what the caller
    presented, compare against the stored digest.
    """
    return hashlib.sha256(token.encode('utf-8')).hexdigest()
