"""
User login endpoint.
"""
from flask import Blueprint, jsonify
from services.auth_service import auth_service
from schemas.auth_schemas import LoginRequestSchema, LoginResultResponseSchema
from utils.validators import validate_request

login_bp = Blueprint('login', __name__)


@login_bp.route('/api/auth/login', methods=['POST'])
@validate_request(LoginRequestSchema)
def login(validated_data):
    """
    Login a user.

    Request body:
        site_id: ID of the site
        email: User email
        password: User password

    Returns:
        200: Login successful with auth token and refresh token
        401: Invalid credentials
        403: Email not verified
    """
    try:
        login_result = auth_service.login(
            site_id=validated_data['site_id'],
            email=validated_data['email'],
            password=validated_data['password']
        )
        schema = LoginResultResponseSchema()
        return jsonify(schema.dump(login_result)), 200
    except ValueError as e:
        error_msg = str(e).lower()
        if 'not verified' in error_msg:
            return jsonify({'error': str(e)}), 403
        else:
            return jsonify({'error': str(e)}), 401
