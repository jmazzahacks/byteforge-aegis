"""
Check verification token status endpoint.
"""
from flask import Blueprint, jsonify
from services.auth_service import auth_service
from schemas.auth_schemas import CheckVerificationTokenSchema
from utils.validators import validate_request
from utils.tenant_api_key_middleware import require_tenant_api_key

check_verification_token_bp = Blueprint('check_verification_token', __name__)


@check_verification_token_bp.route('/api/auth/check-verification-token', methods=['POST'])
@require_tenant_api_key
@validate_request(CheckVerificationTokenSchema)
def check_verification_token(validated_data: dict):
    """
    Check verification token status without consuming it.

    Used by frontend to determine if password form should be shown.

    Request body:
        token: Email verification token

    Returns:
        200: Token is valid, returns password_required and email
        400: Invalid or expired token
    """
    try:
        result = auth_service.check_verification_token(
            token=validated_data['token'],
            site_uuid=validated_data['site_id'],
        )
        return jsonify(result.to_dict()), 200
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
