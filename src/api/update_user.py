"""
Admin user update endpoint.

Currently exposes deletion protection only — the flag that makes the admin
delete path refuse. Requested by tenants whose downstream records custody
real value that would become unattributable if the Aegis identity vanished.
"""
import logging
import time

from flask import Blueprint, jsonify

from database import db_manager
from schemas.auth_schemas import UpdateUserRequestSchema, UserResponseSchema
from utils.api_key_middleware import require_master_api_key
from utils.identifiers import resolve_user
from utils.validators import validate_request

logger = logging.getLogger(__name__)

update_user_bp = Blueprint('update_user', __name__)


@update_user_bp.route('/api/admin/users/<user_id>', methods=['PATCH'])
@require_master_api_key
@validate_request(UpdateUserRequestSchema)
def update_user(validated_data: dict, user_id: str):
    """
    Update admin-controlled fields on a user.

    Requires master API key (X-API-Key header).

    Path parameters:
        user_id: User UUID to update

    Request body:
        deletion_protected: When true, DELETE on this user is refused with
            409 / code 'user_deletion_protected'. Clearing the flag is a
            deliberate, separately-auditable step before deletion.

    Returns:
        200: Updated user
        400: Invalid request body
        401: Missing or invalid API key
        404: User not found
    """
    user = resolve_user(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    previous = user.deletion_protected
    user.deletion_protected = validated_data['deletion_protected']
    user.updated_at = int(time.time())
    updated_user = db_manager.update_user(user)

    # Logged so clearing protection leaves a trace — the protection is only
    # meaningful if unprotect-then-delete is reconstructible after the fact.
    logger.info(
        "Deletion protection changed for user %s (site %s): %s -> %s",
        user.uuid, user.site_uuid, previous, updated_user.deletion_protected
    )

    return jsonify(UserResponseSchema().dump(updated_user)), 200
