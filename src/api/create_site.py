"""
Create site endpoint.
"""
from flask import Blueprint, jsonify
import time
from database import db_manager
from byteforge_aegis_models import Site
from schemas.site_schemas import CreateSiteRequestSchema, SiteResponseSchema
from services.webhook_service import webhook_service
from utils.validators import validate_request
from utils.api_key_middleware import require_master_api_key

create_site_bp = Blueprint('create_site', __name__)


@create_site_bp.route('/api/sites', methods=['POST'])
@require_master_api_key
@validate_request(CreateSiteRequestSchema)
def create_site(validated_data):
    """
    Create a new site.

    Requires master API key (X-API-Key header).

    Request body:
        name: Site name
        domain: Site domain (must be unique)

    Returns:
        201: Site created successfully
        400: Validation error or duplicate domain
        401: Missing or invalid API key
    """
    current_time = int(time.time())

    webhook_url = validated_data.get('webhook_url')
    webhook_secret = webhook_service.generate_webhook_secret() if webhook_url else None

    site = Site(
        id=0,
        name=validated_data['name'],
        domain=validated_data['domain'],
        frontend_url=validated_data['frontend_url'],
        email_from=validated_data['email_from'],
        email_from_name=validated_data['email_from_name'],
        created_at=current_time,
        updated_at=current_time,
        verification_redirect_url=validated_data.get('verification_redirect_url'),
        webhook_url=webhook_url,
        webhook_secret=webhook_secret
    )

    try:
        created_site = db_manager.create_site(site)
        schema = SiteResponseSchema()
        return jsonify(schema.dump(created_site)), 201
    except Exception as e:
        if 'duplicate' in str(e).lower() or 'unique' in str(e).lower():
            return jsonify({'error': 'Domain already exists'}), 400
        return jsonify({'error': str(e)}), 500
