"""
Tenant-key-gated single-user lookup endpoint.

Allows a tenant backend to resolve an Aegis user_id to a user record
(including role) for server-to-server authorization checks. Scoped to
the tenant's own site by the tenant API key.
"""
from flask import Blueprint, jsonify

from database import db_manager
from schemas.auth_schemas import UserResponseSchema
from utils.tenant_api_key_middleware import (
    TENANT_API_KEY_ERROR_BODY,
    TENANT_API_KEY_ERROR_STATUS,
    require_tenant_api_key,
)


get_user_bp = Blueprint('get_user', __name__)


@get_user_bp.route('/api/sites/<int:site_id>/users/<int:user_id>', methods=['GET'])
@require_tenant_api_key
def get_user_by_id(site_id: int, user_id: int):
    """
    Get a single user by ID, scoped to the requesting tenant's site.

    Requires X-Tenant-Api-Key header matching the site_id in the path.

    Path parameters:
        site_id: Site ID (must match the supplied tenant API key)
        user_id: User ID to look up

    Returns:
        200: User record (id, site_id, email, is_verified, role, timestamps)
        401: Missing/invalid tenant API key, unknown user, or user belongs
             to a different site (uniform body to prevent enumeration)
    """
    user = db_manager.find_user_by_id(user_id)
    if user is None or user.site_id != site_id:
        return jsonify(TENANT_API_KEY_ERROR_BODY), TENANT_API_KEY_ERROR_STATUS

    schema = UserResponseSchema()
    return jsonify(schema.dump(user)), 200
