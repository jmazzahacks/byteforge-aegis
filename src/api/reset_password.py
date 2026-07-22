"""
Reset password endpoint.
"""
from flask import Blueprint, jsonify
from services.auth_service import auth_service
from schemas.auth_schemas import ResetPasswordRequestSchema, UserResponseSchema
from utils.validators import validate_request
from utils.tenant_api_key_middleware import require_tenant_api_key

reset_password_bp = Blueprint('reset_password', __name__)


@reset_password_bp.route('/api/auth/reset-password', methods=['POST'])
@require_tenant_api_key
@validate_request(ResetPasswordRequestSchema)
def reset_password(validated_data):
    """
    Reset password using a reset token.

    Request body:
        token: Password reset token
        new_password: New password (min 8 characters)

    Returns:
        200: Password reset successfully
        400: Invalid or expired token
    """
    try:
        user = auth_service.reset_password(
            token=validated_data['token'],
            site_uuid=validated_data['site_id'],
            new_password=validated_data['new_password']
        )
        schema = UserResponseSchema()
        return jsonify(schema.dump(user)), 200
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
