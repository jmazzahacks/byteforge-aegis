"""
Token refresh endpoint.
"""
from flask import Blueprint, jsonify
from services.auth_service import auth_service
from schemas.auth_schemas import RefreshTokenRequestSchema, LoginResultResponseSchema
from utils.validators import validate_request

refresh_token_bp = Blueprint('refresh_token', __name__)


@refresh_token_bp.route('/api/auth/refresh', methods=['POST'])
@validate_request(RefreshTokenRequestSchema)
def refresh_token(validated_data):
    """
    Refresh an authentication token using a refresh token.

    Request body:
        refresh_token: The refresh token string

    Returns:
        200: New auth token (and optionally new refresh token if rotation enabled)
        401: Invalid or expired refresh token
        403: Token reuse detected (potential theft)
    """
    try:
        login_result = auth_service.refresh_auth_token(validated_data['refresh_token'])
        schema = LoginResultResponseSchema()
        return jsonify(schema.dump(login_result)), 200
    except ValueError as e:
        error_msg = str(e).lower()
        if 'reuse detected' in error_msg:
            return jsonify({'error': str(e)}), 403
        return jsonify({'error': str(e)}), 401
