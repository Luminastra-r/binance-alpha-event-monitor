"""Health tracking (v2, per-source, interval-aware).

Fixes the v1 contradiction (30-min idle interval but 10-min WARNING): status
is computed per source from:
  * the CURRENT expected poll interval (set by the poller each loop),
  * last planned attempt time,
  * last successful fetch time,
  * consecutive failure count,
  * the source's explicit state (OK / WARNING / ERROR / BLOCKED / UNKNOWN).

Rules (per source):
  * BLOCKED (explicit, e.g. Binance Square) stays BLOCKED — never degrades to
    ERROR, never faked as PASS.
  * consecutive failures >= 3                     -> ERROR
  * consecutive failures >= 1                     -> WARNING
  * otherwise, when the next cycle has not fired
      (now - last_attempt < expected interval)    -> OK
  * otherwise gap = now - last_success:
      gap <= expected + grace                     -> OK
      gap <= 2 * expected                         -> WARNING
      gap >  2 * expected                         -> ERROR
  * no attempt/success recorded yet               -> UNKNOWN

"SOURCE_OK + NO_ALPHA" and "SOURCE_ERROR" remain clearly distinct.
"""

import json
import logging
import os
from datetime import timedelta
from pathlib import Path

from app.config import Config
from app.timeutil import iso_utc, parse_iso, utcnow

log = logging.getLogger("binance-alpha.health")

_PREFIX = "health:"
_SRC_PREFIX = "health:src:"

_db = None


def init(db) -> None:
    global _db
    _db = db


def _set(key: str, value) -> None:
    if _db is None:
        return
    _db.meta_set(_PREFIX + key, str(value))


# ---------------------------------------------------------------- per-source
def _src(key: str) -> str:
    return f"{_SRC_PREFIX}{key}:"


def record_source_attempt(key: str) -> None:
    if _db is None:
        return
    p = _src(key)
    _db.meta_set(p + "last_attempt", iso_utc(utcnow()))


def record_source_ok(key: str, new_messages: int = 0) -> None:
    if _db is None:
        return
    p = _src(key)
    _db.meta_set(p + "last_success", iso_utc(utcnow()))
    _db.meta_set(p + "consecutive_failures", "0")
    _db.meta_set(p + "status", "OK")
    _db.meta_set(p + "last_message_count", str(new_messages))


def record_source_error(key: str, exc: Exception) -> None:
    if _db is None:
        return
    p = _src(key)
    _db.meta_set(p + "last_error", iso_utc(utcnow()))
    _db.meta_set(p + "last_error_message", str(exc)[:500])
    n = int(_db.meta_get(p + "consecutive_failures", "0") or "0") + 1
    _db.meta_set(p + "consecutive_failures", n)
    _db.meta_set(p + "status", "ERROR")


def record_source_blocked(key: str, reason: str) -> None:
    if _db is None:
        return
    p = _src(key)
    _db.meta_set(p + "status", "BLOCKED")
    _db.meta_set(p + "last_error", iso_utc(utcnow()))
    _db.meta_set(p + "last_error_message", str(reason)[:500])


def record_expected_interval(seconds: int) -> None:
    _set("expected_interval", str(int(seconds)))


def record_detected_messages(n: int) -> None:
    if n:
        _set("last_detected_message", iso_utc(utcnow()))


def record_alpha_event() -> None:
    _set("last_alpha_event", iso_utc(utcnow()))


def record_notification(event_id: int, ntype: str, status: str) -> None:
    _set("last_notification", iso_utc(utcnow()))
    _set("last_notification_detail", f"event={event_id} type={ntype} status={status}")


def record_error(msg: str) -> None:
    _set("last_error", iso_utc(utcnow()))
    _set("last_error_message", str(msg)[:500])


# ---------------------------------------------------------------- snapshot
def _source_snapshot(key: str, expected: int) -> dict:
    now = utcnow()
    p = _src(key)
    if _db is None:
        return {"source": key, "status": "UNKNOWN"}

    def g(k, default=None):
        return _db.meta_get(p + k, default)

    status = g("status", "UNKNOWN") or "UNKNOWN"
    last_success_s = g("last_success")
    last_attempt_s = g("last_attempt")
    last_success = parse_iso(last_success_s) if last_success_s else None
    last_attempt = parse_iso(last_attempt_s) if last_attempt_s else None
    failures = int(g("consecutive_failures", "0") or 0)
    grace = max(60.0, expected * 0.25)

    if status == "BLOCKED":
        pass  # stays BLOCKED
    elif last_success is None:
        status = "UNKNOWN" if failures == 0 else "ERROR"
    elif failures >= 3:
        status = "ERROR"
    elif failures >= 1:
        status = "WARNING"
    elif last_attempt is not None and (now - last_attempt).total_seconds() < expected:
        status = "OK"  # next planned cycle has not fired yet
    else:
        gap = (now - last_success).total_seconds()
        if gap <= expected + grace:
            status = "OK"
        elif gap <= 2 * expected:
            status = "WARNING"
        else:
            status = "ERROR"

    return {
        "source": key,
        "status": status,
        "expected_interval": expected,
        "last_attempt": last_attempt_s,
        "last_success": last_success_s,
        "consecutive_failures": failures,
        "last_error": g("last_error"),
        "last_error_message": g("last_error_message"),
        "last_message_count": g("last_message_count"),
    }


def snapshot() -> dict:
    now = utcnow()

    def _get(key, default=None):
        return _db.meta_get(_PREFIX + key, default) if _db else None

    expected = int(_get("expected_interval", "0") or 0) \
        or Config.POLL_INTERVAL_IDLE_SECONDS

    sources = [
        _source_snapshot(key, expected) for key in Config.SOURCES_ALLOWLIST
    ]
    if not sources:
        sources = [_source_snapshot("telegram_wallet", expected)]

    # overall status = worst of the per-source statuses, ignoring BLOCKED
    # (an intentionally blocked channel must not mark a healthy system down);
    # if every source is BLOCKED/UNKNOWN, report that honestly
    _rank = {"ERROR": 5, "WARNING": 4, "UNKNOWN": 2, "OK": 1}
    active = [s for s in sources if s["status"] != "BLOCKED"]
    if active:
        overall = max((s["status"] for s in active), key=lambda s: _rank.get(s, 0))
    elif sources:
        overall = "BLOCKED"
    else:
        overall = "UNKNOWN"
    last_success_s = max(
        (s.get("last_success") for s in sources if s.get("last_success")), default=None)

    database_status = "OK"
    try:
        with _db.session() as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception as exc:  # noqa: BLE001
        database_status = f"ERROR: {exc}"

    return {
        "ts": iso_utc(now),
        "expected_interval": expected,
        "source_status": overall,
        "last_successful_fetch": last_success_s,
        "last_detected_message": _get("last_detected_message"),
        "last_alpha_event": _get("last_alpha_event"),
        "last_notification": _get("last_notification"),
        "last_notification_detail": _get("last_notification_detail"),
        "last_error": _get("last_error"),
        "last_error_message": _get("last_error_message"),
        "database_status": database_status,
        "checkpoint": _db.meta_get("checkpoint_last_message_id") if _db else None,
        "counts": _db.dump() if _db else {},
        "sources": sources,
        "status_rule": (
            "per source: failures>=3 ERROR, >=1 WARNING; else gap <= "
            "expected+grace OK, <=2*expected WARNING, >2*expected ERROR; "
            "BLOCKED stays BLOCKED"
        ),
    }


def write_json() -> str:
    snap = snapshot()
    path = Config.HEALTH_PATH
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(snap, fh, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        log.warning("health json write failed: %s", exc)
    return path
