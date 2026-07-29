"""
The set of browser origins allowed to call this API.
"""
import logging
import os
import threading
import time
from typing import List
from urllib.parse import urlsplit

from database import db_manager

logger = logging.getLogger(__name__)

# Re-read the sites table at most this often. Adding a tenant should not
# require a restart, but neither should every preflight hit the database.
ORIGIN_CACHE_TTL_SECONDS = 300

_lock = threading.Lock()
_cached_origins: List[str] = []
_cached_at: float = 0.0


def _origin_of(url: str) -> str:
    """scheme://host[:port] — a URL's origin, which is what CORS compares."""
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return ''
    return f'{parts.scheme}://{parts.netloc}'


def _extra_origins() -> List[str]:
    """
    Origins that are not any tenant's frontend_url.

    The admin console is the reason this exists: it is served from the Aegis
    hostnames themselves, which are not rows in the sites table's
    frontend_url sense. Local development origins go here too.
    """
    raw = os.getenv('CORS_EXTRA_ORIGINS', '')
    return [origin.strip() for origin in raw.split(',') if origin.strip()]


def _load_origins() -> List[str]:
    """Read every site's frontend_url and reduce it to an origin."""
    origins = set(_extra_origins())

    try:
        for frontend_url in db_manager.list_site_frontend_urls():
            origin = _origin_of(frontend_url)
            if origin:
                origins.add(origin)
    except Exception:
        # A database blip must not silently widen or empty the policy. Keep
        # serving the previous snapshot; an empty list would break every
        # tenant frontend at once.
        logger.exception('Could not refresh CORS origins; keeping previous set')
        return list(_cached_origins)

    return sorted(origins)


def allowed_origins(force: bool = False) -> List[str]:
    """
    The current allow-list, refreshed at most every ORIGIN_CACHE_TTL_SECONDS.

    Derived from the sites table so a new tenant is allowed as soon as their
    site exists, without an env change or a restart. The tradeoff is that a
    tenant whose browser requests come from an origin other than their
    configured frontend_url is not covered — CORS_EXTRA_ORIGINS is the
    escape hatch for that.
    """
    global _cached_origins, _cached_at

    now = time.monotonic()
    with _lock:
        if force or not _cached_origins or (now - _cached_at) > ORIGIN_CACHE_TTL_SECONDS:
            _cached_origins = _load_origins()
            _cached_at = now
        return list(_cached_origins)
