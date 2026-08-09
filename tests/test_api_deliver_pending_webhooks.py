"""
Tests for the webhook retry sweep endpoint.

This endpoint is what makes retries actually happen — the outbox rows are
inert without something to drive them. It runs unattended on a schedule, so
the failure that matters is the quiet one: a sweep that stops claiming work
leaves events owed forever and nothing says so. `still_due` in the response
is the signal for that, and is asserted here.
"""
import time

import pytest

from byteforge_aegis_models import WebhookEventType, WebhookPayload
from config import get_config
from database import db_manager
from models.webhook_delivery import STATUS_DELIVERED
from services import webhook_service as webhook_service_module
from services.webhook_service import webhook_service
from utils.uuid7 import generate_uuid7

ENDPOINT = '/api/admin/deliver-pending-webhooks'

# Captured before any test monkeypatches the service's clock.
real_time = time.time


@pytest.fixture
def master_headers():
    return {'X-API-Key': get_config().MASTER_API_KEY}


@pytest.fixture
def hooked_site(sample_site):
    sample_site.webhook_url = 'http://tenant.example.com/hook'
    sample_site.webhook_secret = 'a' * 64
    return db_manager.update_site(sample_site)


class FakeResponse:
    def __init__(self, status_code=200, text='ok'):
        self.status_code = status_code
        self.text = text


def _queue_failed_delivery(site, monkeypatch) -> str:
    """Leave one delivery owed and due, as a failed first attempt would."""
    monkeypatch.setattr(
        webhook_service_module.requests, 'post',
        lambda *a, **k: FakeResponse(status_code=500, text='boom')
    )
    payload = WebhookPayload(
        event_id=generate_uuid7(),
        event_type=WebhookEventType.USER_VERIFIED,
        site_uuid=site.uuid,
        user_uuid=generate_uuid7(),
        email='owed@example.com',
        aegis_role='user',
        timestamp=int(time.time()),
    )
    webhook_service.send_webhook(site, payload)

    with db_manager.get_cursor(commit=True) as cursor:
        cursor.execute(
            'UPDATE webhook_deliveries SET next_attempt_at = %s WHERE event_id = %s',
            (int(time.time()) - 1, payload.event_id)
        )
    return payload.event_id


def test_requires_the_master_key(test_client):
    response = test_client.post(ENDPOINT)
    assert response.status_code == 401


def test_rejects_a_wrong_master_key(test_client):
    response = test_client.post(ENDPOINT, headers={'X-API-Key': 'nope'})
    assert response.status_code == 401


def test_a_tenant_key_cannot_drive_the_sweep(test_client, hooked_site):
    """Tenant keys reach the public auth surface, never operator endpoints."""
    response = test_client.post(
        ENDPOINT, headers={'X-Tenant-Api-Key': hooked_site.tenant_api_key}
    )
    assert response.status_code == 401


def test_sweep_delivers_an_owed_event(test_client, hooked_site, master_headers, monkeypatch):
    event_id = _queue_failed_delivery(hooked_site, monkeypatch)
    monkeypatch.setattr(
        webhook_service_module.requests, 'post', lambda *a, **k: FakeResponse()
    )

    response = test_client.post(ENDPOINT, headers=master_headers)

    assert response.status_code == 200
    body = response.get_json()
    assert body['attempted'] == 1
    assert body['delivered'] == 1
    assert body['still_due'] == 0
    assert db_manager.find_webhook_delivery(event_id).status == STATUS_DELIVERED


def test_sweep_reports_work_left_behind(test_client, hooked_site, master_headers, monkeypatch):
    """A delivery that fails again is still owed, and the response says so
    rather than reporting a clean run."""
    _queue_failed_delivery(hooked_site, monkeypatch)

    response = test_client.post(ENDPOINT, headers=master_headers)

    body = response.get_json()
    assert body['delivered'] == 0
    assert body['retrying'] == 1


