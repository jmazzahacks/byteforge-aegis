"""
Password field with the byte-length bound bcrypt actually enforces.
"""
from marshmallow import ValidationError, fields, validate

# bcrypt hashes at most 72 BYTES. Older versions truncated silently; bcrypt 5
# raises ValueError instead, on hashpw AND on checkpw.
BCRYPT_MAX_PASSWORD_BYTES = 72
MIN_PASSWORD_LENGTH = 8


def validate_password_bytes(value: str) -> None:
    """
    Reject passwords bcrypt cannot hash.

    This has to count BYTES, not characters: `validate.Length` counts
    characters, so 72 emoji pass a 72-character cap and then blow up at 288
    bytes inside bcrypt.

    Rejecting at the schema is what closes an account-enumeration oracle.
    Unbounded input reached bcrypt at different points depending on whether
    the account existed — on login the lookup failed first and returned
    "Invalid credentials", while an existing account got as far as the hash
    and returned bcrypt's own error text; on registration the duplicate-email
    branch returned success before hashing, so a *free* address was the one
    that errored. Either way one request per address distinguished them.
    Schema validation runs before any database lookup, so every caller now
    gets the identical 400 regardless of whether the account exists.
    """
    if len(value.encode('utf-8')) > BCRYPT_MAX_PASSWORD_BYTES:
        raise ValidationError(
            f'Password must be at most {BCRYPT_MAX_PASSWORD_BYTES} bytes.'
        )


def PasswordField(**kwargs) -> fields.String:
    """A password string bounded at both ends, for any write path."""
    return fields.String(
        validate=[
            validate.Length(min=MIN_PASSWORD_LENGTH),
            validate_password_bytes,
        ],
        **kwargs
    )


def ExistingPasswordField(**kwargs) -> fields.String:
    """
    A password being *presented* rather than set (login, old_password).

    No minimum — a minimum here would reject accounts created before the
    current policy and, worse, would itself distinguish "too short for
    today's rules" from "wrong password". The byte cap still applies,
    because that is what stops bcrypt raising.
    """
    return fields.String(validate=[validate_password_bytes], **kwargs)
