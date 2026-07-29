"""
Canonical form for email addresses.
"""
from typing import Optional


def normalize_email(email: Optional[str]) -> Optional[str]:
    """
    Lowercase and strip an address so one mailbox is one account.

    Matching used to be exact and UNIQUE(site_uuid, email) is case
    sensitive, so Victim@example.com and victim@example.com were two
    separate accounts delivering to the same real mailbox. That let an
    attacker register a case-variant of an address that already existed —
    registration's duplicate check missed it, so they got a genuine account
    that a downstream tenant app keying on email would likely conflate with
    the original. It also meant a user who typed their own address with
    different capitalisation got "invalid credentials" on login and a silent
    no-op on password reset.

    Only the address as a whole is lowercased. The local part is technically
    case-sensitive per RFC 5321, but no mail provider in practice treats it
    that way, and honouring the spec here is what created the bug.
    """
    if email is None:
        return None

    return email.strip().lower()
