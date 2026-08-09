"""
Tests for durable webhook delivery: the outbox, retries, and the log.

Delivery used to be at-most-once — one POST, no retry, and the only record
written AFTER the attempt. A non-2xx lost the event outright, and anything
queued in the thread pool when the container rotated vanished with no row
at all, so every deploy was a silent loss window.

The properties that fix costs are asserted here directly: the outbox row
exists before any HTTP happens, a failure stays owed rather than
disappearing, retries are bounded, and two workers cannot deliver the same
event twice.
"""
import time

import pytest

from byteforge_aegis_models import WebhookEventType, WebhookPayload
from database import db_manager
from models.webhook_delivery import (
    STATUS_DELIVERED, STATUS_EXHAUSTED, STATUS_PENDING,
)
from services import webhook_service as webhook_service_module
from services.webhook_service import (
    CLAIM_LEASE_SECONDS, MAX_ATTEMPTS, RETRY_BACKOFF_SECONDS, webhook_service,
)
from utils.uuid7 import generate_uuid7

TENANT_URL = "http://tenant.example.com/hook"


class FakeResponse:
    def __init__(self, status_code=200, text='ok'):
        self.status_code = status_code
        self.text = text


@pytest.fixture
def hooked_site(sample_site):
    """A site with webhooks configured."""
    sample_site.webhook_url = TENANT_URL
    sample_site.webhook_secret = "a" * 64
    return db_manager.update_site(sample_site)


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


def _stub_post(monkeypatch, responder):
    """Replace requests.post, recording every call."""
    calls = []

    def fake_post(url, data, headers, timeout, allow_redirects):
        assert allow_redirects is False, 'a redirect would re-send a signed body'
        calls.append({'url': url, 'data': data, 'headers': headers})
        return responder(len(calls))

    monkeypatch.setattr(webhook_service_module.requests, 'post', fake_post)
    return calls


def _ok(_n):
    return FakeResponse()


def _server_error(_n):
    return FakeResponse(status_code=500, text='boom')


# --- durability: the row exists before the attempt -------------------------

def test_delivery_is_persisted_before_any_http(hooked_site, monkeypatch):
    """The property the whole table exists for.

    If the process dies here, the event must still be owed. Asserting from
    inside the POST is the only way to prove the write happened first.
    """
    seen = {}

    def assert_persisted_midflight(url, data, headers, timeout, allow_redirects):
        rows = db_manager.find_webhook_delivery(seen['event_id'])
        seen['persisted_during_post'] = rows is not None
        return FakeResponse()

    monkeypatch.setattr(
        webhook_service_module.requests, 'post', assert_persisted_midflight
    )

    payload = _make_payload(hooked_site.uuid)
    seen['event_id'] = payload.event_id

    webhook_service.send_webhook(hooked_site, payload)

    assert seen.get('persisted_during_post') is True, \
        'the outbox row must exist before the POST, or a crash loses the event'


def test_successful_delivery_is_marked_delivered(hooked_site, monkeypatch):
    _stub_post(monkeypatch, _ok)
    payload = _make_payload(hooked_site.uuid)

    webhook_service.send_webhook(hooked_site, payload)

    delivery = db_manager.find_webhook_delivery(payload.event_id)
    assert delivery.status == STATUS_DELIVERED
    assert delivery.attempts == 1


def test_failed_delivery_stays_owed(hooked_site, monkeypatch):
    """The old behaviour dropped this event entirely."""
    _stub_post(monkeypatch, _server_error)
    payload = _make_payload(hooked_site.uuid)

    webhook_service.send_webhook(hooked_site, payload)

    delivery = db_manager.find_webhook_delivery(payload.event_id)
    assert delivery.status == STATUS_PENDING
    assert delivery.last_status == 500
    assert delivery.next_attempt_at > int(time.time()), 'must be scheduled, not due now'


