"""
Tenant API key middleware for protecting public auth endpoints.

Tenant frontends must hold their site's tenant_api_key server-side and
forward it to Aegis as the X-Tenant-Api-Key header on all calls to the
gated public auth routes (register, login, password reset, etc.).
"""
import hmac
from functools import wraps
from flask import request, jsonify
from database import db_manager


# Public so handlers gated by this decorator can return the same 401 body
# from their own failure paths (e.g. cross-tenant probes), preserving the
# anti-enumeration property across the middleware/handler boundary.
TENANT_API_KEY_ERROR_BODY = {'error': 'Invalid or missing tenant API key'}
TENANT_API_KEY_ERROR_STATUS = 401

_UNIFORM_ERROR = (TENANT_API_KEY_ERROR_BODY, TENANT_API_KEY_ERROR_STATUS)


def require_tenant_api_key(func):
    """
    Decorator that gates a route on the X-Tenant-Api-Key header.

    Reads `site_id` from the JSON body (POST routes) or from `view_args`
    (GET routes with `<int:site_id>` in the path), looks up the site, and
    compares the supplied header value against the stored tenant_api_key
    using constant-time comparison. Any failure (missing header, missing
    site_id, unknown site, mismatch) returns the same 401 error body so
    response shape can't be used to distinguish failure modes. Note that
    response timing is *not* guaranteed equivalent — the early-return
    paths skip the DB lookup. The threat model is automated abuse, not
    nation-state timing analysis.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        supplied_key = request.headers.get('X-Tenant-Api-Key')
        if not supplied_key:
            return jsonify(_UNIFORM_ERROR[0]), _UNIFORM_ERROR[1]

        body = request.get_json(silent=True) or {}
        site_id = body.get('site_id')
        if not isinstance(site_id, int):
            site_id = (request.view_args or {}).get('site_id')
        if not isinstance(site_id, int):
            return jsonify(_UNIFORM_ERROR[0]), _UNIFORM_ERROR[1]

        site = db_manager.find_site_by_id(site_id)
        if site is None or not site.tenant_api_key:
            return jsonify(_UNIFORM_ERROR[0]), _UNIFORM_ERROR[1]

        if not hmac.compare_digest(supplied_key, site.tenant_api_key):
            return jsonify(_UNIFORM_ERROR[0]), _UNIFORM_ERROR[1]

        return func(*args, **kwargs)

    return wrapper
