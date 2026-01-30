"""
User registration endpoint.
"""
from flask import Blueprint, jsonify
from services.auth_service import auth_service
from schemas.auth_schemas import RegisterRequestSchema
from utils.validators import validate_request

register_bp = Blueprint('register', __name__)


@register_bp.route('/api/auth/register', methods=['POST'])
@validate_request(RegisterRequestSchema)
def register(validated_data: dict):
    """
    Register a new user.

    Request body:
        site_id: ID of the site
        email: User email
        password: Optional user password (min 8 characters)
                  If not provided, user will set password via email verification

    Returns:
        201: Registration initiated (check email)
        400: Validation error or self-registration disabled

    Note: Returns the same success response whether email exists or not
          to prevent email enumeration attacks.
    """
    try:
        auth_service.register_user(
            site_id=validated_data['site_id'],
            email=validated_data['email'],
            password=validated_data.get('password')  # Optional - can be None
        )
        # Always return same message to prevent email enumeration
        return jsonify({
            'message': 'Registration initiated. Please check your email to continue.'
        }), 201
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
