"""
Delete user endpoint.
"""
from flask import Blueprint, jsonify
from byteforge_aegis_models import UserRole
from database import db_manager
from utils.api_key_middleware import require_master_api_key
from utils.identifiers import resolve_user

delete_user_bp = Blueprint('delete_user', __name__)


@delete_user_bp.route('/api/admin/users/<user_id>', methods=['DELETE'])
@require_master_api_key
def delete_user(user_id: str):
    """
    Delete a user and all associated data.

    Requires master API key (X-API-Key header).

    Path parameters:
        user_id: User UUID to delete

    Returns:
        200: User deleted successfully
        401: Missing or invalid API key
        404: User not found
        409: User is the last admin of their site
    """
    # Check if user exists first
    user = resolve_user(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    # Refuse to orphan a site's admin access: never delete its last admin.
    if user.role == UserRole.ADMIN and db_manager.count_site_admins(user.site_uuid) <= 1:
        return jsonify({'error': 'Cannot delete the last admin of a site'}), 409

    deleted = db_manager.delete_user(user.uuid)
    if deleted:
        return jsonify({'message': f'User {user.uuid} deleted successfully'}), 200
    else:
        return jsonify({'error': 'Failed to delete user'}), 500
