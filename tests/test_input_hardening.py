"""
Tests for request-level input hardening.

Both controls here are the kind that look present but do nothing if wired
up wrong, so they are asserted through the app rather than by reading
config.
"""
from config import get_config


def _master_headers():
    return {'X-API-Key': get_config().MASTER_API_KEY}


def test_oversized_request_body_is_rejected(test_client, sample_site):
    """The tenant gate parses the body to find site_id BEFORE checking any
    credential, so without a cap an unauthenticated caller can make a worker
    buffer an arbitrarily large request."""
    payload = {'site_id': sample_site.uuid, 'email': 'a@b.com',
               'password': 'x' * (128 * 1024)}

    response = test_client.post(
        '/api/auth/login',
        headers={'X-Tenant-Api-Key': sample_site.tenant_api_key},
        json=payload,
    )

    assert response.status_code == 413


def test_numeric_true_cannot_enable_self_registration(test_client, sample_site):
    """allow_self_registration gates open registration on a tenant.

    fields.Boolean coerces 1 to True, so a client bridge serialising
    booleans numerically could switch it on without anyone intending it.
    """
    response = test_client.put(
        f'/api/sites/{sample_site.uuid}',
        headers=_master_headers(),
        json={'allow_self_registration': 1},
    )

    assert response.status_code == 400


def test_numeric_true_cannot_rotate_a_live_tenant_api_key(test_client, sample_site):
    """Silently rotating this breaks the tenant's integration."""
    response = test_client.put(
        f'/api/sites/{sample_site.uuid}',
        headers=_master_headers(),
        json={'regenerate_tenant_api_key': 1},
    )

    assert response.status_code == 400


def test_real_booleans_still_work(test_client, sample_site):
    """Guard against hardening that simply breaks the field."""
    response = test_client.put(
        f'/api/sites/{sample_site.uuid}',
        headers=_master_headers(),
        json={'allow_self_registration': False},
    )

    assert response.status_code == 200
    assert response.get_json()['allow_self_registration'] is False


def test_overlong_email_is_rejected_not_500(test_client, sample_site):
    """users.email is VARCHAR(255); an uncapped field reached the INSERT and
    raised StringDataRightTruncation, which is not a ValueError and so
    escaped the handler as an unhandled 500."""
    response = test_client.post(
        '/api/auth/register',
        headers={'X-Tenant-Api-Key': sample_site.tenant_api_key},
        json={'site_id': sample_site.uuid,
              'email': ('a' * 250) + '@example.com',
              'password': 'valid_password_9'},
    )

    assert response.status_code == 400
