"""
The running backend version, read from the VERSION file.

Tenants integrating against Aegis need to know which semantics are live —
webhook delivery, for one, changed shape entirely in v65 — and until this
existed there was no way to ask. The advice we had given consumers was to
"cite the backend version", which they had no means of observing.

The file is written by build-publish.sh and copied into the image, so the
number here is the same one in the image tag.
"""
from pathlib import Path
from typing import Optional

UNKNOWN_VERSION = 'unknown'

# src/utils/version.py -> src/utils -> src -> the app root, which is where
# both the repo and the image keep VERSION. The path is resolved from this
# module rather than the working directory because gunicorn runs with
# --chdir src, so cwd is not the app root at runtime.
_VERSION_FILE = Path(__file__).resolve().parents[2] / 'VERSION'

_cached_version: Optional[str] = None


def get_version() -> str:
    """
    The backend version, or 'unknown' when the VERSION file is absent.

    Never raises: this is read on the health endpoint, and a liveness check
    that 500s because a build did not include a file would turn a cosmetic
    omission into an outage.
    """
    global _cached_version

    if _cached_version is not None:
        return _cached_version

    try:
        version = _VERSION_FILE.read_text().strip()
    except OSError:
        version = UNKNOWN_VERSION

    _cached_version = version or UNKNOWN_VERSION
    return _cached_version
