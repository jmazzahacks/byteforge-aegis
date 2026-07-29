"""
Constant-time comparison for secrets that arrive as request headers.
"""
import hmac


def constant_time_equals(supplied: str, stored: str) -> bool:
    """
    Compare an attacker-supplied secret against a stored one.

    Encodes both sides to bytes first. `hmac.compare_digest` raises
    TypeError when handed a `str` containing non-ASCII, and header values
    are attacker-controlled — so passing them in directly turns a single
    high byte into an unhandled 500. That is a denial of service on every
    gated route, and worse on the tenant gate, where the site is resolved
    before the comparison: the exception fires only when the site exists,
    which hands an unauthenticated caller a reliable "is this a real site?"
    oracle and defeats the uniform-error design.

    Comparing bytes keeps the timing property and removes the crash. This
    is the same defect that was fixed in the webhook signature verifier;
    it lived here too.
    """
    if supplied is None or stored is None:
        return False

    return hmac.compare_digest(supplied.encode('utf-8'), stored.encode('utf-8'))
