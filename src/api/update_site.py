"""
Update site endpoint.
"""
from flask import Blueprint, jsonify
import time
from database import db_manager
from schemas.site_schemas import UpdateSiteRequestSchema, SiteResponseSchema
from services.webhook_service import webhook_service
from services.tenant_key_service import tenant_key_service
from utils.validators import validate_request
from utils.api_key_middleware import require_master_api_key
from utils.identifiers import resolve_site

update_site_bp = Blueprint('update_site', __name__)


@update_site_bp.route('/api/sites/<site_id>', methods=['PUT'])
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

    # Find existing site (by integer id or UUID)
    site = resolve_site(site_id)
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
    if 'allow_self_registration' in validated_data:
        site.allow_self_registration = validated_data['allow_self_registration']
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
    if validated_data.get('regenerate_tenant_api_key'):
        site.tenant_api_key = tenant_key_service.generate_tenant_api_key()
    if 'mailgun_domain' in validated_data:
        site.mailgun_domain = validated_data['mailgun_domain']
    if 'mailgun_api_key' in validated_data:
        site.mailgun_api_key = validated_data['mailgun_api_key']

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
