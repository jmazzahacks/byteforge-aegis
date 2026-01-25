"""
Admin user registration endpoint.
"""
from flask import Blueprint, jsonify
from services.auth_service import auth_service
from models.user_role import UserRole
from schemas.auth_schemas import AdminRegisterRequestSchema, UserResponseSchema
from utils.validators import validate_request
from utils.api_key_middleware import require_master_api_key

admin_register_bp = Blueprint('admin_register', __name__)


@admin_register_bp.route('/api/admin/register', methods=['POST'])
@require_master_api_key
@validate_request(AdminRegisterRequestSchema)
def admin_register(validated_data: dict):
    """
    Register a new user for a site (admin-initiated).

    Requires master API key (X-API-Key header).

    The user will receive a verification email and must set their own password
    when clicking the verification link. Admins cannot set passwords for users.

    Request body:
        site_id: ID of the site
        email: User email
        role: Optional role ('user' or 'admin', defaults to 'user')

    Returns:
        201: User created successfully, verification email sent
        400: Validation error or duplicate email
        401: Missing or invalid API key
    """
    try:
        # Get role from request, default to 'user' for security
        role_str = validated_data.get('role', 'user')
        role = UserRole.ADMIN if role_str == 'admin' else UserRole.USER

        user = auth_service.register_user(
            site_id=validated_data['site_id'],
            email=validated_data['email'],
            password=None,  # User will set password via email verification
            role=role,
            is_admin_registration=True  # Bypass allow_self_registration check
        )
        schema = UserResponseSchema()
        return jsonify(schema.dump(user)), 201
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