def test_transport_failure_also_stays_owed(hooked_site, monkeypatch):
    """A timeout or DNS failure is a retry, not a loss."""
    import requests as requests_module

    def explode(*args, **kwargs):
        raise requests_module.exceptions.ConnectTimeout('no route')

    monkeypatch.setattr(webhook_service_module.requests, 'post', explode)
    payload = _make_payload(hooked_site.uuid)

    webhook_service.send_webhook(hooked_site, payload)

    delivery = db_manager.find_webhook_delivery(payload.event_id)
    assert delivery.status == STATUS_PENDING
    assert delivery.last_status is None
    assert 'no route' in delivery.last_error


# --- the sweep -------------------------------------------------------------

def _make_due(event_id: str) -> None:
    """Pull a scheduled retry forward so the sweep will claim it."""
    with db_manager.get_cursor(commit=True) as cursor:
        cursor.execute(
            'UPDATE webhook_deliveries SET next_attempt_at = %s WHERE event_id = %s',
            (int(time.time()) - 1, event_id)
        )


def test_sweep_retries_a_failed_delivery(hooked_site, monkeypatch):
    """First attempt fails, retry succeeds — the event is not lost."""
    calls = _stub_post(monkeypatch, lambda n: _ok(n) if n > 1 else _server_error(n))
    payload = _make_payload(hooked_site.uuid)

    webhook_service.send_webhook(hooked_site, payload)
    _make_due(payload.event_id)
    result = webhook_service.deliver_pending()

    assert result.attempted == 1
    assert result.delivered == 1
    assert len(calls) == 2
    assert db_manager.find_webhook_delivery(payload.event_id).status == STATUS_DELIVERED


def test_sweep_ignores_deliveries_not_yet_due(hooked_site, monkeypatch):
    """Backoff must actually hold the row back."""
    _stub_post(monkeypatch, _server_error)
    payload = _make_payload(hooked_site.uuid)

    webhook_service.send_webhook(hooked_site, payload)
    result = webhook_service.deliver_pending()

    assert result.attempted == 0


def test_delivery_is_given_up_on_after_max_attempts(hooked_site, monkeypatch):
    """Retrying forever would be its own outage. Bounded, then terminal."""
    calls = _stub_post(monkeypatch, _server_error)
    payload = _make_payload(hooked_site.uuid)

    webhook_service.send_webhook(hooked_site, payload)
    for _ in range(MAX_ATTEMPTS):
        _make_due(payload.event_id)
        webhook_service.deliver_pending()

    delivery = db_manager.find_webhook_delivery(payload.event_id)
    assert delivery.status == STATUS_EXHAUSTED
    assert delivery.attempts == MAX_ATTEMPTS
    assert len(calls) == MAX_ATTEMPTS


def test_exhausted_deliveries_are_never_retried_again(hooked_site, monkeypatch):
    _stub_post(monkeypatch, _server_error)
    payload = _make_payload(hooked_site.uuid)
    webhook_service.send_webhook(hooked_site, payload)
    for _ in range(MAX_ATTEMPTS):
        _make_due(payload.event_id)
        webhook_service.deliver_pending()

    _make_due(payload.event_id)
    result = webhook_service.deliver_pending()

    assert result.attempted == 0


def test_backoff_grows_between_attempts(hooked_site, monkeypatch):
    _stub_post(monkeypatch, _server_error)
    payload = _make_payload(hooked_site.uuid)

    webhook_service.send_webhook(hooked_site, payload)
    first_wait = db_manager.find_webhook_delivery(
        payload.event_id
    ).next_attempt_at - int(time.time())

    _make_due(payload.event_id)
    webhook_service.deliver_pending()
    second_wait = db_manager.find_webhook_delivery(
        payload.event_id
    ).next_attempt_at - int(time.time())

    assert second_wait > first_wait
    assert first_wait <= RETRY_BACKOFF_SECONDS[0]


