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
import threading
import time
from typing import Optional

import requests

from database import db_manager
from models.site import Site
from models.webhook_event import WebhookEvent
from models.webhook_payload import WebhookPayload

logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT_SECONDS = 5


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

        thread = threading.Thread(
            target=self._deliver_webhook,
            args=(site, payload),
            daemon=True
        )
        thread.start()

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
            'X-Aegis-Event': payload.event_type,
            'X-Aegis-Timestamp': str(timestamp)
        }

        response_status = None
        response_body = None
        success = False

        try:
            response = requests.post(
                site.webhook_url,
                data=payload_json,
                headers=headers,
                timeout=WEBHOOK_TIMEOUT_SECONDS
            )
            response_status = response.status_code
            response_body = response.text[:1000]
            success = 200 <= response.status_code < 300
        except requests.exceptions.RequestException as e:
            response_body = str(e)[:1000]
            logger.warning(f"Webhook delivery failed for site {site.id}: {e}")

        event = WebhookEvent(
            id=0,
            site_id=site.id,
            event_type=payload.event_type,
            payload=payload_json,
            response_status=response_status,
            response_body=response_body,
            success=success,
            created_at=int(time.time())
        )

        try:
            return db_manager.create_webhook_event(event)
        except Exception as e:
            logger.error(f"Failed to log webhook event for site {site.id}: {e}")
            return None


# Global webhook service instance
webhook_service = WebhookService()
