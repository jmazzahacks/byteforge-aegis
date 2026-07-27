"""
Schema fields with stricter deserialization than marshmallow's defaults.
"""
from typing import Any, Mapping, Optional

from marshmallow import fields


class StrictBoolean(fields.Field):
    """
    Boolean that accepts only real JSON booleans.

    marshmallow's Boolean coerces a wide falsy set ("false", "0", "off", 0)
    and truthy/falsy overrides cannot close the numeric case: in Python
    ``0 == False`` and ``hash(0) == hash(False)``, so ``0 in {False}`` is True
    and a numeric 0 still deserializes to False.

    That matters for safety flags. A client that serializes booleans as 0/1 —
    a form-encoded bridge, some ORMs, marshalers that emit numeric booleans —
    would silently clear deletion protection while the API contract says it
    cannot. Requiring a real bool makes the guarantee independent of any
    particular client's serialization.
    """

    default_error_messages = {'invalid': 'Not a valid boolean (expected true or false).'}

    def _deserialize(
        self,
        value: Any,
        attr: Optional[str],
        data: Optional[Mapping[str, Any]],
        **kwargs: Any
    ) -> bool:
        if not isinstance(value, bool):
            raise self.make_error('invalid')
        return value

    def _serialize(self, value: Any, attr: Optional[str], obj: Any, **kwargs: Any) -> Optional[bool]:
        if value is None:
            return None
        return bool(value)
