"""
Tests for webhook delivery logging.
"""
import time

from byteforge_aegis_models import WebhookEventType, WebhookPayload
from services import webhook_service as webhook_service_module
from services.webhook_service import webhook_service
from utils.uuid7 import generate_uuid7


class FakeResponse:
    status_code = 200
    text = 'ok'


def _make_payload(site_uuid: str) -> WebhookPayload:
    return WebhookPayload(
        event_id=generate_uuid7(),
        event_type=WebhookEventType.USER_DELETED,
        site_uuid=site_uuid,
        user_uuid=generate_uuid7(),
        email="deleted@example.com",
        aegis_role="user",
        timestamp=int(time.time())
    )


def test_delivery_log_row_shares_payload_event_id(sample_site, monkeypatch):
    """The webhook_events row uuid must be the payload's event_id, so a
    tenant-reported event id is directly findable in the delivery log."""
    def fake_post(url, data, headers, timeout, allow_redirects):
        assert allow_redirects is False
        return FakeResponse()

    monkeypatch.setattr(webhook_service_module.requests, 'post', fake_post)

    sample_site.webhook_url = "http://tenant.example.com/hook"
    sample_site.webhook_secret = "a" * 64

    payload = _make_payload(sample_site.uuid)
    event = webhook_service._deliver_webhook(sample_site, payload)

    assert event is not None
    assert event.uuid == payload.event_id
    assert event.event_type == 'user.deleted'
    assert event.success is True
