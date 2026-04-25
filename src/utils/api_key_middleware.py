"""
API key authorization middleware for protecting administrative endpoints.
"""
import hmac
from functools import wraps
from flask import request, jsonify
from config import get_config


def require_master_api_key(func):
    """
    Decorator to require master API key for global administrative operations.

    Checks for X-API-Key header and validates against MASTER_API_KEY from config
    using a constant-time comparison.

    Returns 401 if API key is missing or invalid.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        config = get_config()

        if not config.MASTER_API_KEY:
            return jsonify({'error': 'Master API key not configured'}), 500

        api_key = request.headers.get('X-API-Key')

        if not api_key:
            return jsonify({'error': 'Missing X-API-Key header'}), 401

        if not hmac.compare_digest(api_key, config.MASTER_API_KEY):
            return jsonify({'error': 'Invalid API key'}), 401

        return func(*args, **kwargs)

    return wrapper
