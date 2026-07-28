"""
Webhook delivery service for notifying tenant sites of events.

Uses HMAC-SHA256 signing so tenants can verify webhook authenticity.
Delivers on background threads to avoid adding latency to the
triggering request.
"""
import hashlib
import hmac
import json
import logging
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import requests

from database import db_manager
from byteforge_aegis_models import Site, WebhookEvent, WebhookPayload

logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT_SECONDS = 5

# Delivery runs off the request thread, but bounded. Each delivery ends by
# writing a WebhookEvent row, so it draws from the SAME per-worker DB pool as
# request threads — an unbounded thread per event (a bulk delete, a
# registration burst) could take every spare connection and make requests for
# other tenants fail. Sized so request threads + this stay within the pool:
# gunicorn --threads 3 + 2 here <= DatabaseManager max_conn. Raising either
# means raising the pool, against a Postgres shared with other services.
WEBHOOK_DELIVERY_WORKERS = 2

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
            timestamp: Unix timestamp of the event
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
        Send a webhook to the site's configured URL on a background thread.

        If the site has no webhook_url configured, this is a no-op.
        Delivery results are logged to the webhook_events table.

        Args:
            site: The site to send the webhook to
            payload: The webhook payload to deliver
        """
        if not site.webhook_url or not site.webhook_secret:
            return

        _DELIVERY_POOL.submit(self._deliver_webhook, site, payload)

    def _deliver_webhook(self, site: Site, payload: WebhookPayload) -> Optional[WebhookEvent]:
        """
        Deliver a webhook and log the result. Runs on a background thread.

        Args:
            site: The site to deliver to
            payload: The payload to send

        Returns:
            Optional[WebhookEvent]: The logged event, or None on logging failure
        """
        payload_json = json.dumps(payload.to_dict(), separators=(',', ':'))
        timestamp = payload.timestamp
        signature = self.compute_signature(site.webhook_secret, timestamp, payload_json)

        headers = {
            'Content-Type': 'application/json',
            'X-Aegis-Signature': f"sha256={signature}",
            'X-Aegis-Event': payload.event_type.value,
            'X-Aegis-Timestamp': str(timestamp)
        }

        response_status = None
        response_body = None
        success = False

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
                data=payload_json,
                headers=headers,
                timeout=WEBHOOK_TIMEOUT_SECONDS,
                allow_redirects=False
            )
            response_status = response.status_code
            response_body = response.text[:1000]
            success = 200 <= response.status_code < 300
        except requests.exceptions.RequestException as e:
            response_body = str(e)[:1000]
            logger.warning(f"Webhook delivery failed for site {site.uuid}: {e}")

        # The log row shares the payload's event_id, so a tenant-reported
        # event id is directly greppable in webhook_events. If retries are
        # ever added, rows become per-attempt and event_id moves to its own
        # column so this uuid can stay unique.
        event = WebhookEvent(
            uuid=payload.event_id,
            site_uuid=site.uuid,
            event_type=payload.event_type.value,
            payload=payload_json,
            response_status=response_status,
            response_body=response_body,
            success=success,
            created_at=int(time.time())
        )

        try:
            return db_manager.create_webhook_event(event)
        except Exception as e:
            logger.error(f"Failed to log webhook event for site {site.uuid}: {e}")
            return None


# Global webhook service instance
webhook_service = WebhookService()
