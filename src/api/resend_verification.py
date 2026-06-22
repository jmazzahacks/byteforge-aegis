"""
Resend verification email endpoint.
"""
from flask import Blueprint, jsonify, request
from services.auth_service import auth_service
from utils.api_key_middleware import require_master_api_key
from utils.identifiers import resolve_user

resend_verification_bp = Blueprint('resend_verification', __name__)


@resend_verification_bp.route('/api/admin/resend-verification/<user_id>', methods=['POST'])
@require_master_api_key
def resend_verification(user_id: str):
    """
    Resend verification email for a user.

    Requires master API key (X-API-Key header).

    Path parameters:
        user_id: User identifier (integer id or UUID)

    Returns:
        200: Verification email sent successfully
        400: User already verified or email failed
        401: Missing or invalid API key
        404: User not found
    """
    user = resolve_user(user_id)
    if user is None:
        return jsonify({'error': 'User not found'}), 404
    try:
        success = auth_service.resend_verification_email(user.id)
        if success:
            return jsonify({'message': 'Verification email sent successfully'}), 200
        else:
            return jsonify({'error': 'Failed to send verification email'}), 400
    except ValueError as e:
        error_msg = str(e)
        if 'not found' in error_msg.lower():
            return jsonify({'error': error_msg}), 404
        return jsonify({'error': error_msg}), 400