def test_sweep_with_nothing_to_do_is_a_no_op(test_client, master_headers):
    """It runs every minute; the common case must be cheap and quiet."""
    response = test_client.post(ENDPOINT, headers=master_headers)

    assert response.status_code == 200
    assert response.get_json() == {
        'attempted': 0, 'delivered': 0, 'retrying': 0,
        'exhausted': 0, 'still_due': 0, 'pruned': 0,
    }


def test_a_sweep_does_not_touch_rows_another_sweep_still_holds(
    test_client, hooked_site, master_headers, monkeypatch
):
    """The overlap that actually happens: a slow sweep still working.

    A cron job whose previous run has not finished is the normal case, not
    a rare one, so a second sweep must not re-claim rows the first is
    mid-POST on. This is the property the claim lease exists for, and it
    only holds while the lease outlives a full batch.
    """
    event_id = _queue_failed_delivery(hooked_site, monkeypatch)

    # Sweep A claims the row and is still working; nothing is finished yet.
    claimed = db_manager.claim_webhook_deliveries(
        int(time.time()), webhook_service_module.CLAIM_LEASE_SECONDS, 10
    )
    assert len(claimed) == 1

    posts = []
    monkeypatch.setattr(
        webhook_service_module.requests, 'post',
        lambda *a, **k: (posts.append(1), FakeResponse())[1]
    )
    body = test_client.post(ENDPOINT, headers=master_headers).get_json()

    assert body['attempted'] == 0, 'a held row was re-claimed by a second sweep'
    assert posts == [], 'the same event would have been delivered twice'


def test_a_claim_that_is_never_finished_is_retried_after_the_lease(
    test_client, hooked_site, master_headers, monkeypatch
):
    """The other half: a worker that dies must not strand the event."""
    _queue_failed_delivery(hooked_site, monkeypatch)
    db_manager.claim_webhook_deliveries(
        int(time.time()), webhook_service_module.CLAIM_LEASE_SECONDS, 10
    )
    # ...that worker dies, writing no outcome.

    monkeypatch.setattr(
        webhook_service_module.requests, 'post', lambda *a, **k: FakeResponse()
    )
    monkeypatch.setattr(
        webhook_service_module.time, 'time',
        lambda: real_time() + webhook_service_module.CLAIM_LEASE_SECONDS + 1
    )
    body = test_client.post(ENDPOINT, headers=master_headers).get_json()

    assert body['attempted'] == 1
    assert body['delivered'] == 1


def test_an_abandoned_claim_does_not_burn_an_attempt(hooked_site, monkeypatch):
    """Claiming is not attempting.

    Counting at claim time meant a sweep killed partway through burned
    attempts on rows it never POSTed — six interruptions and an event was
    retired as undeliverable without having been sent once.
    """
    event_id = _queue_failed_delivery(hooked_site, monkeypatch)
    before = db_manager.find_webhook_delivery(event_id).attempts

    db_manager.claim_webhook_deliveries(
        int(time.time()), webhook_service_module.CLAIM_LEASE_SECONDS, 10
    )

    after = db_manager.find_webhook_delivery(event_id).attempts
    assert after == before, 'a claim with no attempt consumed one'


def test_a_late_worker_cannot_resurrect_a_delivered_event(hooked_site, monkeypatch):
    """A worker whose lease expired must not overwrite the outcome.

    Otherwise its late failure flips a delivered row back to pending and
    the tenant is sent the event again.
    """
    event_id = _queue_failed_delivery(hooked_site, monkeypatch)
    delivery = db_manager.find_webhook_delivery(event_id)
    stale_lease = delivery.next_attempt_at

    # Another worker re-claimed and delivered it in the meantime.
    reclaimed = db_manager.claim_webhook_deliveries(
        delivery.next_attempt_at, webhook_service_module.CLAIM_LEASE_SECONDS, 10
    )
    db_manager.finish_webhook_delivery(
        event_id, STATUS_DELIVERED, int(time.time()),
        reclaimed[0].next_attempt_at, 200, None, reclaimed[0].next_attempt_at
    )

    owned = db_manager.finish_webhook_delivery(
        event_id, 'pending', int(time.time()),
        int(time.time()) + 30, 500, 'late failure', stale_lease
    )

    assert owned is False, 'the late write should have been refused'
    assert db_manager.find_webhook_delivery(event_id).status == STATUS_DELIVERED


