"""
Webhook delivery service for notifying tenant sites of events.

Uses HMAC-SHA256 signing so tenants can verify webhook authenticity.

Delivery is durable and retried. An event is written to the
`webhook_deliveries` outbox BEFORE any HTTP attempt, then attempted
immediately on a background thread; whatever the outcome, the row records
it. Failures are re-attempted on a backoff by `deliver_pending`, which a
cron job drives.

This replaced at-most-once delivery, where the only record of an event was
written AFTER the POST. That lost an event on any non-2xx — and lost it
with no trace at all if the container rotated while deliveries were queued
in the thread pool, which made every deploy a silent loss window.
"""
import hashlib
import hmac
import json
import logging
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Tuple

import requests

from database import db_manager
from byteforge_aegis_models import Site, WebhookEvent, WebhookPayload
from models.webhook_delivery import (
    STATUS_DELIVERED, STATUS_EXHAUSTED, STATUS_PENDING, WebhookDelivery,
)
from models.webhook_sweep_result import WebhookSweepResult
from utils.uuid7 import generate_uuid7

logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT_SECONDS = 5

# Delivery runs off the request thread, but bounded. Each delivery writes to
# the DB, so it draws from the SAME per-worker pool as request threads — an
# unbounded thread per event (a bulk delete, a registration burst) could take
# every spare connection and make requests for other tenants fail. Sized so
# request threads + this stay within the pool: gunicorn --threads 3 + 2 here
# <= DatabaseManager max_conn. Raising either means raising the pool, against
# a Postgres shared with other services.
WEBHOOK_DELIVERY_WORKERS = 2

# Wait before each retry, indexed by the attempt that just failed. Six
# attempts spanning ~7 hours: dense enough that a brief tenant restart costs
# seconds, patient enough to cover an outage somebody has to wake up for.
RETRY_BACKOFF_SECONDS = (30, 120, 600, 3600, 21600)
MAX_ATTEMPTS = len(RETRY_BACKOFF_SECONDS) + 1

# Ceiling on one sweep. Small on purpose: a sweep POSTs its batch serially,
# so the batch size times the worst-case request time is how long one sweep
# can run — and that has to fit inside the claim lease below, or a second
# sweep starts re-claiming rows the first is still working through.
DELIVERY_BATCH_LIMIT = 10

# How long a claim owns a row. Must exceed the time a full batch can take,
# not just one request: the timeout applies to connect AND read, so a
# blackholing tenant costs up to 2x per delivery.
#
#   worst case = DELIVERY_BATCH_LIMIT * WEBHOOK_TIMEOUT_SECONDS * 2 = 100s
#
# 300s leaves room to spare. Too short and overlapping sweeps double-deliver;
# too long and a genuinely dead worker's rows wait that long to be retried.
CLAIM_LEASE_SECONDS = 300

# Delivered rows are dropped after this. The outbox holds a full payload per
# webhook ever sent, so it would otherwise grow without bound. Exhausted rows
# are kept: they are the record of what a tenant never received.
DELIVERED_RETENTION_SECONDS = 30 * 24 * 60 * 60
PRUNE_BATCH_LIMIT = 500

_DELIVERY_POOL = ThreadPoolExecutor(
    max_workers=WEBHOOK_DELIVERY_WORKERS,
    thread_name_prefix='webhook-delivery',
)


