"""
Marshmallow schemas for site API requests and responses.
"""
from marshmallow import Schema, fields, validate


class CreateSiteRequestSchema(Schema):
    """Schema for creating a new site"""
    name = fields.String(required=True, validate=validate.Length(min=1, max=255))
    domain = fields.String(required=True, validate=validate.Length(min=1, max=255))
    frontend_url = fields.Url(required=True)
    verification_redirect_url = fields.Url(required=False, allow_none=True)
    email_from = fields.Email(required=True)
    email_from_name = fields.String(required=True, validate=validate.Length(min=1, max=255))
    allow_self_registration = fields.Boolean(load_default=True)
    webhook_url = fields.Url(required=False, allow_none=True)
    mailgun_domain = fields.String(required=False, allow_none=True, validate=validate.Length(max=255))
    mailgun_api_key = fields.String(required=False, allow_none=True, validate=validate.Length(max=255))


class UpdateSiteRequestSchema(Schema):
    """Schema for updating a site (all fields optional)"""
    name = fields.String(required=False, validate=validate.Length(min=1, max=255))
    domain = fields.String(required=False, validate=validate.Length(min=1, max=255))
    frontend_url = fields.Url(required=False)
    verification_redirect_url = fields.Url(required=False, allow_none=True)
    email_from = fields.Email(required=False)
    email_from_name = fields.String(required=False, validate=validate.Length(min=1, max=255))
    allow_self_registration = fields.Boolean(required=False)
    webhook_url = fields.Url(required=False, allow_none=True)
    regenerate_webhook_secret = fields.Boolean(required=False)
    regenerate_tenant_api_key = fields.Boolean(required=False)
    mailgun_domain = fields.String(required=False, allow_none=True, validate=validate.Length(max=255))
    mailgun_api_key = fields.String(required=False, allow_none=True, validate=validate.Length(max=255))


class SiteResponseSchema(Schema):
    """Schema for site response (admin-only — includes secrets)."""
    uuid = fields.String()
    name = fields.String()
    domain = fields.String()
    frontend_url = fields.Url()
    verification_redirect_url = fields.Url(allow_none=True)
    email_from = fields.Email()
    email_from_name = fields.String()
    created_at = fields.Integer()
    updated_at = fields.Integer()
    allow_self_registration = fields.Boolean()
    webhook_url = fields.Url(allow_none=True)
    webhook_secret = fields.String(allow_none=True)
    tenant_api_key = fields.String(allow_none=True)
    mailgun_domain = fields.String(allow_none=True)
    mailgun_api_key = fields.String(allow_none=True)


class PublicSiteResponseSchema(Schema):
    """Schema for site response on public endpoints. Excludes secrets."""
    uuid = fields.String()
    name = fields.String()
    domain = fields.String()
    frontend_url = fields.Url()
    verification_redirect_url = fields.Url(allow_none=True)
    email_from = fields.Email()
    email_from_name = fields.String()
    created_at = fields.Integer()
    updated_at = fields.Integer()
    allow_self_registration = fields.Boolean()
