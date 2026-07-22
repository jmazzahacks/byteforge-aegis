"""
Current user introspection endpoint.

Allows downstream services to resolve a bearer token to the owning user.
"""
from flask import Blueprint, jsonify, make_response, request
from database import db_manager
from schemas.auth_schemas import UserResponseSchema
from utils.auth_middleware import require_auth

me_bp = Blueprint('me', __name__)


@me_bp.route('/api/auth/me', methods=['GET'])
@require_auth
def me():
    """
    Return the user associated with the bearer token.

    Headers:
        Authorization: Bearer <auth_token>

    Returns:
        200: User record for the authenticated user
        401: Missing, malformed, unknown, or expired token
    """
    user = db_manager.find_user_by_uuid(request.user_uuid)
    if user is None:
        return jsonify({'error': 'Invalid or expired token'}), 401

    schema = UserResponseSchema()
    response = make_response(jsonify(schema.dump(user)), 200)
    response.headers['Cache-Control'] = 'no-store'
    return response
