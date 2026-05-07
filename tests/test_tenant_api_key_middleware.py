"""
Tests for the require_tenant_api_key decorator.

Each test exercises the gate against the live /api/auth/register endpoint
since the decorator runs before validate_request and returns identical
401 responses for every failure mode (anti-enumeration).
"""


UNIFORM_ERROR_FRAGMENT = 'tenant api key'


def _post(test_client, path, body, headers=None):
    return test_client.post(path, json=body, headers=headers or {})


def test_missing_header_returns_401(test_client, sample_site):
    """No X-Tenant-Api-Key header returns 401."""
    response = _post(
        test_client,
        '/api/auth/register',
        {'site_id': sample_site.id, 'email': 'a@example.com'},
    )
    assert response.status_code == 401
    assert UNIFORM_ERROR_FRAGMENT in response.get_json()['error'].lower()


def test_wrong_key_returns_401(test_client, sample_site):
    """Header present but doesn't match the site's tenant_api_key."""
    response = _post(
        test_client,
        '/api/auth/register',
        {'site_id': sample_site.id, 'email': 'a@example.com'},
        headers={'X-Tenant-Api-Key': 'completely_wrong_key'},
    )
    assert response.status_code == 401
    assert UNIFORM_ERROR_FRAGMENT in response.get_json()['error'].lower()


def test_missing_site_id_returns_401(test_client, sample_site):
    """site_id absent from body — uniform 401, not 400."""
    response = _post(
        test_client,
        '/api/auth/register',
        {'email': 'a@example.com'},
        headers={'X-Tenant-Api-Key': sample_site.tenant_api_key},
    )
    assert response.status_code == 401
    assert UNIFORM_ERROR_FRAGMENT in response.get_json()['error'].lower()


def test_non_int_site_id_returns_401(test_client, sample_site):
    """site_id of wrong type returns uniform 401."""
    response = _post(
        test_client,
        '/api/auth/register',
        {'site_id': 'not-an-int', 'email': 'a@example.com'},
        headers={'X-Tenant-Api-Key': sample_site.tenant_api_key},
    )
    assert response.status_code == 401
    assert UNIFORM_ERROR_FRAGMENT in response.get_json()['error'].lower()


def test_unknown_site_id_returns_401(test_client, sample_site):
    """site_id that doesn't exist in DB returns uniform 401."""
    response = _post(
        test_client,
        '/api/auth/register',
        {'site_id': 999999, 'email': 'a@example.com'},
        headers={'X-Tenant-Api-Key': sample_site.tenant_api_key},
    )
    assert response.status_code == 401
    assert UNIFORM_ERROR_FRAGMENT in response.get_json()['error'].lower()


def test_uniform_error_response_across_failure_modes(test_client, sample_site):
    """Every failure mode returns identical response bytes (anti-enumeration)."""
    cases = [
        ({'site_id': sample_site.id, 'email': 'a@example.com'}, {}),
        ({'site_id': sample_site.id, 'email': 'a@example.com'}, {'X-Tenant-Api-Key': 'wrong'}),
        ({'email': 'a@example.com'}, {'X-Tenant-Api-Key': sample_site.tenant_api_key}),
        ({'site_id': 999999, 'email': 'a@example.com'}, {'X-Tenant-Api-Key': sample_site.tenant_api_key}),
    ]
    response_bodies = set()
    for body, headers in cases:
        response = _post(test_client, '/api/auth/register', body, headers=headers)
        assert response.status_code == 401
        # Bytes-level comparison so future drift in jsonify() output (whitespace,
        # field ordering) breaks this test rather than silently leaking a probe.
        response_bodies.add(response.get_data())
    assert len(response_bodies) == 1, (
        f"Expected identical response bodies across failure modes, got: {response_bodies}"
    )


def test_valid_key_passes_through_to_handler(test_client, sample_site):
    """Correct key + correct site_id reaches the handler (returns 201)."""
    response = _post(
        test_client,
        '/api/auth/register',
        {
            'site_id': sample_site.id,
            'email': 'newuser@example.com',
            'password': 'gate_test_pw_99',
        },
        headers={'X-Tenant-Api-Key': sample_site.tenant_api_key},
    )
    # Should pass the gate. Register returns 201 on success.
    assert response.status_code == 201


def test_path_site_id_fallback_for_get(test_client, sample_site, sample_user):
    """For GET routes (no JSON body), middleware reads site_id from view_args."""
    response = test_client.get(
        f'/api/sites/{sample_site.id}/users/{sample_user.id}',
        headers={'X-Tenant-Api-Key': sample_site.tenant_api_key},
    )
    # Passing the gate means we reach the handler; sample_user belongs to
    # sample_site so we get 200.
    assert response.status_code == 200


def test_path_site_id_fallback_missing_returns_401(test_client, sample_site):
    """GET against a path-based route with no matching site → uniform 401."""
    response = test_client.get(
        f'/api/sites/999999/users/1',
        headers={'X-Tenant-Api-Key': sample_site.tenant_api_key},
    )
    assert response.status_code == 401
    assert UNIFORM_ERROR_FRAGMENT in response.get_json()['error'].lower()
