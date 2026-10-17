"""Notification dispatch with dedup and status persistence.

Rules (spec §9):
  * DISCOVERED and START_MINUS_10_MINUTES are deduped per (event_id, type):
    at most one send each, enforced by a partial unique index + a pre-check.
  * EVENT_UPDATED is intentionally repeatable (a start time can change
    several times) and is NOT covered by the unique index.
  * A failed send is recorded (status=FAILED + error message) and is never
    auto-retried later (retries live inside the provider, max 3 attempts).
"""

import logging

from app import db as db_mod
from app.notifiers.base import get_provider
from app.templates import build_discovered, build_t10, build_updated

log = logging.getLogger("binance-alpha.notifications")

DEDUP_TYPES = {"DISCOVERED", "START_MINUS_10_MINUTES"}


def send_notification(db: "db_mod.Database", event, notification_type: str,
                      title: str, content: str, url: str | None = None) -> str:
    """Send a notification for an event. Returns a status string:
    SENT / DRY_RUN / SKIPPED_DUPLICATE / FAILED."""
    if notification_type in DEDUP_TYPES and db.notification_exists(event.id, notification_type):
        log.info("skip duplicate notification event=%s type=%s",
                 event.id, notification_type)
        return "SKIPPED_DUPLICATE"

    nid = db.insert_notification(event.id, notification_type)
    if nid is None:
        log.info("skip duplicate notification (race) event=%s type=%s",
                 event.id, notification_type)
        return "SKIPPED_DUPLICATE"

    provider = get_provider()
    ok, err = provider.send(title, content, url)

    from app import health

    if ok:
        status = "DRY_RUN" if provider.dry_run else "SENT"
        db.mark_notification(nid, status, None)
        health.record_notification(event.id, notification_type, status)
        log.info("[%s] notification event=%s type=%s", status, event.id, notification_type)
        return status

    db.mark_notification(nid, "FAILED", err)
    health.record_error(f"notification FAILED event={event.id} type={notification_type}: {err}")
    log.error("notification FAILED event=%s type=%s: %s", event.id, notification_type, err)
    return "FAILED"
