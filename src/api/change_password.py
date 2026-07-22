"""
Change password endpoint.
"""
from flask import Blueprint, jsonify, request
from services.auth_service import auth_service
from schemas.auth_schemas import ChangePasswordRequestSchema, UserResponseSchema
from utils.validators import validate_request
from utils.auth_middleware import require_auth

change_password_bp = Blueprint('change_password', __name__)


@change_password_bp.route('/api/auth/change-password', methods=['POST'])
@require_auth
@validate_request(ChangePasswordRequestSchema)
def change_password(validated_data):
    """
    Change a user's password.

    Headers:
        Authorization: Bearer <token>

    Request body:
        old_password: Current password
        new_password: New password (min 8 characters)

    Returns:
        200: Password changed successfully
        400: Invalid old password or validation error
        401: Missing or invalid auth token
    """
    try:
        user = auth_service.change_password(
            user_uuid=request.user_uuid,
            old_password=validated_data['old_password'],
            new_password=validated_data['new_password']
        )
        schema = UserResponseSchema()
        return jsonify(schema.dump(user)), 200
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
