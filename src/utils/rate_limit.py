"""
Rate limiting for credential endpoints.
"""
import logging
from typing import Optional

from flask import request
from flask_limiter import Limiter

logger = logging.getLogger(__name__)

# Two independent keyings, because they catch different attacks and neither
# subsumes the other:
#
#   (site, email)  from the request body — catches credential stuffing against
#                  ONE account, and reset-mail flooding of one address. Needs
#                  no trust in any header.
#   client IP      from CF-Connecting-IP — catches a SPREAD attack, where one
#                  guess each against ten thousand accounts stays under every
#                  per-account limit. Requires a trustworthy client address,
#                  which took confirming the whole proxy chain to establish.
#
# Both apply to the credential endpoints. A request has to stay under both.


def site_email_key() -> str:
    """Bucket by the account being targeted, not the caller."""
    body = request.get_json(silent=True) or {}
    site_id = body.get('site_id') or 'unknown-site'
    email = body.get('email') or 'unknown-email'

    if isinstance(email, str):
        email = email.strip().lower()

    return f'{site_id}:{email}'


def site_key() -> str:
    """Bucket by site, for limits that cap damage across a whole tenant."""
    body = request.get_json(silent=True) or {}
    return str(body.get('site_id') or 'unknown-site')


# Set by zeus's nginx from Cloudflare's CF-Connecting-IP. This is the ONLY
# header carrying a trustworthy client address, and the reason is worth
# recording because the obvious alternatives are both traps:
#
#   X-Real-IP           is cloudflared's container address — nginx is not the
#                       edge, so this is the SAME value for every request in
#                       the world. Keying on it puts every caller in one
#                       bucket: a global outage wearing a security control's
#                       clothes.
#   X-Forwarded-For     is set with $proxy_add_x_forwarded_for, which APPENDS
#                       to whatever the client sent. Its leftmost entry — the
#                       one implementations reach for — is attacker-supplied,
#                       so rotating it mints a fresh bucket per request.
#
# Cloudflare strips any client-supplied CF-Connecting-IP before setting its
# own, and the backend binds only loopback and the tailnet with no tunnel
# ingress of its own, so nginx is the only route in. That is what makes this
# header unforgeable here — not the header itself, which is trivially
# spoofable on a service reachable directly.
CLIENT_IP_HEADER = 'CF-Connecting-IP'

_missing_ip_header_warned = False


def client_ip_key() -> Optional[str]:
    """
    Bucket by real client address, or None when we cannot know it.

    Returning None makes the limit skip rather than apply. That is the
    important choice: the tempting fallback is a constant like 'unknown',
    which silently merges every caller into one bucket and takes logins down
    for every tenant the moment the header goes missing. A missing header is
    a misconfiguration, and the safe response to a misconfigured control is
    to not enforce it — loudly.
    """
    global _missing_ip_header_warned

    client_ip = request.headers.get(CLIENT_IP_HEADER)
    if client_ip:
        return client_ip.strip()

    if not _missing_ip_header_warned:
        _missing_ip_header_warned = True
        logger.warning(
            '%s header absent — per-IP rate limiting is NOT in effect. '
            'Expected on requests proxied by nginx; if this appears in '
            'production the proxy_set_header line is missing.',
            CLIENT_IP_HEADER,
        )

    return None


limiter = Limiter(key_func=site_email_key)

# Credential stuffing against one account. bcrypt at cost 12 is ~100-250ms,
# so this also bounds how much CPU one account can consume.
LOGIN_LIMIT = '10 per minute'

# Registration creates rows and sends mail on both branches — the duplicate
# path mails a "sign-in attempt" notice, so it is an amplifier either way.
REGISTER_LIMIT = '5 per minute'

# Each accepted reset request sends one email on the TENANT's Mailgun domain,
# so flooding burns their sending reputation, not ours. Tight per address.
PASSWORD_RESET_LIMIT = '3 per 15 minutes'

# Per-tenant ceiling so an attacker holding a tenant key cannot walk a whole
# user list one address at a time and stay under the per-address limit.
PASSWORD_RESET_SITE_LIMIT = '60 per hour'


def client_ip_unavailable() -> bool:
    """Exempt the per-IP limits when there is no trustworthy address."""
    return client_ip_key() is None


# Per-IP ceilings. Set well above the per-account limits, because one address
# legitimately carries many users: an office, a university, a mobile carrier's
# NAT. These are sized to catch one attacker spraying many accounts, not to
# police normal shared egress — too tight and a whole building loses login.
LOGIN_IP_LIMIT = '60 per minute'
REGISTER_IP_LIMIT = '20 per hour'
PASSWORD_RESET_IP_LIMIT = '20 per hour'
