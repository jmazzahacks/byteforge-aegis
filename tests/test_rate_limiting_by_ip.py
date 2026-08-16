"""
Per-IP rate limiting, which catches what per-account limits cannot.

A spread attack — one guess each against ten thousand accounts — stays under
every per-account limit forever. These tests use a DIFFERENT email on each
request specifically so the per-account limit cannot be what fires; if it
were, the test would pass while proving nothing.
"""
import pytest

from utils.rate_limit import CLIENT_IP_HEADER, PASSWORD_RESET_IP_LIMIT, limiter

TENANT_HEADER = 'X-Tenant-Api-Key'
ATTACKER_IP = '203.0.113.7'
OTHER_IP = '198.51.100.9'

# Parsed from the configured limit so the tests cannot drift away from it.
RESET_IP_MAX = int(PASSWORD_RESET_IP_LIMIT.split()[0])

# The spread-attack tests drive PASSWORD RESET rather than LOGIN. Two
# reasons, both about the test rather than about which endpoint matters:
#
#   Flakiness — flask-limiter uses fixed windows, so a login test needs 60+
#   requests inside one wall-clock MINUTE, and a run straddling the boundary
#   sees the counter reset and no 429 at all. Observed as a real flake.
#   Reset's limit is 20 per HOUR: fewer requests, and a boundary ~60x less
#   likely to be crossed mid-test.
#
#   Speed — reset for an unknown address returns after one indexed SELECT.
#   Login and register both bcrypt, and at cost 12 that is ~250ms per
#   request, which put 15 seconds on every future suite run.
#
# The keying and exemption logic under test is shared across all three.


@pytest.fixture(autouse=True)
def reset_limiter(test_client):
    limiter.reset()
    yield
    limiter.reset()


def _spread_request(client, site, index, client_ip=None):
    """One request against a DISTINCT account, so the per-account limit
    can never be what fires."""
    headers = {TENANT_HEADER: site.tenant_api_key}
    if client_ip is not None:
        headers[CLIENT_IP_HEADER] = client_ip

    return client.post(
        '/api/auth/request-password-reset',
        headers=headers,
        json={'site_id': site.uuid,
              'email': f'victim{index}@test.example.com'},
    )


def test_spread_attack_from_one_ip_is_blocked(test_client, sample_site):
    """The attack per-account limits miss entirely."""
    statuses = [
        _spread_request(test_client, sample_site, i, ATTACKER_IP).status_code
        for i in range(RESET_IP_MAX + 5)
    ]

    assert 429 in statuses, (
        f'{len(statuses)} attempts against {len(statuses)} different accounts '
        f'from one IP were never limited'
    )
    # Must be the IP limit firing, not the per-account one — every request
    # used a different address, so per-account never reaches 2.
    assert statuses.index(429) >= RESET_IP_MAX


def test_a_different_ip_is_a_separate_bucket(test_client, sample_site):
    """One blocked attacker must not lock out unrelated users."""
    for i in range(RESET_IP_MAX + 5):
        _spread_request(test_client, sample_site, i, ATTACKER_IP)

    innocent = _spread_request(test_client, sample_site, 9999, OTHER_IP)

    assert innocent.status_code != 429


def test_no_ip_header_means_no_ip_limiting(test_client, sample_site):
    """Absent header must SKIP the limit, not collapse everyone into one
    bucket — that would take logins down for every tenant at once."""
    statuses = [
        _spread_request(test_client, sample_site, i).status_code
        for i in range(RESET_IP_MAX + 5)
    ]

    assert 429 not in statuses, (
        'requests with no client-IP header were rate limited together — '
        'they are sharing a bucket, which is the failure this avoids'
    )

# Deleted 2026-08-16: test_per_account_limit_still_applies_independently.
# It drove 12 logins against the 10/minute PER-ACCOUNT limit with the client-IP
# header set, and it was the only flaky test in the suite — fixed windows mean a
# minute boundary splitting the run leaves <=10 on each side and no 429 fires.
# It went rather than getting propped up because it earned nothing:
# test_rate_limiting.py::test_repeated_login_attempts_are_eventually_blocked
# already asserts the account limit fires; the only increment was "it still
# fires when the IP header is present", which would require flask-limiter to
# apply one @limiter.limit decorator and skip another based on inbound headers.
# Its second assertion (index(429) < LOGIN_IP_MAX) compared an index into a
# 12-element list against 60 — unfailable, and the tell that it wasn't load
# bearing. It was the only reader of LOGIN_IP_MAX/LOGIN_IP_LIMIT, both removed
# with it; the remaining tests are all keyed off the reset limit.
