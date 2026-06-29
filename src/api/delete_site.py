"""
Delete site endpoint.
"""
from flask import Blueprint, jsonify
from database import db_manager
from utils.api_key_middleware import require_master_api_key
from utils.identifiers import resolve_site

delete_site_bp = Blueprint('delete_site', __name__)


@delete_site_bp.route('/api/sites/<site_id>', methods=['DELETE'])
@require_master_api_key
def delete_site(site_id: str):
    """
    Delete a site and ALL of its data (users, tokens, etc.).

    Requires master API key (X-API-Key header). This is irreversible: every
    dependent record for the tenant is removed via ON DELETE CASCADE.

    Path parameters:
        site_id: Site identifier to delete (integer id or UUID)

    Returns:
        200: Site deleted successfully
        401: Missing or invalid API key
        404: Site not found
        500: Deletion failed
    """
    # Check if site exists first (by integer id or UUID)
    site = resolve_site(site_id)
    if not site:
        return jsonify({'error': 'Site not found'}), 404

    deleted = db_manager.delete_site(site.id)
    if deleted:
        return jsonify({'message': f'Site {site.id} deleted successfully'}), 200
    else:
        return jsonify({'error': 'Failed to delete site'}), 500