def test_a_deleted_site_stops_being_owed(hooked_site, monkeypatch):
    """Otherwise the row is retried until exhausted against nothing."""
    _stub_post(monkeypatch, _server_error)
    payload = _make_payload(hooked_site.uuid)
    webhook_service.send_webhook(hooked_site, payload)

    hooked_site.webhook_url = None
    db_manager.update_site(hooked_site)

    _make_due(payload.event_id)
    result = webhook_service.deliver_pending()

    assert result.exhausted == 1
    assert db_manager.find_webhook_delivery(payload.event_id).status == STATUS_EXHAUSTED


def test_a_non_requests_exception_costs_exactly_one_attempt(hooked_site, monkeypatch):
    """One POST must never be counted as two.

    requests can raise outside its own hierarchy on a malformed tenant URL
    (a bad IDNA hostname surfaces as UnicodeError). When the sweep caught
    that at the loop level it settled the row a SECOND time, so a single
    send burned two attempts and events retired after half their real
    tries.
    """
    def raise_outside_requests(*args, **kwargs):
        raise UnicodeError('label empty or too long')

    monkeypatch.setattr(
        webhook_service_module.requests, 'post', raise_outside_requests
    )
    payload = _make_payload(hooked_site.uuid)

    webhook_service.send_webhook(hooked_site, payload)

    delivery = db_manager.find_webhook_delivery(payload.event_id)
    assert delivery.attempts == 1, 'one POST was counted as more than one attempt'
    assert delivery.status == STATUS_PENDING
    assert 'UnicodeError' in delivery.last_error


def test_one_poison_row_does_not_stop_the_sweep(hooked_site, monkeypatch):
    """A tenant-supplied URL that always raises must not block everyone.

    Unisolated, one such row aborts the sweep for every other tenant, on
    every run, forever — the outage is global and permanent.
    """
    poison = _make_payload(hooked_site.uuid)
    healthy = _make_payload(hooked_site.uuid)

    def selective(url, data, headers, timeout, allow_redirects):
        if poison.event_id in data:
            raise UnicodeError('label empty or too long')
        return FakeResponse(status_code=500)

    monkeypatch.setattr(webhook_service_module.requests, 'post', selective)
    webhook_service.send_webhook(hooked_site, poison)
    webhook_service.send_webhook(hooked_site, healthy)
    _make_due(poison.event_id)
    _make_due(healthy.event_id)

    result = webhook_service.deliver_pending()

    assert result.attempted == 2, 'the poison row aborted the sweep'
    assert db_manager.find_webhook_delivery(healthy.event_id).attempts == 2


# --- claiming --------------------------------------------------------------

def test_a_claimed_delivery_is_not_claimed_twice(hooked_site, monkeypatch):
    """Two concurrent sweeps must take disjoint rows, not double-deliver."""
    _stub_post(monkeypatch, _server_error)
    payload = _make_payload(hooked_site.uuid)
    webhook_service.send_webhook(hooked_site, payload)
    _make_due(payload.event_id)

    now = int(time.time())
    first = db_manager.claim_webhook_deliveries(now, CLAIM_LEASE_SECONDS, 10)
    second = db_manager.claim_webhook_deliveries(now, CLAIM_LEASE_SECONDS, 10)

    assert len(first) == 1
    assert second == [], 'the same delivery was claimed by two callers'


def test_a_claim_lease_expires_so_a_dead_worker_releases_its_row(hooked_site, monkeypatch):
    """There is no in-flight state to reap — the lease IS the recovery.

    A worker killed mid-POST leaves the row claimed. It must become due
    again on its own, or that event is stuck forever.
    """
    _stub_post(monkeypatch, _server_error)
    payload = _make_payload(hooked_site.uuid)
    webhook_service.send_webhook(hooked_site, payload)
    _make_due(payload.event_id)

    now = int(time.time())
    claimed = db_manager.claim_webhook_deliveries(now, CLAIM_LEASE_SECONDS, 10)
    assert len(claimed) == 1
    # ...worker dies here, writing no outcome.

    after_lease = now + CLAIM_LEASE_SECONDS + 1
    reclaimed = db_manager.claim_webhook_deliveries(after_lease, CLAIM_LEASE_SECONDS, 10)

    assert len(reclaimed) == 1, 'a dead worker stranded the delivery'