def test_a_late_worker_cannot_overwrite_a_still_pending_winner(hooked_site, monkeypatch):
    """The lost-lease race that actually happens.

    Most such races are between two FAILING attempts, which leave the row
    pending — so a guard that only refuses terminal rows would accept the
    loser's late write, double-count the attempt, clobber the winner's
    error, and drag next_attempt_at backwards into a sooner retry.
    """
    event_id = _queue_failed_delivery(hooked_site, monkeypatch)
    stale = db_manager.find_webhook_delivery(event_id)

    # The winner re-claims after the lease and also fails: still pending.
    winner = db_manager.claim_webhook_deliveries(
        stale.next_attempt_at, webhook_service_module.CLAIM_LEASE_SECONDS, 10
    )[0]
    db_manager.finish_webhook_delivery(
        event_id, 'pending', int(time.time()), int(time.time()) + 600,
        503, 'winner failure', winner.next_attempt_at
    )
    after_winner = db_manager.find_webhook_delivery(event_id)

    owned = db_manager.finish_webhook_delivery(
        event_id, 'pending', int(time.time()), int(time.time()) + 30,
        500, 'loser failure', stale.next_attempt_at
    )

    assert owned is False, 'a stale lease was accepted as ownership'
    current = db_manager.find_webhook_delivery(event_id)
    assert current.attempts == after_winner.attempts, 'one POST, two attempts counted'
    assert current.last_error == 'winner failure', "the loser clobbered the winner's error"
    assert current.next_attempt_at == after_winner.next_attempt_at, \
        'the retry was pulled earlier, making another duplicate likelier'


def test_old_delivered_rows_are_pruned(test_client, hooked_site, master_headers, monkeypatch):
    """The outbox holds a full payload per webhook ever sent."""
    monkeypatch.setattr(
        webhook_service_module.requests, 'post', lambda *a, **k: FakeResponse()
    )
    payload = WebhookPayload(
        event_id=generate_uuid7(), event_type=WebhookEventType.USER_VERIFIED,
        site_uuid=hooked_site.uuid, user_uuid=generate_uuid7(),
        email='old@example.com', aegis_role='user', timestamp=int(time.time()),
    )
    webhook_service.send_webhook(hooked_site, payload)

    ancient = int(time.time()) - webhook_service_module.DELIVERED_RETENTION_SECONDS - 1
    with db_manager.get_cursor(commit=True) as cursor:
        cursor.execute(
            'UPDATE webhook_deliveries SET updated_at = %s WHERE event_id = %s',
            (ancient, payload.event_id)
        )

    body = test_client.post(ENDPOINT, headers=master_headers).get_json()

    assert body['pruned'] == 1
    assert db_manager.find_webhook_delivery(payload.event_id) is None


def test_exhausted_rows_are_kept_when_pruning(test_client, hooked_site, master_headers, monkeypatch):
    """They are the record of what a tenant never received."""
    event_id = _queue_failed_delivery(hooked_site, monkeypatch)
    ancient = int(time.time()) - webhook_service_module.DELIVERED_RETENTION_SECONDS - 1
    with db_manager.get_cursor(commit=True) as cursor:
        cursor.execute(
            "UPDATE webhook_deliveries SET status='exhausted', updated_at=%s "
            "WHERE event_id = %s",
            (ancient, event_id)
        )

    body = test_client.post(ENDPOINT, headers=master_headers).get_json()

    assert body['pruned'] == 0
    assert db_manager.find_webhook_delivery(event_id) is not None
