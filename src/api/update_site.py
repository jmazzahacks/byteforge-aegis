"""
Update site endpoint.
"""
from flask import Blueprint, jsonify
import time
from database import db_manager
from schemas.site_schemas import UpdateSiteRequestSchema, SiteResponseSchema
from services.webhook_service import webhook_service
from utils.validators import validate_request
from utils.api_key_middleware import require_master_api_key

update_site_bp = Blueprint('update_site', __name__)


@update_site_bp.route('/api/sites/<int:site_id>', methods=['PUT'])
@require_master_api_key
@validate_request(UpdateSiteRequestSchema)
def update_site(validated_data, site_id):
    """
    Update a site.

    Requires master API key (X-API-Key header).

    Path parameters:
        site_id: Site ID

    Request body (all fields optional):
        name: Site name
        domain: Site domain
        frontend_url: Frontend URL
        email_from: Email from address
        email_from_name: Email from name

    Returns:
        200: Site updated successfully
        400: Validation error or duplicate domain
        401: Missing or invalid API key
        404: Site not found
    """
    # Check if any fields were provided
    if not validated_data:
        return jsonify({'error': 'At least one field must be provided'}), 400

    # Find existing site
    site = db_manager.find_site_by_id(site_id)
    if site is None:
        return jsonify({'error': 'Site not found'}), 404

    # Update only provided fields
    if 'name' in validated_data:
        site.name = validated_data['name']
    if 'domain' in validated_data:
        site.domain = validated_data['domain']
    if 'frontend_url' in validated_data:
        site.frontend_url = validated_data['frontend_url']
    if 'verification_redirect_url' in validated_data:
        site.verification_redirect_url = validated_data['verification_redirect_url']
    if 'email_from' in validated_data:
        site.email_from = validated_data['email_from']
    if 'email_from_name' in validated_data:
        site.email_from_name = validated_data['email_from_name']
    if 'webhook_url' in validated_data:
        site.webhook_url = validated_data['webhook_url']
        if site.webhook_url:
            # Generate new secret when webhook URL is set or changed
            site.webhook_secret = webhook_service.generate_webhook_secret()
        else:
            # Clear secret when webhook URL is removed
            site.webhook_secret = None
    if validated_data.get('regenerate_webhook_secret') and site.webhook_url:
        site.webhook_secret = webhook_service.generate_webhook_secret()

    # Update timestamp
    site.updated_at = int(time.time())

    # Save to database
    try:
        updated_site = db_manager.update_site(site)
        schema = SiteResponseSchema()
        return jsonify(schema.dump(updated_site)), 200
    except Exception as e:
        if 'duplicate' in str(e).lower() or 'unique' in str(e).lower():
            return jsonify({'error': 'Domain already exists'}), 400
        return jsonify({'error': str(e)}), 500
