"""
Marshmallow fields for the int -> UUID migration (dual-support phase).

SiteIdentifierField accepts a request's ``site_id`` as either an integer id or
a UUID string and normalizes it to the integer site id, so downstream services
and handlers continue to operate on integer ids unchanged during the migration.
"""
from typing import Any

from marshmallow import ValidationError, fields

from utils.identifiers import resolve_site


class SiteIdentifierField(fields.Field):
    """A site identifier accepted as an integer id or UUID string, loaded as the int id."""

    def _deserialize(self, value: Any, attr, data, **kwargs) -> int:
        site = resolve_site(value)
        if site is None:
            raise ValidationError("Unknown or invalid site identifier")
        return site.id
