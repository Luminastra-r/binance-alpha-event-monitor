"""Persisted T-10 reminders (spec §7-8, §10, §25).

Reminders live in SQLite (event_reminders), never only in memory. On startup
the scheduler re-registers every PENDING reminder. A reminder whose scheduled
time has passed is marked MISSED and is NOT backfilled ("错过不补发").
"""

import logging
from datetime import timedelta

from app.config import Config
from app.timeutil import utcnow

log = logging.getLogger("binance-alpha.reminders")

REMINDER_TYPE_T10 = "START_MINUS_10_MINUTES"


def scheduled_reminder_time(event):
    """T-10 instant = start_time - REMINDER_MINUTES_BEFORE_START (UTC)."""
    if not event.start_time:
        return None
    return event.start_time - timedelta(minutes=Config.REMINDER_MINUTES_BEFORE_START)


def upsert_start_reminder(db, event) -> str:
    """Create (or recreate after a start-time change) the T-10 reminder.

    Returns one of: CREATED / PAST_WINDOW / NO_START.
    A reminder is never created when its scheduled instant is already in the
    past — that would fire an already-invalid "10 minutes to start" notice.
    """
    if not event.start_time:
        return "NO_START"

    scheduled = scheduled_reminder_time(event)
    now = utcnow()
    if scheduled <= now:
        # T-10 window already passed (e.g. discovered at 15:57 for 16:00) —
        # cancel any stale pending reminder and do not create a new one.
        db.cancel_pending_reminders(event.id, REMINDER_TYPE_T10)
        log.info("event=%s start=%s: T-10 window already passed, no reminder",
                 event.id, event.start_time)
        return "PAST_WINDOW"

    db.cancel_pending_reminders(event.id, REMINDER_TYPE_T10)
    db.insert_reminder(event.id, REMINDER_TYPE_T10, scheduled)
    log.info("event=%s T-10 reminder created at %s", event.id, scheduled)
    return "CREATED"