class WebhookService:
    """Service for delivering signed webhooks to tenant sites."""

    def generate_webhook_secret(self) -> str:
        """
        Generate a cryptographically random webhook secret.

        Returns:
            str: 64-character hex string
        """
        return secrets.token_hex(32)

    def compute_signature(self, secret: str, timestamp: int, payload_json: str) -> str:
        """
        Compute HMAC-SHA256 signature over timestamp and payload.

        The signature covers both the timestamp and body to prevent
        replay attacks.

        Args:
            secret: The site's webhook secret
            timestamp: Unix timestamp being signed
            payload_json: JSON-serialized payload body

        Returns:
            str: Hex-encoded HMAC-SHA256 digest
        """
        message = f"{timestamp}.{payload_json}"
        return hmac.new(
            secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()

    def send_webhook(self, site: Site, payload: WebhookPayload) -> None:
        """
        Queue a webhook for delivery to the site's configured URL.

        The outbox row is written synchronously, before returning: that
        write is what makes the event survive a crash or a rotation. Only
        the HTTP attempt happens on a background thread.

        If the site has no webhook_url configured, this is a no-op.

        Args:
            site: The site to send the webhook to
            payload: The webhook payload to deliver
        """
        if not site.webhook_url or not site.webhook_secret:
            return

        now = int(time.time())
        delivery = WebhookDelivery(
            event_id=payload.event_id,
            site_uuid=site.uuid,
            event_type=payload.event_type.value,
            # Serialized once and stored verbatim. The signature is computed
            # over these exact bytes on every attempt, so re-serializing
            # later could reorder keys and produce a body that does not
            # match its own signature.
            payload=json.dumps(payload.to_dict(), separators=(',', ':')),
            status=STATUS_PENDING,
            attempts=0,
            next_attempt_at=now,
            created_at=now,
            updated_at=now,
        )

        try:
            db_manager.create_webhook_delivery(delivery)
        except Exception as e:
            # Nothing is queued if this fails, which is the honest outcome:
            # an event we cannot record is one we cannot promise to deliver.
            logger.error(
                "Failed to persist webhook delivery %s for site %s: %s",
                payload.event_id, site.uuid, e
            )
            return

        _DELIVERY_POOL.submit(self._attempt_now, site, payload.event_id)

    def _attempt_now(self, site: Site, event_id: str) -> None:
        """
        Make the immediate first attempt, if nothing else claimed it.

        Runs on a pool thread whose Future nobody inspects, so anything
        raised here would otherwise vanish entirely — including a pool
        exhaustion that stops the immediate attempt happening at all. The
        row is still safe (the sweep will find it), but the reason it fell
        back to the sweep has to reach the log or it is invisible.
        """
        try:
            now = int(time.time())
            delivery = db_manager.claim_webhook_delivery(
                event_id, now, CLAIM_LEASE_SECONDS
            )
            if delivery is None:
                # A sweep got there first; it will carry the attempt.
                return
            self._deliver(site, delivery)
        except Exception as e:
            logger.warning(
                "Immediate webhook attempt for event %s failed outside "
                "delivery; the retry sweep will pick it up: %s", event_id, e
            )

    def deliver_pending(
        self, limit: int = DELIVERY_BATCH_LIMIT
    ) -> WebhookSweepResult:
        """
        Attempt every delivery that is due. Driven by cron.

        Args:
            limit: Maximum deliveries to attempt in this sweep

        Returns:
            WebhookSweepResult: What the sweep did, and what is left.
        """
        started = time.time()
        now = int(started)
        claimed = db_manager.claim_webhook_deliveries(
            now, CLAIM_LEASE_SECONDS, limit
        )

        delivered = 0
        exhausted = 0
        attempted = 0
        for delivery in claimed:
            # Stop before the lease this sweep holds can expire underneath
            # it. Past that point another sweep may re-claim the rows we
            # have not reached, and two workers POSTing the same event is
            # exactly what the lease exists to prevent. The unreached rows
            # are simply left claimed; their lease lapses and the next
            # sweep takes them. Belt for anyone who later raises the batch
            # size or the timeout without redoing the arithmetic above.
            if time.time() - started > CLAIM_LEASE_SECONDS / 2:
                logger.warning(
                    'Webhook sweep stopped early after %.0fs with %s of %s '
                    'deliveries unattempted; the next sweep will take them',
                    time.time() - started, len(claimed) - attempted, len(claimed)
                )
                break

            attempted += 1
            # Isolated per delivery. webhook_url is tenant-supplied, and
            # some malformed values raise from inside requests before any
            # RequestException exists to catch (a bad IDNA hostname raises
            # UnicodeError). Unisolated, one such row would abort the sweep
            # for every other tenant, on every run, forever.
            outcome = self._deliver_claimed(delivery, now)

            if outcome == STATUS_DELIVERED:
                delivered += 1
            elif outcome == STATUS_EXHAUSTED:
                exhausted += 1

        return self._summarize_sweep(attempted, delivered, exhausted)

    def _summarize_sweep(
        self, attempted: int, delivered: int, exhausted: int
    ) -> WebhookSweepResult:
        """Count up the sweep, without letting bookkeeping sink it.

        Every delivery above is already committed by the time we get here.
        If the prune or the backlog count fails, the sweep still happened —
        raising would 500 the endpoint and lose the record of a run that
        worked, which is precisely the signal an operator alerts on.
        """
        pruned = 0
        try:
            pruned = db_manager.delete_settled_webhook_deliveries(
                int(time.time()) - DELIVERED_RETENTION_SECONDS,
                PRUNE_BATCH_LIMIT
            )
        except Exception as e:
            logger.error('Webhook delivery prune failed: %s', e)

        still_due = 0
        try:
            still_due = db_manager.count_pending_webhook_deliveries(
                int(time.time())
            )
        except Exception as e:
            logger.error('Could not count pending webhook deliveries: %s', e)

        result = WebhookSweepResult(
            attempted=attempted,
            delivered=delivered,
            retrying=attempted - delivered - exhausted,
            exhausted=exhausted,
            still_due=still_due,
            pruned=pruned,
        )
        if attempted:
            logger.info('Webhook sweep: %s', result.to_dict())
        return result

    def _deliver_claimed(self, delivery: WebhookDelivery, now: int) -> str:
        """
        Deliver a claimed row, or retire it if nobody is owed it.

        Never raises: a single poison row must not abort the sweep for
        every other tenant.
        """
        try:
            site = db_manager.find_site_by_uuid(delivery.site_uuid)
        except Exception as e:
            logger.error(
                'Could not load site %s for webhook %s: %s',
                delivery.site_uuid, delivery.event_id, e
            )
            return STATUS_PENDING

        if site is None or not site.webhook_url or not site.webhook_secret:
            # The site was deleted, or its webhook was unconfigured after
            # the event was raised. Nobody is owed this any more, and
            # retrying it to exhaustion would just be noise.
            owned = db_manager.finish_webhook_delivery(
                delivery.event_id, STATUS_EXHAUSTED, now,
                delivery.next_attempt_at, None,
                'site missing or webhook not configured',
                delivery.next_attempt_at
            )
            return STATUS_EXHAUSTED if owned else STATUS_PENDING

        return self._deliver(site, delivery)

    def _deliver(self, site: Site, delivery: WebhookDelivery) -> str:
        """
        POST one claimed delivery and record the outcome.

        Settles the row exactly once and never raises. Both matter: an
        exception escaping after the row was already settled would let the
        caller settle it a second time, double-counting the attempt and
        retiring events after half their real attempts.

        Args:
            site: The site to deliver to
            delivery: The claimed delivery. `attempts` is the count BEFORE
                this one — the claim no longer increments it.

        Returns:
            str: The delivery's resulting status, or STATUS_PENDING when
                the outcome could not be recorded at all.
        """
        # Signed with the CURRENT time, not the event's. Receivers reject a
        # stale X-Aegis-Timestamp (the reference verifier allows 300s), so
        # re-sending a retry under the original timestamp would be refused
        # by every correct receiver the moment the backoff exceeded their
        # tolerance. The event's own time stays in the signed body, so
        # nothing about the payload changes between attempts.
        signed_at = int(time.time())
        signature = self.compute_signature(
            site.webhook_secret, signed_at, delivery.payload
        )

        headers = {
            'Content-Type': 'application/json',
            'X-Aegis-Signature': f"sha256={signature}",
            'X-Aegis-Event': delivery.event_type,
            'X-Aegis-Timestamp': str(signed_at),
        }

        response_status = None
        response_body = None
        success = False
        attempt_number = delivery.attempts + 1

        try:
            # Redirects are refused: following one would re-send the signed
            # payload to a destination chosen by the receiver, not the
            # configured webhook_url. URL-level SSRF filtering is deliberately
            # absent — tenant backends are reached over tailscale/private
            # addresses, so internal IPs are legitimate destinations. If
            # tenant self-service webhook config is ever added, delivery-time
            # IP pinning must come with it.
            response = requests.post(
                site.webhook_url,
                data=delivery.payload,
                headers=headers,
                timeout=WEBHOOK_TIMEOUT_SECONDS,
                allow_redirects=False
            )
            response_status = response.status_code
            response_body = response.text[:1000]
            success = 200 <= response.status_code < 300
        except requests.exceptions.RequestException as e:
            response_body = str(e)[:1000]
            logger.warning(
                "Webhook attempt %s/%s failed for site %s (event %s): %s",
                attempt_number, MAX_ATTEMPTS, site.uuid,
                delivery.event_id, e
            )
        except Exception as e:
            # requests can raise outside its own hierarchy on a malformed
            # tenant URL — a bad IDNA hostname surfaces as UnicodeError.
            # Caught here so the row is still settled and the sweep carries
            # on; webhook_url is tenant-supplied, so this is reachable
            # without anything being wrong on our side.
            response_body = f'{type(e).__name__}: {e}'[:1000]
            logger.error(
                "Webhook attempt %s/%s raised for site %s (event %s): %s",
                attempt_number, MAX_ATTEMPTS, site.uuid, delivery.event_id, e
            )

        now = int(time.time())
        status, next_attempt_at = self._next_state(delivery, success, now)

        try:
            owned = db_manager.finish_webhook_delivery(
                delivery.event_id, status, now, next_attempt_at,
                response_status, response_body if not success else None,
                delivery.next_attempt_at
            )
        except Exception as e:
            # The attempt happened but could not be recorded. The lease
            # lapses and the row is retried, which may deliver twice —
            # better than a row nobody ever settles.
            logger.error(
                "Could not record webhook outcome for event %s: %s",
                delivery.event_id, e
            )
            return STATUS_PENDING

        # Logged whether or not we still owned the row: the POST really
        # happened, and a duplicate that reached the tenant is exactly the
        # delivery someone will come looking for in the audit log.
        self._log_attempt(
            delivery, attempt_number, response_status, response_body,
            success, now
        )

        if not owned:
            # The lease expired and another worker took the row while this
            # POST was in flight. The write was refused rather than allowed
            # to overwrite their outcome or drag next_attempt_at backwards.
            logger.warning(
                "Webhook attempt for event %s finished after losing its "
                "lease — the tenant may have received it twice. If this "
                "recurs, CLAIM_LEASE_SECONDS is too short for reality.",
                delivery.event_id
            )
            return STATUS_PENDING

        if status == STATUS_EXHAUSTED:
            logger.error(
                "Webhook GIVEN UP after %s attempts: site %s, event %s (%s). "
                "The tenant will never receive this event.",
                attempt_number, site.uuid, delivery.event_id,
                delivery.event_type
            )

        return status

    def _next_state(
        self, delivery: WebhookDelivery, success: bool, now: int
    ) -> Tuple[str, int]:
        """
        Decide what happens to a delivery after an attempt.

        Returns (status, next_attempt_at) — annotated so unpacking them the
        wrong way round is a type error rather than a row that retries in
        1970.

        `delivery.attempts` is the count BEFORE this attempt, since the
        claim no longer increments it, so the attempt just made is
        attempts + 1 and the first failure indexes backoff element 0.
        """
        if success:
            return STATUS_DELIVERED, delivery.next_attempt_at

        attempt_number = delivery.attempts + 1
        if attempt_number >= MAX_ATTEMPTS:
            return STATUS_EXHAUSTED, delivery.next_attempt_at

        return STATUS_PENDING, now + RETRY_BACKOFF_SECONDS[attempt_number - 1]

    def _log_attempt(
        self, delivery: WebhookDelivery, attempt_number: int,
        response_status: Optional[int], response_body: Optional[str],
        success: bool, now: int
    ) -> None:
        """Append this attempt to the audit log, never failing the delivery.

        The model is constructed inside the try too. `event_id` and
        `attempt` only exist in byteforge-aegis-models 2.7.0, and the image
        pulls that lib unpinned — so a cached build layer carrying 2.6.0
        makes this a TypeError rather than a DB error, and it would
        otherwise escape and take the delivery down with it.
        """
        try:
            event = WebhookEvent(
                uuid=generate_uuid7(),
                event_id=delivery.event_id,
                site_uuid=delivery.site_uuid,
                event_type=delivery.event_type,
                payload=delivery.payload,
                response_status=response_status,
                response_body=response_body,
                success=success,
                attempt=attempt_number,
                created_at=now,
            )
            db_manager.create_webhook_event(event)
        except Exception as e:
            logger.error(
                "Failed to log webhook attempt for site %s: %s",
                delivery.site_uuid, e
            )


# Global webhook service instance
webhook_service = WebhookService()
