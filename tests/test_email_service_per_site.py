"""
Tests for per-site Mailgun config in EmailService.

Covers the resolution logic: per-site values override globals, missing
per-site values fall back to globals, and a domain/from-address mismatch
emits a warning without aborting the send.
"""
from unittest.mock import patch
import pytest

from services.email_service import EmailService


@pytest.fixture
def svc():
    return EmailService()


@pytest.fixture
def fake_config():
    """Patch get_config to return controlled MAILGUN_* values."""
    class _Cfg:
        MAILGUN_DOMAIN = 'global.mg.example.com'
        MAILGUN_API_KEY = 'global-api-key'
        AEGIS_FRONTEND_URL = 'http://localhost:3000'
    with patch('services.email_service.get_config', return_value=_Cfg()):
        yield _Cfg


def test_per_site_values_override_global(svc, fake_config):
    domain, key = svc._resolve_mailgun_config(
        site_mailgun_domain='mg.tenant.com',
        site_mailgun_api_key='tenant-key',
    )
    assert domain == 'mg.tenant.com'
    assert key == 'tenant-key'


def test_falls_back_to_global_when_site_fields_null(svc, fake_config):
    domain, key = svc._resolve_mailgun_config(
        site_mailgun_domain=None,
        site_mailgun_api_key=None,
    )
    assert domain == 'global.mg.example.com'
    assert key == 'global-api-key'


def test_partial_per_site_falls_back_independently(svc, fake_config):
    """Each field falls back independently."""
    domain, key = svc._resolve_mailgun_config(
        site_mailgun_domain='mg.tenant.com',
        site_mailgun_api_key=None,
    )
    assert domain == 'mg.tenant.com'
    assert key == 'global-api-key'


def test_aligned_from_address_no_warning(svc, fake_config, caplog):
    with caplog.at_level('WARNING'):
        svc._resolve_mailgun_config(
            site_mailgun_domain='mg.tenant.com',
            site_mailgun_api_key='k',
            site_email_from='noreply@mg.tenant.com',
        )
    assert not any('mismatch' in r.message for r in caplog.records)


def test_subdomain_alignment_no_warning(svc, fake_config, caplog):
    """from on a subdomain of the sending domain (or vice versa) is fine."""
    with caplog.at_level('WARNING'):
        svc._resolve_mailgun_config(
            site_mailgun_domain='mg.tenant.com',
            site_mailgun_api_key='k',
            site_email_from='noreply@tenant.com',
        )
        # mg.tenant.com ends with .tenant.com OR sending_domain ends with from_domain
    # The from_domain "tenant.com" — sending "mg.tenant.com" ends with ".tenant.com"
    assert not any('mismatch' in r.message for r in caplog.records)


def test_mismatched_from_address_warns(svc, fake_config, caplog):
    with caplog.at_level('WARNING'):
        svc._resolve_mailgun_config(
            site_mailgun_domain='mg.aegis.com',
            site_mailgun_api_key='k',
            site_email_from='noreply@unrelated.com',
        )
    assert any('mismatch' in r.message.lower() for r in caplog.records)


def test_send_email_uses_per_site_url(svc, fake_config):
    """Verify send_email POSTs to the per-site Mailgun domain URL."""
    fake_response = type('R', (), {'status_code': 200, 'json': lambda self: {}, 'text': ''})()
    with patch('services.email_service.requests.post', return_value=fake_response) as mock_post:
        ok = svc.send_email(
            to_email='u@x.com',
            subject='s',
            html_content='<p>h</p>',
            from_email='noreply@mg.tenant.com',
            from_name='Tenant',
            mailgun_domain='mg.tenant.com',
            mailgun_api_key='tenant-key',
        )
    assert ok is True
    assert mock_post.call_args.args[0] == 'https://api.mailgun.net/v3/mg.tenant.com/messages'
    assert mock_post.call_args.kwargs['auth'] == ('api', 'tenant-key')


def test_send_email_falls_back_to_global(svc, fake_config):
    """Site without per-site config still sends via the global creds."""
    fake_response = type('R', (), {'status_code': 200, 'json': lambda self: {}, 'text': ''})()
    with patch('services.email_service.requests.post', return_value=fake_response) as mock_post:
        ok = svc.send_email(
            to_email='u@x.com',
            subject='s',
            html_content='<p>h</p>',
            from_email='noreply@global.mg.example.com',
            from_name='Global',
            mailgun_domain=None,
            mailgun_api_key=None,
        )
    assert ok is True
    assert mock_post.call_args.args[0] == 'https://api.mailgun.net/v3/global.mg.example.com/messages'
    assert mock_post.call_args.kwargs['auth'] == ('api', 'global-api-key')


def test_no_config_anywhere_returns_false(svc):
    """If neither per-site nor global Mailgun config exists, the send fails fast."""
    class _EmptyCfg:
        MAILGUN_DOMAIN = ''
        MAILGUN_API_KEY = ''
        AEGIS_FRONTEND_URL = ''
    with patch('services.email_service.get_config', return_value=_EmptyCfg()):
        ok = svc.send_email(
            to_email='u@x.com',
            subject='s',
            html_content='<p>h</p>',
            from_email='noreply@x.com',
            from_name='X',
        )
    assert ok is False
