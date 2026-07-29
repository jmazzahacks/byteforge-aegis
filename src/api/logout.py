"""
User logout endpoint.
"""
from flask import Blueprint, jsonify, request
from database import db_manager
from services.token_service import token_service
from utils.auth_middleware import require_auth

logout_bp = Blueprint('logout', __name__)


@logout_bp.route('/api/auth/logout', methods=['POST'])
@require_auth
def logout():
    """
    Logout a user by invalidating their auth token and their session's
    refresh token family.

    Deleting the auth token alone was not a logout: the refresh token
    outlives it by default a full week, so a captured one could mint fresh
    auth tokens indefinitely after the user believed they had signed out.

    Pass the session's refresh_token to end that session specifically.
    Omitting it still clears the auth token, but leaves the refresh token
    live — so clients should always send it.

    Headers:
        Authorization: Bearer <token>

    Body (optional):
        refresh_token: The session's refresh token; its family is revoked

    Returns:
        200: Logout successful
        401: Missing or invalid token
        404: Token not found
    """
    auth_header = request.headers.get('Authorization')
    token = auth_header[7:]  # Remove 'Bearer ' prefix

    deleted = db_manager.delete_auth_token(token)

    # Ignored if it isn't this caller's token — see
    # revoke_refresh_family_for_user. Failure is deliberately not reported:
    # distinguishing "not yours" from "unknown" would confirm whether a
    # refresh token exists.
    body = request.get_json(silent=True) or {}
    refresh_token = body.get('refresh_token')
    if refresh_token:
        token_service.revoke_refresh_family_for_user(refresh_token, request.user_uuid)

    if deleted:
        return jsonify({'message': 'Logged out successfully'}), 200
    else:
        return jsonify({'error': 'Token not found'}), 404
