"""
Admin endpoint to list users for the authenticated admin's site.
"""
from flask import Blueprint, jsonify, request
from database import db_manager
from byteforge_aegis_models import UserRole
from schemas.auth_schemas import UserResponseSchema
from utils.role_middleware import require_role

admin_list_users_bp = Blueprint('admin_list_users', __name__)


@admin_list_users_bp.route('/api/admin/users', methods=['GET'])
@require_role(UserRole.ADMIN)
def admin_list_users():
    """
    List all users for the authenticated admin's site.

    Requires Bearer token authentication with admin role.
    Automatically scopes to the admin's site_id.

    Headers:
        Authorization: Bearer <token>

    Returns:
        200: List of users for the admin's site
        401: Missing or invalid token
        403: User does not have admin role
    """
    site_id = request.user.site_id
    users = db_manager.list_users_by_site(site_id)
    schema = UserResponseSchema(many=True)
    return jsonify(schema.dump(users)), 200
