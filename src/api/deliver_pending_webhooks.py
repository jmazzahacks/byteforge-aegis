"""
Webhook retry sweep endpoint.

Driven by cron rather than an in-process scheduler. The app runs several
gunicorn workers against a deliberately tight DB pool (see the sizing note
in webhook_service), so a poller thread in each worker would add a
connection per worker and contend for the same pool it is trying to
protect. A single external caller keeps the concurrency accounted for, and
matches how expired-token cleanup is already driven.
"""
import logging

from flask import Blueprint, jsonify

from services.webhook_service import webhook_service
from utils.api_key_middleware import require_master_api_key

logger = logging.getLogger(__name__)

deliver_pending_webhooks_bp = Blueprint('deliver_pending_webhooks', __name__)


@deliver_pending_webhooks_bp.route(
    '/api/admin/deliver-pending-webhooks', methods=['POST']
)
@require_master_api_key
def deliver_pending_webhooks():
    """
    Attempt every webhook delivery that is currently due.

    Requires master API key (X-API-Key header). Intended to be called on a
    schedule of roughly a minute; safe to call by hand, and safe to run
    concurrently with itself — claims use SKIP LOCKED, so two overlapping
    sweeps take disjoint rows rather than double-delivering.

    A sweep is bounded, so a large backlog drains over several runs instead
    of one run holding connections for minutes. `still_due` in the response
    is what to watch: persistently non-zero means the backlog is growing
    faster than the schedule drains it.

    Returns:
        200: Counts of attempted, delivered, retrying, exhausted, still_due
        401: Missing or invalid API key
    """
    result = webhook_service.deliver_pending()

    if result.exhausted:
        logger.error(
            'Webhook sweep gave up on %s delivery(s) — those events are lost '
            'to their tenants', result.exhausted, extra=result.to_dict()
        )

    return jsonify(result.to_dict()), 200
