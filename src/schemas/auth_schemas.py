"""
Marshmallow schemas for authentication API requests and responses.
"""
from marshmallow import Schema, fields, validate

from schemas.identifier_fields import SiteIdentifierField
from schemas.password_field import ExistingPasswordField, PasswordField
from schemas.strict_fields import StrictBoolean


class RegisterRequestSchema(Schema):
    """Schema for user registration request (password optional - can set via email verification)"""
    site_id = SiteIdentifierField(required=True)
    email = fields.Email(required=True, validate=validate.Length(max=255))
    password = PasswordField()  # Optional - if not provided, user sets via email


class AdminRegisterRequestSchema(Schema):
    """Schema for admin user registration request (no password - user sets via email)"""
    site_id = SiteIdentifierField(required=True)
    email = fields.Email(required=True, validate=validate.Length(max=255))
    role = fields.String(validate=validate.OneOf(['user', 'admin']))


class TenantAdminRegisterSchema(Schema):
    """Schema for tenant admin user registration (site_id derived from admin's token)"""
    email = fields.Email(required=True, validate=validate.Length(max=255))
    role = fields.String(validate=validate.OneOf(['user', 'admin']))


class LoginRequestSchema(Schema):
    """Schema for login request"""
    site_id = SiteIdentifierField(required=True)
    email = fields.Email(required=True, validate=validate.Length(max=255))
    password = ExistingPasswordField(required=True)


class VerifyEmailRequestSchema(Schema):
    """Schema for email verification request (with optional password for admin-created users)"""
    site_id = SiteIdentifierField(required=True)
    token = fields.String(required=True)
    password = PasswordField()  # Optional - required only for users without password


class CheckVerificationTokenSchema(Schema):
    """Schema for checking verification token status"""
    site_id = SiteIdentifierField(required=True)
    token = fields.String(required=True)


class ChangePasswordRequestSchema(Schema):
    """Schema for password change request"""
    old_password = ExistingPasswordField(required=True)
    new_password = PasswordField(required=True)


class RequestPasswordResetSchema(Schema):
    """Schema for password reset request"""
    site_id = SiteIdentifierField(required=True)
    email = fields.Email(required=True, validate=validate.Length(max=255))


class ResetPasswordRequestSchema(Schema):
    """Schema for password reset with token"""
    site_id = SiteIdentifierField(required=True)
    token = fields.String(required=True)
    new_password = PasswordField(required=True)


class RequestEmailChangeSchema(Schema):
    """Schema for email change request"""
    new_email = fields.Email(required=True, validate=validate.Length(max=255))
    # Required: a bearer token alone must not be able to move the account's
    # login identifier. Same reasoning as old_password on ChangePassword.
    password = ExistingPasswordField(required=True)


class ConfirmEmailChangeSchema(Schema):
    """Schema for confirming email change"""
    token = fields.String(required=True)


class UpdateUserRequestSchema(Schema):
    """Schema for the master-key admin user update (deletion protection)."""
    # StrictBoolean, not fields.Boolean: the default coerces "false"/"0"/"off"
    # and numeric 0, any of which would silently clear protection.
    deletion_protected = StrictBoolean(required=True)


class UserResponseSchema(Schema):
    """Schema for user response (no sensitive data)"""
    uuid = fields.String()
    site_uuid = fields.String()
    email = fields.Email()
    is_verified = fields.Boolean()
    role = fields.Method("get_role")
    created_at = fields.Integer()
    updated_at = fields.Integer()
    deletion_protected = fields.Boolean()

    def get_role(self, obj):
        """Extract role value from UserRole enum"""
        return obj.role.value if hasattr(obj.role, 'value') else obj.role


class AuthTokenResponseSchema(Schema):
    """Schema for auth token response"""
    token = fields.String()
    user_uuid = fields.String()
    expires_at = fields.Integer()


class RefreshTokenRequestSchema(Schema):
    """Schema for refresh token request"""
    refresh_token = fields.String(required=True)


class RefreshTokenResponseSchema(Schema):
    """Schema for refresh token response"""
    token = fields.String()
    user_uuid = fields.String()
    site_uuid = fields.String()
    expires_at = fields.Integer()


class LoginResultResponseSchema(Schema):
    """Schema for login result response (auth + refresh tokens)"""
    auth_token = fields.Nested(AuthTokenResponseSchema)
    refresh_token = fields.Nested(RefreshTokenResponseSchema, allow_none=True)
