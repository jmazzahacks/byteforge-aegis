"""
Tenant API key middleware for protecting public auth endpoints.

Tenant frontends must hold their site's tenant_api_key server-side and
forward it to Aegis as the X-Tenant-Api-Key header on all calls to the
gated public auth routes (register, login, password reset, etc.).
"""
from functools import wraps
from flask import g, request, jsonify
from utils.identifiers import resolve_site
from utils.secret_compare import constant_time_equals


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
    (GET routes with `<site_id>` in the path) — as a site UUID —
    resolves the site, and compares the supplied header value
    against the stored tenant_api_key using constant-time comparison. Any
    failure (missing header, missing site_id, unknown site, mismatch) returns
    the same 401 error body so response shape can't be used to distinguish
    failure modes. Note that response timing is *not* guaranteed equivalent —
    the early-return paths skip the DB lookup. The threat model is automated
    abuse, not nation-state timing analysis.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        supplied_key = request.headers.get('X-Tenant-Api-Key')
        if not supplied_key:
            return jsonify(_UNIFORM_ERROR[0]), _UNIFORM_ERROR[1]

        # The path wins, and disagreement is fatal. Preferring the body let a
        # caller authenticate against their own site while a handler that
        # re-derived the site from the path authorized against someone else's
        # — a cross-tenant read with only one tenant's key. Flask parses a
        # JSON body on GET too, so the two really can differ.
        path_site_id = (request.view_args or {}).get('site_id')
        body = request.get_json(silent=True) or {}
        body_site_id = body.get('site_id')

        if path_site_id is not None and body_site_id is not None \
                and body_site_id != path_site_id:
            return jsonify(_UNIFORM_ERROR[0]), _UNIFORM_ERROR[1]

        site_identifier = path_site_id if path_site_id is not None else body_site_id

        site = resolve_site(site_identifier)
        if site is None or not site.tenant_api_key:
            return jsonify(_UNIFORM_ERROR[0]), _UNIFORM_ERROR[1]

        if not constant_time_equals(supplied_key, site.tenant_api_key):
            return jsonify(_UNIFORM_ERROR[0]), _UNIFORM_ERROR[1]

        # Handlers should authorize against the site we actually
        # authenticated, rather than resolving an identifier a second time
        # and risking a fresh disagreement.
        g.tenant_site = site

        return func(*args, **kwargs)

    return wrapper