# --- signing ---------------------------------------------------------------

def test_each_attempt_is_signed_with_a_fresh_timestamp(hooked_site, monkeypatch):
    """Retries would otherwise be rejected as stale.

    Receivers refuse an old X-Aegis-Timestamp (the reference verifier
    allows 300s), so re-sending under the original timestamp would fail
    every correct receiver once the backoff exceeded their tolerance.
    """
    calls = _stub_post(monkeypatch, _server_error)
    payload = _make_payload(hooked_site.uuid)

    webhook_service.send_webhook(hooked_site, payload)
    _make_due(payload.event_id)
    with_frozen_clock = int(time.time()) + 400
    monkeypatch.setattr(webhook_service_module.time, 'time', lambda: with_frozen_clock)
    webhook_service.deliver_pending()

    first_ts = int(calls[0]['headers']['X-Aegis-Timestamp'])
    second_ts = int(calls[1]['headers']['X-Aegis-Timestamp'])
    assert second_ts > first_ts
    assert second_ts == with_frozen_clock


def test_the_body_is_byte_identical_across_attempts(hooked_site, monkeypatch):
    """The signature covers these exact bytes; re-serializing could reorder
    keys and produce a body that does not match its own signature."""
    calls = _stub_post(monkeypatch, _server_error)
    payload = _make_payload(hooked_site.uuid)

    webhook_service.send_webhook(hooked_site, payload)
    _make_due(payload.event_id)
    webhook_service.deliver_pending()

    assert calls[0]['data'] == calls[1]['data']


def test_the_signature_verifies_against_the_sent_body(hooked_site, monkeypatch):
    calls = _stub_post(monkeypatch, _ok)
    payload = _make_payload(hooked_site.uuid)

    webhook_service.send_webhook(hooked_site, payload)

    sent = calls[0]
    expected = webhook_service.compute_signature(
        hooked_site.webhook_secret,
        int(sent['headers']['X-Aegis-Timestamp']),
        sent['data'],
    )
    assert sent['headers']['X-Aegis-Signature'] == f'sha256={expected}'


# --- the attempt log -------------------------------------------------------

def test_the_log_is_greppable_by_the_tenant_reported_event_id(hooked_site, monkeypatch):
    """event_id moved off the row's uuid when rows became per-attempt. It is
    the value a tenant quotes, so it has to stay findable."""
    _stub_post(monkeypatch, _ok)
    payload = _make_payload(hooked_site.uuid)

    webhook_service.send_webhook(hooked_site, payload)

    events = db_manager.list_webhook_events_by_site(hooked_site.uuid)
    assert len(events) == 1
    assert events[0].event_id == payload.event_id
    assert events[0].uuid != payload.event_id, 'the row id is per-attempt now'
    assert events[0].success is True


def test_every_attempt_is_logged_separately(hooked_site, monkeypatch):
    """One row per attempt — the old schema could not represent this at all,
    because uuid was the event id and the second attempt collided on it."""
    _stub_post(monkeypatch, _server_error)
    payload = _make_payload(hooked_site.uuid)

    webhook_service.send_webhook(hooked_site, payload)
    _make_due(payload.event_id)
    webhook_service.deliver_pending()

    events = db_manager.list_webhook_events_by_site(hooked_site.uuid)
    assert len(events) == 2
    assert sorted(e.attempt for e in events) == [1, 2]
    assert {e.event_id for e in events} == {payload.event_id}


# --- sites without webhooks ------------------------------------------------

def test_a_site_without_a_webhook_url_queues_nothing(sample_site, monkeypatch):
    def forbid(*args, **kwargs):
        raise AssertionError('must not POST for an unconfigured site')

    monkeypatch.setattr(webhook_service_module.requests, 'post', forbid)
    payload = _make_payload(sample_site.uuid)

    webhook_service.send_webhook(sample_site, payload)

    assert db_manager.find_webhook_delivery(payload.event_id) is None
