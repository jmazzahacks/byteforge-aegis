"""
Request email change endpoint.
"""
from flask import Blueprint, jsonify, request
from services.auth_service import auth_service
from schemas.auth_schemas import RequestEmailChangeSchema
from utils.validators import validate_request
from utils.auth_middleware import require_auth

request_email_change_bp = Blueprint('request_email_change', __name__)


@request_email_change_bp.route('/api/auth/request-email-change', methods=['POST'])
@require_auth
@validate_request(RequestEmailChangeSchema)
def request_email_change(validated_data):
    """
    Request an email change.

    Headers:
        Authorization: Bearer <token>

    Request body:
        new_email: New email address

    Returns:
        200: Email change request created
        400: Invalid email or email already in use
        401: Missing or invalid auth token
    """
    try:
        auth_service.request_email_change(
            user_uuid=request.user_uuid,
            new_email=validated_data['new_email']
        )
        return jsonify({'message': 'Email change confirmation sent'}), 200
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
