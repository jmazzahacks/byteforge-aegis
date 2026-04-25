"""
Tenant API key generation service.

The tenant_api_key is a per-site secret required in the X-Tenant-Api-Key
header on public auth endpoints (register, login, password reset, etc.).
It must live server-side on the tenant's backend.
"""
import secrets


class TenantKeyService:
    """Service for generating tenant API keys."""

    def generate_tenant_api_key(self) -> str:
        """
        Generate a cryptographically random tenant API key.

        Returns:
            str: 64-character hex string
        """
        return secrets.token_hex(32)


tenant_key_service = TenantKeyService()
