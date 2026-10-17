"""Dynamic polling loop (v2, multi-source).

Per-source behaviour:
  * each official source keeps its own first-start flag and its own health
    state (last attempt / last success / consecutive failures);
  * one source failing (or being BLOCKED) never blocks the other sources;
  * poll failures never reset existing events, notifications or reminders.

Global interval (unchanged, spec §5):
  no tracked event          -> POLL_INTERVAL_IDLE_SECONDS (30 min)
  upcoming, start unknown   -> POLL_INTERVAL_UPCOMING_SECONDS (15 min)
  start known, <= 2h away   -> POLL_INTERVAL_NEAR_EVENT_SECONDS (5 min)
  start known, <= 15min     -> POLL_INTERVAL_COUNTDOWN_SECONDS (1 min)
  event ended               -> back to idle

First-start behaviour (spec §28): each source's first fetch stores history and
sends NO notifications; a small random stagger between sources avoids hitting
them all at the same instant.
"""

import logging
import random
import threading

from app import event_engine
from app.config import Config
from app.timeutil import utcnow

log = logging.getLogger("binance-alpha.poller")


class Poller:
    def __init__(self, db, sources: list, stop_event: threading.Event):
        self.db = db
        self.sources = sources
        self.stop = stop_event

    # ------------------------------------------------------------- lifecycle
    def update_lifecycle(self) -> None:
        """UPCOMING -> LIVE when now >= start_time; -> ENDED after the
        airdrop window (start + AIRDROP_WINDOW_HOURS)."""
        from datetime import timedelta

        from app.timeutil import utcnow

        now = utcnow()
        window = timedelta(hours=Config.AIRDROP_WINDOW_HOURS)
        for e in self.db.active_events():
            if not e.start_time:
                continue
            if now >= e.start_time + window and e.status != "ENDED":
                self.db.update_event_status(e.id, "ENDED")
                log.info("event=%s (%s) marked ENDED", e.id, e.event_key)
            elif now >= e.start_time and e.status == "UPCOMING":
                self.db.update_event_status(e.id, "LIVE")
                log.info("event=%s (%s) marked LIVE", e.id, e.event_key)

    # ------------------------------------------------------------- per source
    def _is_first_fetch(self, source_key: str) -> bool:
        return self.db.meta_get(f"source_first_done:{source_key}") is None

    def _run_source(self, source) -> dict:
        from app import health

        from app.sources.base import SourceBlockedError

        key = source.key
        first = self._is_first_fetch(key)
        health.record_source_attempt(key)
        try:
            fetched = source.fetch_page()
        except SourceBlockedError as exc:
            health.record_source_blocked(key, str(exc))
            log.error("source %s BLOCKED: %s", key, exc)
            return {"ok": False, "blocked": True, "error": str(exc),
                    "first": first, "new": 0, "alpha": 0}
        except Exception as exc:  # noqa: BLE001
            health.record_source_error(key, exc)
            log.error("source %s fetch failed: %s", key, exc)
            return {"ok": False, "blocked": False, "error": str(exc),
                    "first": first, "new": 0, "alpha": 0}

        # valid empty page (channel with no text posts) -> still SOURCE_OK
        if not fetched:
            health.record_source_ok(key, 0)
            log.info("source %s fetch OK, no messages on page", key)
            return {"ok": True, "blocked": False, "first": first,
                    "new": 0, "alpha": 0}

        # oldest -> newest so event association is chronological
        fetched.sort(key=lambda m: (m.published_at or utcnow(), m.source_message_id))

        new_count = 0
        alpha_count = 0
        for m in fetched:
            row_id = self.db.insert_message_if_new(
                source=m.source,
                source_message_id=m.source_message_id,
                raw_text=m.raw_text,
                normalized_text=m.normalized_text,
                published_at=m.published_at,
                message_url=m.message_url,
            )
            if row_id is None:
                continue
            new_count += 1
            if first:
                continue  # history: store, checkpoint, do not notify

            result = event_engine.process_message(self.db, m, notify=True)
            if result.matched:
                alpha_count += 1
                self.db.mark_message_matched(row_id)
                health.record_alpha_event()
                log.info(
                    "ALPHA match source=%s msg=%s key=%s created=%s type=%s "
                    "actions=%s",
                    m.source, m.source_message_id,
                    result.event.event_key if result.event else "?",
                    result.created,
                    result.event.event_type if result.event else "?",
                    result.actions,
                )

        if first:
            self.db.meta_set(f"source_first_done:{key}", "1")
            log.info(
                "FIRST START (source=%s): stored %d historical message(s), "
                "checkpoint established, NO notifications sent (history suppressed)",
                key, new_count,
            )

        health.record_source_ok(key, new_count)
        health.record_detected_messages(new_count)
        log.info("source %s poll done: %d new message(s), %d alpha match(es)",
                 key, new_count, alpha_count)
        return {"ok": True, "blocked": False, "first": first,
                "new": new_count, "alpha": alpha_count}

    def run_once(self) -> dict:
        results = {}
        ok_any = False
        stagger = random.uniform(0, Config.SOURCE_STAGGER_MAX_SECONDS)
        for src in self.sources:
            if self.stop.is_set():
                break
            results[src.key] = self._run_source(src)
            ok_any = ok_any or results[src.key].get("ok")
            if stagger and src is not self.sources[-1]:
                self.stop.wait(stagger)
                stagger = random.uniform(0, Config.SOURCE_STAGGER_MAX_SECONDS)
        return {
            "ok": ok_any or not results,  # at least one source alive
            "sources": results,
            "first": any(r.get("first") for r in results.values()),
        }

    # ----------------------------------------------------------- interval
    def compute_interval(self) -> int:
        from datetime import timedelta

        now = utcnow()
        events = self.db.active_events()
        if not events:
            return Config.POLL_INTERVAL_IDLE_SECONDS

        near = countdown = upcoming_unknown = False
        for e in events:
            if e.start_time:
                delta = (e.start_time - now).total_seconds()
                if 0 < delta <= 15 * 60:
                    countdown = True
                elif 0 < delta <= 2 * 3600:
                    near = True
                elif -Config.AIRDROP_WINDOW_HOURS * 3600 < delta <= 0:
                    near = True
            else:
                upcoming_unknown = True

        if countdown:
            return Config.POLL_INTERVAL_COUNTDOWN_SECONDS
        if near:
            return Config.POLL_INTERVAL_NEAR_EVENT_SECONDS
        if upcoming_unknown:
            return Config.POLL_INTERVAL_UPCOMING_SECONDS
        return Config.POLL_INTERVAL_IDLE_SECONDS

    # ------------------------------------------------------------------ loop
    def loop(self) -> None:
        log.info("poller started (sources: %s)",
                 ", ".join(s.key for s in self.sources))
        while not self.stop.is_set():
            result = self.run_once()
            self.update_lifecycle()
            interval = self.compute_interval()
            from app import health

            health.record_expected_interval(interval)
            health.write_json()
            ok_flags = {k: v.get("ok") for k, v in result["sources"].items()}
            log.info("next poll in %ds (sources ok=%s)", interval, ok_flags)
            wait = interval * (1 + random.uniform(-Config.POLL_JITTER_FRACTION,
                                                  Config.POLL_JITTER_FRACTION))
            if self.stop.wait(wait):
                break
        log.info("poller stopped")
