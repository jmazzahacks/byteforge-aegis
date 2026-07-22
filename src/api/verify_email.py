"""
Email verification endpoint.
"""
from flask import Blueprint, jsonify
from services.auth_service import auth_service
from schemas.auth_schemas import UserResponseSchema, VerifyEmailRequestSchema
from utils.validators import validate_request
from utils.tenant_api_key_middleware import require_tenant_api_key

verify_email_bp = Blueprint('verify_email', __name__)


@verify_email_bp.route('/api/auth/verify-email', methods=['POST'])
@require_tenant_api_key
@validate_request(VerifyEmailRequestSchema)
def verify_email(validated_data: dict):
    """
    Verify a user's email address.

    For admin-created users (no password set), password must be provided.
    For self-registered users, password is optional/ignored.

    Request body:
        token: Email verification token
        password: Optional password (required for admin-created users)

    Returns:
        200: Email verified successfully with user data and redirect_url
        400: Invalid or expired token, or password required but not provided
    """
    try:
        result = auth_service.verify_email(
            token=validated_data['token'],
            site_uuid=validated_data['site_id'],
            password=validated_data.get('password')
        )
        # Dump the user through the response schema — result.to_dict() would
        # serialize the backend User model, which carries password_hash.
        return jsonify({
            'user': UserResponseSchema().dump(result.user),
            'redirect_url': result.redirect_url,
        }), 200
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
