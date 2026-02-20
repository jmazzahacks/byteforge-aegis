"""
Tenant admin endpoint to register a new user for the admin's own site.
"""
from flask import Blueprint, jsonify, request
from services.auth_service import auth_service
from byteforge_aegis_models import UserRole
from schemas.auth_schemas import TenantAdminRegisterSchema, UserResponseSchema
from utils.validators import validate_request
from utils.role_middleware import require_role

admin_register_user_bp = Blueprint('admin_register_user', __name__)


@admin_register_user_bp.route('/api/admin/register-user', methods=['POST'])
@require_role(UserRole.ADMIN)
@validate_request(TenantAdminRegisterSchema)
def admin_register_user(validated_data: dict):
    """
    Register a new user for the authenticated admin's site.

    Requires Bearer token authentication with admin role.
    The site_id is derived from the admin's own user record,
    so admins can only add users to their own site.

    Request body:
        email: User email
        role: Optional role ('user' or 'admin', defaults to 'user')

    Returns:
        201: User created successfully, verification email sent
        400: Validation error or duplicate email
        401: Missing or invalid token
        403: User does not have admin role
    """
    try:
        site_id = request.user.site_id
        role_str = validated_data.get('role', 'user')
        role = UserRole.ADMIN if role_str == 'admin' else UserRole.USER

        user = auth_service.register_user(
            site_id=site_id,
            email=validated_data['email'],
            password=None,
            role=role,
            is_admin_registration=True,
        )
        schema = UserResponseSchema()
        return jsonify(schema.dump(user)), 201
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
