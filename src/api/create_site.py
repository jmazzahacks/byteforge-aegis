"""
Create site endpoint.
"""
import logging

from flask import Blueprint, jsonify
import time
from database import db_manager
from byteforge_aegis_models import Site
from schemas.site_schemas import CreateSiteRequestSchema, SiteResponseSchema
from services.webhook_service import webhook_service
from services.tenant_key_service import tenant_key_service
from utils.validators import validate_request
from utils.api_key_middleware import require_master_api_key

logger = logging.getLogger(__name__)

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
        name=validated_data['name'],
        domain=validated_data['domain'],
        frontend_url=validated_data['frontend_url'],
        email_from=validated_data['email_from'],
        email_from_name=validated_data['email_from_name'],
        created_at=current_time,
        updated_at=current_time,
        verification_redirect_url=validated_data.get('verification_redirect_url'),
        allow_self_registration=validated_data.get('allow_self_registration', True),
        webhook_url=webhook_url,
        webhook_secret=webhook_secret,
        tenant_api_key=tenant_key_service.generate_tenant_api_key(),
        mailgun_domain=validated_data.get('mailgun_domain'),
        mailgun_api_key=validated_data.get('mailgun_api_key'),
    )

    try:
        created_site = db_manager.create_site(site)
        schema = SiteResponseSchema()
        return jsonify(schema.dump(created_site)), 201
    except Exception as e:
        if 'duplicate' in str(e).lower() or 'unique' in str(e).lower():
            return jsonify({'error': 'Domain already exists'}), 400
        # Log the detail, return a generic message. str(e) here is psycopg2's
        # text — constraint names, table and column names, sometimes a
        # fragment of the failing statement. The audience is master-key
        # holders, so this is not a disclosure to strangers, but there is no
        # reason to hand out schema internals when the log has them.
        logger.exception('Unexpected error creating site')
        return jsonify({'error': 'Failed to create site'}), 500
