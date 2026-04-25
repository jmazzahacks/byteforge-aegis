"""
Smoke tests verifying every public auth endpoint is gated by the
require_tenant_api_key decorator. Each endpoint:
  - Returns 401 with the uniform tenant-api-key error when the header is missing.
  - Passes the gate (response != 401-from-middleware) when the key is correct.

Full per-endpoint behavior is covered by the existing handler tests; these
tests only check the gate is wired up correctly.
"""

UNIFORM_ERROR = 'Invalid or missing tenant API key'


GATED_REQUESTS = [
    ('/api/auth/register', {'email': 'gate@example.com', 'password': 'gate_pw_8888'}),
    ('/api/auth/login', {'email': 'gate@example.com', 'password': 'whatever8'}),
    ('/api/auth/request-password-reset', {'email': 'gate@example.com'}),
    ('/api/auth/reset-password', {'token': 'fake_token', 'new_password': 'newpass99'}),
    ('/api/auth/verify-email', {'token': 'fake_token'}),
    ('/api/auth/check-verification-token', {'token': 'fake_token'}),
]


def _with_site_id(body, sample_site):
    return {**body, 'site_id': sample_site.id}


def test_all_gated_endpoints_reject_missing_header(test_client, sample_site):
    for path, body in GATED_REQUESTS:
        response = test_client.post(path, json=_with_site_id(body, sample_site))
        assert response.status_code == 401, f"{path} should 401 without key"
        assert response.get_json()['error'] == UNIFORM_ERROR, f"{path} should return uniform error"


def test_all_gated_endpoints_reject_wrong_header(test_client, sample_site):
    for path, body in GATED_REQUESTS:
        response = test_client.post(
            path,
            json=_with_site_id(body, sample_site),
            headers={'X-Tenant-Api-Key': 'wrong_key'},
        )
        assert response.status_code == 401, f"{path} should 401 with wrong key"
        assert response.get_json()['error'] == UNIFORM_ERROR


def test_all_gated_endpoints_pass_with_correct_header(test_client, sample_site):
    """With the correct key, the gate passes — handler may still fail downstream
    (bad token, no user, etc.) but the response will not be the gate's 401."""
    for path, body in GATED_REQUESTS:
        response = test_client.post(
            path,
            json=_with_site_id(body, sample_site),
            headers={'X-Tenant-Api-Key': sample_site.tenant_api_key},
        )
        # Gate-level 401 has the uniform error. Anything else means we got past it.
        if response.status_code == 401:
            assert response.get_json()['error'] != UNIFORM_ERROR, (
                f"{path} should pass the gate with the correct key"
            )
