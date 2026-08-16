"""
Tests for the master-key-protected POST /api/sites endpoint.

This endpoint had no coverage at all, which is how it shipped broken: phase 3
of the int->UUID migration (7cd5c9d) dropped the `id=0` placeholder from the
Site(...) call without replacing it with a uuid, and Site.uuid is a required
field with no default. Every create raised TypeError inside the constructor —
before the handler's own try/except — so it surfaced as a bare Flask 500.
"""
from config import get_config
from database import db_manager


def _master_headers() -> dict:
    return {'X-API-Key': get_config().MASTER_API_KEY}


def _site_payload(domain: str = 'newsite.example.com') -> dict:
    return {
        'name': 'New Site',
        'domain': domain,
        'frontend_url': 'https://newsite.example.com',
        'email_from': 'noreply@newsite.example.com',
        'email_from_name': 'New Site',
    }


def test_create_site_mints_uuid_and_persists(test_client, clean_database):
    """Regression: the site is created and comes back with a real UUID."""
    response = test_client.post(
        '/api/sites',
        json=_site_payload(),
        headers=_master_headers(),
    )

    assert response.status_code == 201
    data = response.get_json()

    # The uuid must be minted, not empty — an empty string would still round
    # trip through the response schema, so assert it addresses a real row.
    assert data['uuid']
    persisted = db_manager.find_site_by_uuid(data['uuid'])
    assert persisted is not None
    assert persisted.domain == 'newsite.example.com'


def test_create_site_assigns_distinct_uuids(test_client, clean_database):
    """Two creates must not collide on a shared placeholder uuid."""
    first = test_client.post(
        '/api/sites', json=_site_payload('one.example.com'), headers=_master_headers()
    )
    second = test_client.post(
        '/api/sites', json=_site_payload('two.example.com'), headers=_master_headers()
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.get_json()['uuid'] != second.get_json()['uuid']


def test_create_site_duplicate_domain_returns_400(test_client, sample_site):
    """The duplicate branch is reachable — it sits after the Site(...) call."""
    response = test_client.post(
        '/api/sites',
        json=_site_payload(sample_site.domain),
        headers=_master_headers(),
    )

    assert response.status_code == 400
    assert response.get_json()['error'] == 'Domain already exists'


def test_create_site_requires_master_key(test_client, clean_database):
    response = test_client.post('/api/sites', json=_site_payload())
    assert response.status_code == 401
