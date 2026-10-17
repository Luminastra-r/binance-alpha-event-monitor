"""SQLite storage layer.

Schema (see SCHEMA below):
  messages          - every fetched message, deduped by (source, source_message_id)
  alpha_events      - one row per Alpha event, keyed by event_key
  notifications     - outbound notifications; deduped (event_id, notification_type)
                      for DISCOVERED / START_MINUS_10_MINUTES (partial unique index)
  event_reminders   - persisted T-10 reminders (survive restarts)
  event_sources     - per-event official-source provenance (v2)
  meta              - key/value store (checkpoint, health state)
"""

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from app.timeutil import iso_utc, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_message_id TEXT NOT NULL,
    message_url TEXT,
    published_at TEXT,
    raw_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    fingerprint TEXT NOT NULL UNIQUE,
    matched INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_messages_source_msg
    ON messages(source, source_message_id);

CREATE TABLE IF NOT EXISTS alpha_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT NOT NULL UNIQUE,
    project_name TEXT,
    symbol TEXT,
    event_type TEXT,
    start_time TEXT,
    end_time TEXT,
    points_threshold INTEGER,
    points_cost INTEGER,
    airdrop_amount INTEGER,
    claim_rule TEXT,
    source TEXT,
    source_url TEXT,
    first_seen_at TEXT NOT NULL,
    last_updated_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'UPCOMING'
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    notification_type TEXT NOT NULL,
    sent_at TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- dedup: same event + same notification type sent at most once
-- (EVENT_UPDATED is intentionally repeatable: start time may change several times)
CREATE UNIQUE INDEX IF NOT EXISTS ux_notifications_event_type
    ON notifications(event_id, notification_type)
    WHERE notification_type IN ('DISCOVERED', 'START_MINUS_10_MINUTES');

CREATE TABLE IF NOT EXISTS event_reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    reminder_type TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    sent_at TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_reminders_pending
    ON event_reminders(status, scheduled_at);

-- every official source that reported this event, with per-channel first/last
-- seen times (cross-source provenance, spec §3 "保存一个事件对应的全部官方来源")
CREATE TABLE IF NOT EXISTS event_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    source_message_id TEXT,
    message_url TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(event_id, source)
);
CREATE INDEX IF NOT EXISTS ix_event_sources_event ON event_sources(event_id);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


