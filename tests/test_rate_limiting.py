"""
Tests that the credential endpoints are actually rate limited.

A misconfigured limiter is indistinguishable from no limiter until someone
is being attacked, so these assert a 429 really arrives rather than trusting
that the decorators were applied.
"""
import pytest

from utils.rate_limit import limiter

TENANT_HEADER = 'X-Tenant-Api-Key'


@pytest.fixture(autouse=True)
def reset_limiter(test_client):
    """Counters are process-global; without this, tests contaminate each other.

    Depends on test_client so it runs after create_app — the limiter has no
    storage backend until init_app has been called.
    """
    limiter.reset()
    yield
    limiter.reset()


def _login(client, site, email='victim@test.example.com', password='wrong_password_9'):
    return client.post(
        '/api/auth/login',
        headers={TENANT_HEADER: site.tenant_api_key},
        json={'site_id': site.uuid, 'email': email, 'password': password},
    )


def test_repeated_login_attempts_are_eventually_blocked(test_client, sample_site):
    """Credential stuffing against one account must stop being free.

    21 requests, not 15, and the count is load bearing. flask-limiter uses
    fixed windows, so a minute boundary landing mid-run splits the attempts
    into two counters. Against the 10/minute account limit a 429 needs 11 in
    ONE window, and 15 attempts split as anything from 5+10 to 10+5 leaves
    both sides short — the limit never fires and the test fails having found
    no bug. At 21 no split can leave both sides under 11.
    """
    statuses = [_login(test_client, sample_site).status_code for _ in range(21)]

    assert 429 in statuses, f"no request was rate limited: {statuses}"


def test_the_limit_is_per_account_not_global(test_client, sample_site):
    """A blocked attacker must not lock every other user out of logging in.

    This is the failure mode of keying limits badly: if the bucket were
    shared, one attacker would deny login to the whole tenant.
    """
    for _ in range(15):
        _login(test_client, sample_site, email='victim@test.example.com')

    other = _login(test_client, sample_site, email='someone-else@test.example.com')

    assert other.status_code != 429


def test_password_reset_is_limited_more_tightly_than_login(test_client, sample_site):
    """Each accepted reset sends mail on the tenant's own Mailgun domain."""
    statuses = []
    for _ in range(6):
        response = test_client.post(
            '/api/auth/request-password-reset',
            headers={TENANT_HEADER: sample_site.tenant_api_key},
            json={'site_id': sample_site.uuid, 'email': 'target@test.example.com'},
        )
        statuses.append(response.status_code)

    assert 429 in statuses, f"reset requests were never limited: {statuses}"
    # Tighter than login's 10/min — 3 per 15 minutes.
    assert statuses.index(429) < 5


def test_a_normal_login_is_not_blocked(test_client, sample_site):
    """Guard against a limit so tight it breaks ordinary use."""
    assert _login(test_client, sample_site).status_code != 429
