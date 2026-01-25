"""
List all sites endpoint.
"""
from flask import Blueprint, jsonify
from database import db_manager
from schemas.site_schemas import SiteResponseSchema
from utils.api_key_middleware import require_master_api_key

list_sites_bp = Blueprint('list_sites', __name__)


@list_sites_bp.route('/api/sites', methods=['GET'])
@require_master_api_key
def list_sites():
    """
    List all sites.

    Requires master API key (X-API-Key header).

    Returns:
        200: List of all sites
        401: Missing or invalid API key
    """
    # Get all sites from database
    with db_manager.get_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, name, domain, frontend_url, verification_redirect_url, email_from, email_from_name, created_at, updated_at, allow_self_registration
            FROM sites
            ORDER BY created_at DESC
            """
        )
        rows = cursor.fetchall()

    # Convert to Site objects and serialize
    from models.site import Site
    sites = [Site.from_dict(row) for row in rows]
    schema = SiteResponseSchema(many=True)

    return jsonify(schema.dump(sites)), 200
