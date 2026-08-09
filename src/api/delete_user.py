"""
Delete user endpoint.
"""
import time

from flask import Blueprint, jsonify
from byteforge_aegis_models import UserRole, WebhookEventType, WebhookPayload
from database import db_manager
from services.webhook_service import webhook_service
from utils.api_key_middleware import require_master_api_key
from utils.identifiers import resolve_user
from utils.uuid7 import generate_uuid7

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
        409: Deletion refused — user is deletion-protected, or is the last
             admin of their site. The 'code' field distinguishes them.
    """
    # Check if user exists first
    user = resolve_user(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    # Deletion protection is checked first: it is an explicit operator
    # decision about this account (or its whole tenant), so it should be the
    # reported reason even when another guard would also refuse.
    # Fail closed: users.site_uuid is NOT NULL REFERENCES sites(uuid), so a
    # missing site means the DB is in a state we don't understand — refuse
    # rather than fall through to the delete.
    site = db_manager.find_site_by_uuid(user.site_uuid)
    if not site:
        return jsonify({'error': 'Site for this user could not be resolved'}), 500

    if site.deletion_protected:
        return jsonify({
            'error': 'This site is protected from deletion; none of its users can be deleted',
            'code': 'site_deletion_protected'
        }), 409

    if user.deletion_protected:
        return jsonify({
            'error': 'User is protected from deletion',
            'code': 'user_deletion_protected'
        }), 409

    # Refuse to orphan a site's admin access: never delete its last admin.
    if user.role == UserRole.ADMIN and db_manager.count_site_admins(user.site_uuid) <= 1:
        return jsonify({
            'error': 'Cannot delete the last admin of a site',
            'code': 'last_site_admin'
        }), 409

    deleted = db_manager.delete_user(user.uuid)
    if deleted:
        # Notify the tenant site so it can clean up mirror rows keyed on
        # this user's uuid. The outbox row is written here so the event
        # survives a crash; only the POST is backgrounded. `site` was
        # already resolved above for the protection guard.
        webhook_payload = WebhookPayload(
            event_id=generate_uuid7(),
            event_type=WebhookEventType.USER_DELETED,
            site_uuid=user.site_uuid,
            user_uuid=user.uuid,
            email=user.email,
            aegis_role=user.role.value,
            timestamp=int(time.time())
        )
        webhook_service.send_webhook(site, webhook_payload)
        return jsonify({'message': f'User {user.uuid} deleted successfully'}), 200
    else:
        return jsonify({'error': 'Failed to delete user'}), 500
