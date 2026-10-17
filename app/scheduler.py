"""In-process reminder scheduler thread.

Runs every REMINDER_TICK_SECONDS and fires due T-10 reminders. On startup it
logs how many PENDING reminders were recovered from SQLite (restart recovery,
spec §8). A reminder older than the grace window is marked MISSED and never
sent; a reminder inside the grace window is sent late rather than dropped.
"""

import logging
import threading
from datetime import timedelta

from app import notifications, templates
from app.config import Config
from app.timeutil import parse_iso, utcnow

log = logging.getLogger("binance-alpha.scheduler")


class ReminderScheduler(threading.Thread):
    def __init__(self, db, stop_event: threading.Event,
                 tick_seconds: int | None = None,
                 grace_seconds: int | None = None):
        super().__init__(name="reminder-scheduler", daemon=True)
        self.db = db
        self.stop = stop_event
        self.tick = tick_seconds or Config.REMINDER_TICK_SECONDS
        self.grace = grace_seconds if grace_seconds is not None else Config.REMINDER_FIRE_GRACE_SECONDS

    def recover(self) -> int:
        n = self.db.count_pending_reminders()
        log.info("startup: %d pending T-10 reminder(s) recovered from SQLite", n)
        return n

    def run(self) -> None:
        self.recover()
        while not self.stop.wait(self.tick):
            try:
                self._check_due()
            except Exception as exc:  # noqa: BLE001
                from app import health

                health.record_error(f"reminder scheduler error: {exc}")
                log.exception("reminder scheduler error")

    def _check_due(self) -> None:
        now = utcnow()
        rows = self.db.pending_reminders_due(now)
        if not rows:
            return
        for row in rows:
            scheduled = parse_iso(row["scheduled_at"])
            if scheduled is None:
                continue
            event = self.db.get_event_by_id(row["event_id"])
            if event is None:
                self.db.mark_reminder(row["rid"], "MISSED")
                continue

            if scheduled < now - timedelta(seconds=self.grace):
                self.db.mark_reminder(row["rid"], "MISSED")
                log.info("reminder %s missed (T-10 already passed %s), not resent",
                         row["rid"], scheduled)
                continue

            title, content = templates.build_t10(event)
            status = notifications.send_notification(
                self.db, event, "START_MINUS_10_MINUTES", title, content
            )
            if status in ("SENT", "DRY_RUN"):
                self.db.mark_reminder(row["rid"], "SENT")
                log.info("reminder %s fired (T-10) event=%s", row["rid"], event.id)
            else:
                self.db.mark_reminder(row["rid"], "FAILED")
                log.error("reminder %s delivery failed: %s", row["rid"], status)
