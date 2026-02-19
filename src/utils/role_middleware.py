"""
Role-based authorization middleware for protecting endpoints by user role.
"""
from functools import wraps
from flask import request, jsonify
from services.token_service import token_service
from database import db_manager
from byteforge_aegis_models import UserRole


def require_role(required_role: UserRole):
    """
    Decorator to require a specific user role for an endpoint.

    Extracts the auth token from the Authorization header,
    validates it, and checks if the user has the required role.

    Args:
        required_role: The UserRole required to access this endpoint

    Returns:
        Decorator function that validates user role
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            auth_header = request.headers.get('Authorization')

            if not auth_header:
                return jsonify({'error': 'Missing authorization header'}), 401

            if not auth_header.startswith('Bearer '):
                return jsonify({'error': 'Invalid authorization header format'}), 401

            token = auth_header[7:]  # Remove 'Bearer ' prefix

            user_id = token_service.validate_auth_token(token)

            if user_id is None:
                return jsonify({'error': 'Invalid or expired token'}), 401

            user = db_manager.find_user_by_id(user_id)

            if not user:
                return jsonify({'error': 'User not found'}), 401

            if user.role != required_role:
                return jsonify({'error': 'Insufficient permissions'}), 403

            request.user_id = user_id
            request.user = user

            return func(*args, **kwargs)

        return wrapper
    return decorator
