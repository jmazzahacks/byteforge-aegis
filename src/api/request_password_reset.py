"""
Request password reset endpoint.
"""
from flask import Blueprint, jsonify
from services.auth_service import auth_service
from schemas.auth_schemas import RequestPasswordResetSchema
from utils.validators import validate_request
from utils.tenant_api_key_middleware import require_tenant_api_key

request_password_reset_bp = Blueprint('request_password_reset', __name__)


@request_password_reset_bp.route('/api/auth/request-password-reset', methods=['POST'])
@require_tenant_api_key
@validate_request(RequestPasswordResetSchema)
def request_password_reset(validated_data):
    """
    Request a password reset token.

    Request body:
        site_id: ID of the site
        email: User email

    Returns:
        200: Password reset token sent (always returns 200 for security)
    """
    try:
        auth_service.request_password_reset(
            site_id=validated_data['site_id'],
            email=validated_data['email']
        )
    except ValueError:
        pass  # Don't reveal if email exists

    return jsonify({'message': 'If the email exists, a password reset link has been sent'}), 200