class Database:
    def __init__(self, path: str | os.PathLike):
        self.path = str(path)
        parent = Path(self.path).parent
        parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        conn = self._raw_connect()
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _raw_connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @contextmanager
    def session(self):
        """Fresh connection per session -> safe across poller/scheduler threads."""
        conn = self._raw_connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---------------------------------------------------------------- meta
    def meta_get(self, key: str, default: str | None = None) -> str | None:
        with self.session() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    def meta_set(self, key: str, value: str) -> None:
        with self.session() as conn:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    # ------------------------------------------------------------- messages
    def insert_message_if_new(self, source: str, source_message_id: str,
                              raw_text: str, normalized_text: str,
                              published_at=None, message_url: str = "") -> int | None:
        """Insert a message; returns the new row id, or None if it already exists."""
        import hashlib

        fingerprint = hashlib.sha256(
            f"{source}|{source_message_id}".encode("utf-8")
        ).hexdigest()
        pub = iso_utc(published_at) if published_at else None
        with self.session() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO messages "
                "(source, source_message_id, message_url, published_at, raw_text, "
                " normalized_text, fingerprint) "
                "VALUES (?,?,?,?,?,?,?)",
                (source, source_message_id, message_url, pub, raw_text,
                 normalized_text, fingerprint),
            )
            if cur.rowcount != 1:
                return None
            return cur.lastrowid

    def mark_message_matched(self, message_row_id: int) -> None:
        with self.session() as conn:
            conn.execute("UPDATE messages SET matched=1 WHERE id=?", (message_row_id,))

    def get_message_id(self, source: str, source_message_id: str) -> int | None:
        with self.session() as conn:
            row = conn.execute(
                "SELECT id FROM messages WHERE source=? AND source_message_id=?",
                (source, source_message_id),
            ).fetchone()
            return row["id"] if row else None

    def message_count(self) -> int:
        with self.session() as conn:
            return conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]

    # ---------------------------------------------------------- alpha events
    def get_event_by_key(self, event_key: str):
        with self.session() as conn:
            row = conn.execute(
                "SELECT * FROM alpha_events WHERE event_key=?", (event_key,)
            ).fetchone()
            return row

    def find_unknown_event_on_date(self, date_key: str):
        """Return the most recent alpha:unknown event for a calendar date."""
        with self.session() as conn:
            row = conn.execute(
                "SELECT * FROM alpha_events "
                "WHERE event_key=? OR event_key LIKE ? "
                "ORDER BY id DESC LIMIT 1",
                (f"alpha:unknown:{date_key}", f"alpha:unknown:{date_key}:%"),
            ).fetchone()
            return row

    # ------------------------------------------------- event association (v2)
    def find_events_by_symbol_on_date(self, symbol: str, date_key: str):
        """All events whose key is alpha:{SYM}:{date} (incl. suffixed keys)."""
        sym = symbol.upper()
        like = f"alpha:{sym}:{date_key}%"
        with self.session() as conn:
            rows = conn.execute(
                "SELECT * FROM alpha_events WHERE event_key LIKE ? "
                "ORDER BY id ASC", (like,),
            ).fetchall()
            return [self.event_row_to_model(r) for r in rows]

    def find_unknown_events_on_date(self, date_key: str):
        """All alpha:unknown events on a calendar date (may be several)."""
        with self.session() as conn:
            rows = conn.execute(
                "SELECT * FROM alpha_events WHERE event_key LIKE ? "
                "ORDER BY id ASC", (f"alpha:unknown:{date_key}%",),
            ).fetchall()
            return [self.event_row_to_model(r) for r in rows]

    def find_events_by_start_proximity(self, date_key: str, start_time,
                                       tolerance_seconds: int):
        """Events on a date whose start_time is within tolerance of the given
        start (used to associate symbol-unknown pre-alerts across channels)."""
        from app.timeutil import iso_utc

        with self.session() as conn:
            rows = conn.execute(
                "SELECT * FROM alpha_events "
                "WHERE event_key LIKE ? AND start_time IS NOT NULL ORDER BY id ASC",
                (f"alpha:%:{date_key}%",),
            ).fetchall()
        out = []
        for r in rows:
            ev = self.event_row_to_model(r)
            if ev.start_time is None:
                continue
            if abs((ev.start_time - start_time).total_seconds()) <= tolerance_seconds:
                out.append(ev)
        return out

    # --------------------------------------------------------- event sources
    def upsert_event_source(self, event_id: int, source: str,
                            source_message_id: str = "",
                            message_url: str = "") -> None:
        """Record that `source` reported this event. First report keeps the
        first_seen_at; later reports refresh last_seen_at."""
        from app.timeutil import iso_utc

        now = iso_utc(utcnow())
        with self.session() as conn:
            conn.execute(
                "INSERT INTO event_sources "
                "(event_id, source, source_message_id, message_url, "
                " first_seen_at, last_seen_at) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(event_id, source) DO UPDATE SET "
                " last_seen_at=excluded.last_seen_at, "
                " source_message_id=excluded.source_message_id, "
                " message_url=excluded.message_url",
                (event_id, source, source_message_id, message_url, now, now),
            )

    def get_event_sources(self, event_id: int):
        with self.session() as conn:
            rows = conn.execute(
                "SELECT source, source_message_id, message_url, "
                "       first_seen_at, last_seen_at "
                "FROM event_sources WHERE event_id=? ORDER BY first_seen_at ASC",
                (event_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_event_first_message_text(self, event_id: int) -> str | None:
        """Normalized text of the first official message that reported an event
        (used for content-similarity association of unknown announcements)."""
        with self.session() as conn:
            row = conn.execute(
                "SELECT m.normalized_text FROM messages m "
                "JOIN event_sources es "
                "  ON es.source = m.source "
                " AND es.source_message_id = m.source_message_id "
                "WHERE es.event_id=? ORDER BY es.first_seen_at ASC, m.id ASC LIMIT 1",
                (event_id,),
            ).fetchone()
            return row["normalized_text"] if row else None

    def event_row_to_model(self, row) -> "AlphaEvent":
        from app.models import AlphaEvent
        from app.timeutil import parse_iso

        if row is None:
            return None
        return AlphaEvent(
            id=row["id"],
            event_key=row["event_key"],
            project_name=row["project_name"],
            symbol=row["symbol"],
            event_type=row["event_type"],
            start_time=parse_iso(row["start_time"]),
            end_time=parse_iso(row["end_time"]),
            points_threshold=row["points_threshold"],
            points_cost=row["points_cost"],
            airdrop_amount=row["airdrop_amount"],
            claim_rule=row["claim_rule"],
            source=row["source"],
            source_url=row["source_url"],
            first_seen_at=parse_iso(row["first_seen_at"]),
            last_updated_at=parse_iso(row["last_updated_at"]),
            status=row["status"],
        )

    def create_event(self, event_key: str, fields, source: str, source_url: str):
        """Create an event row from ExtractedFields; returns (row_id, model)."""
        from app.models import AlphaEvent
        from app.timeutil import iso_utc

        now = utcnow()
        with self.session() as conn:
            cur = conn.execute(
                "INSERT INTO alpha_events "
                "(event_key, project_name, symbol, event_type, start_time, end_time, "
                " points_threshold, points_cost, airdrop_amount, claim_rule, source, "
                " source_url, first_seen_at, last_updated_at, status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event_key,
                    fields.project_name,
                    fields.symbol,
                    None,  # event_type set by caller afterwards
                    iso_utc(fields.start_time) if fields.start_time else None,
                    iso_utc(fields.end_time) if fields.end_time else None,
                    fields.points_threshold,
                    fields.points_cost,
                    fields.airdrop_amount,
                    fields.claim_rule,
                    source,
                    source_url,
                    iso_utc(now),
                    iso_utc(now),
                    "UPCOMING",
                ),
            )
            event_id = cur.lastrowid
        return event_id, self.get_event_by_id(event_id)

    def update_event(self, event_id: int, fields, event_type: str) -> None:
        """Apply extracted fields onto an existing event (COALESCE keeps old
        values when the new message does not restate them)."""
        from app.timeutil import iso_utc

        with self.session() as conn:
            conn.execute(
                "UPDATE alpha_events SET "
                " project_name=COALESCE(?, project_name),"
                " symbol=COALESCE(?, symbol),"
                " event_type=?,"  # event_type follows the latest message
                " start_time=COALESCE(?, start_time),"
                " end_time=COALESCE(?, end_time),"
                " points_threshold=COALESCE(?, points_threshold),"
                " points_cost=COALESCE(?, points_cost),"
                " airdrop_amount=COALESCE(?, airdrop_amount),"
                " claim_rule=COALESCE(?, claim_rule),"
                " last_updated_at=?"
                " WHERE id=?",
                (
                    fields.project_name,
                    fields.symbol,
                    event_type,
                    iso_utc(fields.start_time) if fields.start_time else None,
                    iso_utc(fields.end_time) if fields.end_time else None,
                    fields.points_threshold,
                    fields.points_cost,
                    fields.airdrop_amount,
                    fields.claim_rule,
                    iso_utc(utcnow()),
                    event_id,
                ),
            )

    def set_event_type(self, event_id: int, event_type: str) -> None:
        from app.timeutil import iso_utc

        with self.session() as conn:
            conn.execute(
                "UPDATE alpha_events SET event_type=?, last_updated_at=? WHERE id=?",
                (event_type, iso_utc(utcnow()), event_id),
            )

    def update_event_status(self, event_id: int, status: str) -> None:
        with self.session() as conn:
            conn.execute(
                "UPDATE alpha_events SET status=?, last_updated_at=? WHERE id=?",
                (status, iso_utc(utcnow()), event_id),
            )

    def claim_unknown_event(self, event_id: int, event_key: str, fields, event_type: str):
        """Attach symbol/project to an alpha:unknown event and rename its key."""
        from app.timeutil import iso_utc

        with self.session() as conn:
            conn.execute(
                "UPDATE alpha_events SET event_key=?, project_name=?, symbol=?,"
                " event_type=?, start_time=COALESCE(?, start_time),"
                " points_threshold=COALESCE(?, points_threshold),"
                " points_cost=COALESCE(?, points_cost),"
                " airdrop_amount=COALESCE(?, airdrop_amount),"
                " claim_rule=COALESCE(?, claim_rule), last_updated_at=? WHERE id=?",
                (
                    event_key,
                    fields.project_name,
                    fields.symbol,
                    event_type,
                    iso_utc(fields.start_time) if fields.start_time else None,
                    fields.points_threshold,
                    fields.points_cost,
                    fields.airdrop_amount,
                    fields.claim_rule,
                    iso_utc(utcnow()),
                    event_id,
                ),
            )

    def get_event_by_id(self, event_id: int):
        with self.session() as conn:
            row = conn.execute(
                "SELECT * FROM alpha_events WHERE id=?", (event_id,)
            ).fetchone()
            return self.event_row_to_model(row)

    def get_event_by_symbol_date(self, symbol: str, date_key: str):
        with self.session() as conn:
            row = conn.execute(
                "SELECT * FROM alpha_events WHERE event_key=?",
                (f"alpha:{symbol.upper()}:{date_key}",),
            ).fetchone()
            return self.event_row_to_model(row)

    def active_events(self, statuses=("UPCOMING", "LIVE")):
        with self.session() as conn:
            rows = conn.execute(
                "SELECT * FROM alpha_events WHERE status IN (%s)"
                % ",".join("?" * len(statuses)),
                statuses,
            ).fetchall()
            return [self.event_row_to_model(r) for r in rows]

    def all_events(self) -> list:
        """All canonical events, oldest first (verification/debug)."""
        with self.session() as conn:
            rows = conn.execute(
                "SELECT * FROM alpha_events ORDER BY id ASC").fetchall()
            return [self.event_row_to_model(r) for r in rows]

    # ---------------------------------------------------------- notifications
    def notification_exists(self, event_id: int, notification_type: str) -> bool:
        with self.session() as conn:
            row = conn.execute(
                "SELECT 1 FROM notifications WHERE event_id=? AND notification_type=? "
                "LIMIT 1",
                (event_id, notification_type),
            ).fetchone()
            return row is not None

    def insert_notification(self, event_id: int, notification_type: str) -> int | None:
        with self.session() as conn:
            try:
                cur = conn.execute(
                    "INSERT INTO notifications (event_id, notification_type, status) "
                    "VALUES (?,?, 'PENDING')",
                    (event_id, notification_type),
                )
                return cur.lastrowid
            except sqlite3.IntegrityError:
                return None

    def mark_notification(self, notification_id: int, status: str,
                          error_message: str | None = None) -> None:
        with self.session() as conn:
            if status == "SENT" or status == "DRY_RUN":
                conn.execute(
                    "UPDATE notifications SET status=?, sent_at=?, error_message=? "
                    "WHERE id=?",
                    (status, iso_utc(utcnow()), error_message, notification_id),
                )
            else:
                conn.execute(
                    "UPDATE notifications SET status=?, error_message=? WHERE id=?",
                    (status, error_message, notification_id),
                )

    # ------------------------------------------------------------- reminders
    def cancel_pending_reminders(self, event_id: int, reminder_type: str) -> None:
        with self.session() as conn:
            conn.execute(
                "UPDATE event_reminders SET status='CANCELLED' "
                "WHERE event_id=? AND reminder_type=? AND status='PENDING'",
                (event_id, reminder_type),
            )

    def insert_reminder(self, event_id: int, reminder_type: str,
                        scheduled_at) -> int:
        from app.timeutil import iso_utc

        with self.session() as conn:
            cur = conn.execute(
                "INSERT INTO event_reminders (event_id, reminder_type, scheduled_at) "
                "VALUES (?,?,?)",
                (event_id, reminder_type, iso_utc(scheduled_at)),
            )
            return cur.lastrowid

    def pending_reminders_due(self, cutoff):
        from app.timeutil import iso_utc

        with self.session() as conn:
            rows = conn.execute(
                "SELECT r.id AS rid, r.event_id AS event_id, r.scheduled_at, "
                "       e.start_time, e.status AS event_status, "
                "       e.project_name, e.symbol, e.points_threshold, "
                "       e.points_cost, e.airdrop_amount, e.claim_rule "
                "FROM event_reminders r JOIN alpha_events e ON e.id = r.event_id "
                "WHERE r.status='PENDING' AND r.scheduled_at <= ? "
                "ORDER BY r.scheduled_at ASC",
                (iso_utc(cutoff),),
            ).fetchall()
            return rows

    def count_pending_reminders(self) -> int:
        with self.session() as conn:
            return conn.execute(
                "SELECT COUNT(*) c FROM event_reminders WHERE status='PENDING'"
            ).fetchone()["c"]

    def mark_reminder(self, reminder_row_id: int, status: str) -> None:
        with self.session() as conn:
            if status == "SENT":
                conn.execute(
                    "UPDATE event_reminders SET status=?, sent_at=? WHERE id=?",
                    (status, iso_utc(utcnow()), reminder_row_id),
                )
            else:
                conn.execute(
                    "UPDATE event_reminders SET status=? WHERE id=?",
                    (status, reminder_row_id),
                )

    # ------------------------------------------------------------- utilities
    def dump(self) -> dict:
        """Debug dump used by health/report."""
        with self.session() as conn:
            messages = conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
            events = conn.execute("SELECT COUNT(*) c FROM alpha_events").fetchone()["c"]
            notif = conn.execute(
                "SELECT COUNT(*) c FROM notifications").fetchone()["c"]
            reminders = conn.execute(
                "SELECT COUNT(*) c FROM event_reminders").fetchone()["c"]
            sources = conn.execute(
                "SELECT COUNT(*) c FROM event_sources").fetchone()["c"]
            return {
                "messages": messages,
                "alpha_events": events,
                "notifications": notif,
                "event_reminders": reminders,
                "event_sources": sources,
            }
