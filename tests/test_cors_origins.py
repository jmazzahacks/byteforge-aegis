"""
CORS must allow tenant frontends and nobody else.

The policy was '*' — and env.docker.example shipped that value, so it was
the deployed policy rather than a dev default. These assert the actual
response headers, because a misconfigured allow-list looks identical to a
working one until an attacker's page is the caller.
"""
import time

import pytest

from database import db_manager
from byteforge_aegis_models import Site
from utils.cors_origins import _origin_of, allowed_origins
from utils.uuid7 import generate_uuid7

TENANT_ORIGIN = 'http://test.example.com'
ATTACKER_ORIGIN = 'https://evil.example.com'


@pytest.fixture(autouse=True)
def fresh_origin_cache():
    """The cache is process-global and TTL'd; tests must not inherit it."""
    allowed_origins(force=True)
    yield
    allowed_origins(force=True)


def _cors_header(response):
    return response.headers.get('Access-Control-Allow-Origin')


def test_tenant_frontend_origin_is_allowed(test_client, sample_site):
    """sample_site's frontend_url is http://test.example.com."""
    response = test_client.get('/api/health', headers={'Origin': TENANT_ORIGIN})

    assert _cors_header(response) == TENANT_ORIGIN


def test_unknown_origin_is_not_allowed(test_client, sample_site):
    response = test_client.get('/api/health', headers={'Origin': ATTACKER_ORIGIN})

    assert _cors_header(response) != ATTACKER_ORIGIN
    assert _cors_header(response) != '*'


def test_preflight_from_an_unknown_origin_is_not_allowed(test_client, sample_site):
    response = test_client.options(
        '/api/auth/login',
        headers={
            'Origin': ATTACKER_ORIGIN,
            'Access-Control-Request-Method': 'POST',
        },
    )

    assert _cors_header(response) != ATTACKER_ORIGIN
    assert _cors_header(response) != '*'


def test_a_new_tenant_is_allowed_without_a_restart(test_client, sample_site):
    """The point of deriving from the database rather than an env list."""
    now = int(time.time())
    db_manager.create_site(Site(
        uuid=generate_uuid7(),
        name='Newly Added',
        domain='newtenant.example.com',
        frontend_url='https://newtenant.example.com',
        email_from='noreply@newtenant.example.com',
        email_from_name='Newly Added',
        created_at=now,
        updated_at=now,
        tenant_api_key='newtenant_key_64chars_ccccccccccccccccccccccccccccccccccc',
    ))
    allowed_origins(force=True)  # stand in for the TTL elapsing

    response = test_client.get(
        '/api/health', headers={'Origin': 'https://newtenant.example.com'}
    )

    assert _cors_header(response) == 'https://newtenant.example.com'


def test_origin_is_scheme_and_host_only():
    """A frontend_url carries a path; an Origin header never does."""
    assert _origin_of('https://app.example.com/login?next=/x') == 'https://app.example.com'
    assert _origin_of('https://app.example.com:8443/') == 'https://app.example.com:8443'
    assert _origin_of('not a url') == ''
    assert _origin_of('') == ''
