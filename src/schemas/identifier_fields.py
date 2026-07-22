"""
Marshmallow fields for site identifiers.

SiteIdentifierField accepts a request's ``site_id`` as a site UUID, verifies
the site exists, and loads the canonical uuid string for downstream services.
"""
from typing import Any

from marshmallow import ValidationError, fields

from utils.identifiers import resolve_site


class SiteIdentifierField(fields.Field):
    """A site UUID, validated to reference an existing site; loads as the uuid string."""

    def _deserialize(self, value: Any, attr, data, **kwargs) -> str:
        site = resolve_site(value)
        if site is None:
            raise ValidationError("Unknown or invalid site identifier")
        return site.uuid
