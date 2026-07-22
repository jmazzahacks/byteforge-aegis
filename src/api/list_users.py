"""
List users for a site endpoint.
"""
from flask import Blueprint, jsonify, request
from database import db_manager
from schemas.auth_schemas import UserResponseSchema
from utils.api_key_middleware import require_master_api_key
from utils.identifiers import resolve_site

list_users_bp = Blueprint('list_users', __name__)


@list_users_bp.route('/api/sites/<site_id>/users', methods=['GET'])
@require_master_api_key
def list_users_by_site_id(site_id: str):
    """
    List all users for a site by UUID.

    Requires master API key (X-API-Key header).

    Path parameters:
        site_id: Site UUID

    Returns:
        200: List of users
        401: Missing or invalid API key
        404: Site not found
    """
    site = resolve_site(site_id)
    if site is None:
        return jsonify({'error': 'Site not found'}), 404

    users = db_manager.list_users_by_site(site.uuid)
    schema = UserResponseSchema(many=True)
    return jsonify(schema.dump(users)), 200


@list_users_bp.route('/api/sites/by-domain/users', methods=['GET'])
@require_master_api_key
def list_users_by_domain():
    """
    List all users for a site by domain.

    Requires master API key (X-API-Key header).

    Query parameters:
        domain: Site domain

    Returns:
        200: List of users
        400: Missing domain parameter
        401: Missing or invalid API key
        404: Site not found
    """
    domain = request.args.get('domain')

    if not domain:
        return jsonify({'error': 'Domain parameter is required'}), 400

    site = db_manager.find_site_by_domain(domain)
    if site is None:
        return jsonify({'error': 'Site not found'}), 404

    users = db_manager.list_users_by_site(site.uuid)
    schema = UserResponseSchema(many=True)
    return jsonify(schema.dump(users)), 200
