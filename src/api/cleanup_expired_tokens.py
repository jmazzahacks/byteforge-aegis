"""
Expired token cleanup endpoint.
"""
import logging

from flask import Blueprint, jsonify

from services.token_service import token_service
from utils.api_key_middleware import require_master_api_key

logger = logging.getLogger(__name__)

cleanup_expired_tokens_bp = Blueprint('cleanup_expired_tokens', __name__)


@cleanup_expired_tokens_bp.route('/api/admin/cleanup-expired-tokens', methods=['POST'])
@require_master_api_key
def cleanup_expired_tokens():
    """
    Delete every expired token across all five token tables.

    Requires master API key (X-API-Key header). Intended to be called on a
    schedule; safe to call by hand at any time, and safe to call twice.

    Only rows whose expires_at has already passed are removed, so this never
    invalidates a live session — an expired token would be rejected at
    validation anyway. The work is proportional to the number of dead rows,
    which is why the first run after a long gap is much larger than the rest.

    Returns:
        200: Per-table counts of rows removed, plus a total
        401: Missing or invalid API key
    """
    result = token_service.cleanup_expired_tokens()

    logger.info(
        'Expired token cleanup removed %s row(s)',
        result.total,
        extra={
            'auth_tokens': result.auth_tokens,
            'refresh_tokens': result.refresh_tokens,
            'email_verification_tokens': result.email_verification_tokens,
            'password_reset_tokens': result.password_reset_tokens,
            'email_change_requests': result.email_change_requests,
            'total': result.total,
        },
    )

    return jsonify({
        'auth_tokens': result.auth_tokens,
        'refresh_tokens': result.refresh_tokens,
        'email_verification_tokens': result.email_verification_tokens,
        'password_reset_tokens': result.password_reset_tokens,
        'email_change_requests': result.email_change_requests,
        'total': result.total,
    }), 200
