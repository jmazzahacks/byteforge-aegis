"""
Rate limiting for credential endpoints.
"""
from flask import request
from flask_limiter import Limiter

# Limits are keyed on (site, email) from the request body rather than on the
# client IP. That is deliberate: this service runs behind zeus's nginx behind
# Cloudflare, so request.remote_addr is a proxy, and X-Forwarded-For is only
# trustworthy if every hop is known to rewrite it. Trusting it blindly lets an
# attacker rotate the header to get a fresh bucket per request; not trusting
# it collapses every caller into one bucket, which is a self-inflicted denial
# of service. Keying on the account being attacked avoids the question
# entirely and targets the abuse that matters — credential stuffing against
# one account, and reset-mail flooding of one address.
#
# What this does NOT stop is a spread attack: one guess each against ten
# thousand accounts stays under a per-account limit. Catching that needs a
# per-IP limit, which needs the proxy chain confirmed first.


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
