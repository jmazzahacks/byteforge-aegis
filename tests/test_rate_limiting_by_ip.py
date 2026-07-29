"""
Per-IP rate limiting, which catches what per-account limits cannot.

A spread attack — one guess each against ten thousand accounts — stays under
every per-account limit forever. These tests use a DIFFERENT email on each
request specifically so the per-account limit cannot be what fires; if it
were, the test would pass while proving nothing.
"""
import pytest

from utils.rate_limit import CLIENT_IP_HEADER, LOGIN_IP_LIMIT, limiter

TENANT_HEADER = 'X-Tenant-Api-Key'
ATTACKER_IP = '203.0.113.7'
OTHER_IP = '198.51.100.9'

# Parsed from the configured limit so the test cannot drift away from it.
LOGIN_IP_MAX = int(LOGIN_IP_LIMIT.split()[0])


@pytest.fixture(autouse=True)
def reset_limiter(test_client):
    limiter.reset()
    yield
    limiter.reset()


def _spread_login(client, site, index, client_ip=None):
    """One login attempt against a distinct account."""
    headers = {TENANT_HEADER: site.tenant_api_key}
    if client_ip is not None:
        headers[CLIENT_IP_HEADER] = client_ip

    return client.post(
        '/api/auth/login',
        headers=headers,
        json={'site_id': site.uuid,
              'email': f'victim{index}@test.example.com',
              'password': 'wrong_password_9'},
    )


def test_spread_attack_from_one_ip_is_blocked(test_client, sample_site):
    """The attack per-account limits miss entirely."""
    statuses = [
        _spread_login(test_client, sample_site, i, ATTACKER_IP).status_code
        for i in range(LOGIN_IP_MAX + 5)
    ]

    assert 429 in statuses, (
        f'{len(statuses)} attempts against {len(statuses)} different accounts '
        f'from one IP were never limited'
    )
    # Must be the IP limit firing, not the per-account one — every request
    # used a different address, so per-account never reaches 2.
    assert statuses.index(429) >= LOGIN_IP_MAX


def test_a_different_ip_is_a_separate_bucket(test_client, sample_site):
    """One blocked attacker must not lock out unrelated users."""
    for i in range(LOGIN_IP_MAX + 5):
        _spread_login(test_client, sample_site, i, ATTACKER_IP)

    innocent = _spread_login(test_client, sample_site, 9999, OTHER_IP)

    assert innocent.status_code != 429


def test_no_ip_header_means_no_ip_limiting(test_client, sample_site):
    """Absent header must SKIP the limit, not collapse everyone into one
    bucket — that would take logins down for every tenant at once."""
    statuses = [
        _spread_login(test_client, sample_site, i).status_code
        for i in range(LOGIN_IP_MAX + 5)
    ]

    assert 429 not in statuses, (
        'requests with no client-IP header were rate limited together — '
        'they are sharing a bucket, which is the failure this avoids'
    )


def test_per_account_limit_still_applies_independently(test_client, sample_site):
    """The two keyings are independent; the account limit is much tighter."""
    statuses = []
    for _ in range(12):
        response = test_client.post(
            '/api/auth/login',
            headers={TENANT_HEADER: sample_site.tenant_api_key,
                     CLIENT_IP_HEADER: ATTACKER_IP},
            json={'site_id': sample_site.uuid,
                  'email': 'one-victim@test.example.com',
                  'password': 'wrong_password_9'},
        )
        statuses.append(response.status_code)

    # 10/minute per account, well under the 60/minute per IP.
    assert 429 in statuses
    assert statuses.index(429) < LOGIN_IP_MAX
