"""
Tenant-key-gated single-user lookup endpoint.

Allows a tenant backend to resolve an Aegis user_id to a user record
(including role) for server-to-server authorization checks. Scoped to
the tenant's own site by the tenant API key.
"""
from flask import Blueprint, jsonify

from schemas.auth_schemas import UserResponseSchema
from utils.identifiers import resolve_site, resolve_user
from utils.tenant_api_key_middleware import (
    TENANT_API_KEY_ERROR_BODY,
    TENANT_API_KEY_ERROR_STATUS,
    require_tenant_api_key,
)


get_user_bp = Blueprint('get_user', __name__)


@get_user_bp.route('/api/sites/<site_id>/users/<user_id>', methods=['GET'])
@require_tenant_api_key
def get_user_by_id(site_id: str, user_id: str):
    """
    Get a single user by id, scoped to the requesting tenant's site.

    Requires X-Tenant-Api-Key header matching the site in the path. Both
    site_id and user_id may be supplied as an integer id or a UUID.

    Path parameters:
        site_id: Site identifier (must match the supplied tenant API key)
        user_id: User identifier to look up

    Returns:
        200: User record (id, uuid, site_id, site_uuid, email, role, timestamps)
        401: Missing/invalid tenant API key, unknown user, or user belongs
             to a different site (uniform body to prevent enumeration)
    """
    site = resolve_site(site_id)
    user = resolve_user(user_id)
    if user is None or site is None or user.site_id != site.id:
        return jsonify(TENANT_API_KEY_ERROR_BODY), TENANT_API_KEY_ERROR_STATUS

    schema = UserResponseSchema()
    return jsonify(schema.dump(user)), 200
