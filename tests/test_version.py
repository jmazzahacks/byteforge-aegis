"""
The health endpoint reports which backend version is running.

Tenants integrate against version-specific semantics — webhook delivery
changed shape entirely in v65 — and we had told them to "cite the backend
version" while giving them no way to observe it. This closes that.
"""
import utils.version as version_module
from utils.version import UNKNOWN_VERSION, get_version


def _clear_cache():
    version_module._cached_version = None


def test_health_reports_a_version(test_client):
    response = test_client.get('/api/health')

    assert response.status_code == 200
    body = response.get_json()
    assert body['status'] == 'healthy'
    assert body['version']


def test_version_matches_the_version_file():
    """The number must be the one build-publish.sh stamped, since that is
    what the image tag is derived from."""
    _clear_cache()
    expected = version_module._VERSION_FILE.read_text().strip()

    assert get_version() == expected


def test_a_missing_version_file_does_not_break_health(test_client, monkeypatch, tmp_path):
    """Liveness must not depend on a cosmetic file.

    A health check that 500s because a build omitted VERSION would turn a
    missing string into an outage, and health is what orchestrators restart
    containers on.
    """
    _clear_cache()
    monkeypatch.setattr(version_module, '_VERSION_FILE', tmp_path / 'absent')

    assert get_version() == UNKNOWN_VERSION

    _clear_cache()
    response = test_client.get('/api/health')
    assert response.status_code == 200
    assert response.get_json()['version'] == UNKNOWN_VERSION

    _clear_cache()
