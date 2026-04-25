"""
Tests for the public /api/sites/by-domain endpoint.

Specifically verifies the public response does NOT leak webhook_secret
or tenant_api_key, since the endpoint is unauthenticated.
"""


def test_by_domain_returns_site_without_secrets(test_client, sample_site):
    """Public endpoint returns site info but excludes secret fields."""
    response = test_client.get(f'/api/sites/by-domain?domain={sample_site.domain}')

    assert response.status_code == 200
    data = response.get_json()
    assert data['id'] == sample_site.id
    assert data['domain'] == sample_site.domain
    assert 'webhook_secret' not in data
    assert 'tenant_api_key' not in data
    assert 'mailgun_api_key' not in data


def test_by_domain_unknown_returns_404(test_client, clean_database):
    response = test_client.get('/api/sites/by-domain?domain=nope.example.com')
    assert response.status_code == 404


def test_by_domain_missing_param_returns_400(test_client, clean_database):
    response = test_client.get('/api/sites/by-domain')
    assert response.status_code == 400
