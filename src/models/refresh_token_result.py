from dataclasses import dataclass
from typing import Optional
from byteforge_aegis_models import RefreshToken


@dataclass
class RefreshTokenResult:
    """
    Result of validating and rotating a refresh token.

    Attributes:
        user_uuid: The UUID of the user the token belongs to
        site_uuid: The UUID of the site the token belongs to
        new_refresh_token: The new rotated refresh token (None if rotation disabled or within grace period)
    """
    user_uuid: str
    site_uuid: str
    new_refresh_token: Optional[RefreshToken] = None
