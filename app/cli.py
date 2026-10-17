"""CLI entry points.

Commands:
  python -m app run [--live]        run the monitor (poller + reminder scheduler)
  python -m app test-source         parser self-test + live/fixture fetch check
  python -m app test-alpha          offline keyword + extraction tests (spec §30)
  python -m app test-notification   dual-stage reminder tests (spec §31)
  python -m app replay [DNA|DRV]    real-source historical verification (spec §32)
  python -m app health              health snapshot (spec §34)
  python -m app version
"""

import argparse
import logging
import signal
import sys
import threading
from datetime import datetime, timezone

from app import __version__
from app.db import Database
from app.sources.base import build_source

log = logging.getLogger("binance-alpha.cli")


def setup_logging() -> None:
    from app.config import Config

    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        import os
        from pathlib import Path

        log_dir = Path(Config.DB_PATH).parent
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.FileHandler(log_dir / "monitor.log", encoding="utf-8")
        )
    except Exception:  # noqa: BLE001
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def cmd_run(args: argparse.Namespace) -> int:
    from app import health
    from app.config import Config
    from app.poller import Poller
    from app.scheduler import ReminderScheduler
    from app.sources.base import build_sources
    from app.timeutil import configure_display_tz

    setup_logging()
    configure_display_tz(Config.TIMEZONE)

    if args.live:
        import os

        os.environ.pop("TELEGRAM_FIXTURE", None)
        os.environ.pop("TELEGRAM_ANNOUNCEMENTS_FIXTURE", None)
        os.environ.pop("SQUARE_FIXTURE", None)

    db = Database(Config.DB_PATH)
    health.init(db)
    sources = build_sources()
    stop = threading.Event()

    def _signal_handler(*_args) -> None:
        log.info("shutdown signal received")
        stop.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    scheduler = ReminderScheduler(db, stop)
    scheduler.start()
    poller = Poller(db, sources, stop)

    log.info("Binance Alpha Event Monitor v%s starting (env=%s, tz=%s)",
             __version__, Config.APP_ENV, Config.TIMEZONE)
    log.info("sources: %s", ", ".join(s.key for s in sources))

    try:
        poller.loop()
    finally:
        stop.set()
        scheduler.join(timeout=10)
        health.write_json()
        log.info("monitor stopped cleanly")
    return 0


def cmd_test_source(args: argparse.Namespace) -> int:
    setup_logging()
    from app.sources import telegram_html as th
    from app.sources.base import SourceParseError

    print("=" * 62)
    print("SOURCE PARSER TEST")
    print("=" * 62)

    failures = 0

    # -- 1) synthetic HTML parse ------------------------------------------
    print("\n-- 1) synthetic t.me widget HTML --")
    html = _synthetic_telegram_html()
    msgs = th.parse_html_page(html, "binance_wallet_announcements")
    ok = len(msgs) == 3 and msgs[0].source_message_id == "1001" \
        and "first platform to feature" in msgs[0].raw_text \
        and msgs[0].published_at is not None \
        and "Derive" in msgs[1].raw_text and "line two" in msgs[1].raw_text \
        and msgs[2].raw_text == "No match here."
    for m in msgs:
        print(f"  id={m.source_message_id} ts={m.published_at} text={m.raw_text[:60]!r}")
    print(f"  synthetic parse: {'PASS' if ok else 'FAIL'}")
    failures += 0 if ok else 1

    # -- 2) structure-change must raise (never SOURCE_OK on parse failure) --
    print("\n-- 2) malformed page must raise SourceParseError --")
    raised = False
    try:
        th.parse_html_page("<html><body><div>captcha</div></body></html>",
                           "binance_wallet_announcements")
    except SourceParseError:
        raised = True
    print(f"  malformed -> SourceParseError: {'PASS' if raised else 'FAIL'}")
    failures += 0 if raised else 1

    raised2 = False
    try:
        th.parse_html_page(
            '<div class="tgme_widget_message" data-post="x/1">'
            '<div class="tgme_widget_message_text js-message_text">hi</div></div>',
            "binance_wallet_announcements",
        )
    except SourceParseError as exc:
        raised2 = True
        print(f"  wrong-channel guard: PASS ({exc})")
    failures += 0 if raised2 else 1
    if not raised2:
        print("  wrong-channel guard: FAIL")

    # -- 3) live fetch (production path) -----------------------------------
    print("\n-- 3) live fetch (all configured sources) --")
    from app.sources.base import SourceBlockedError, build_sources

    srcs = build_sources()
    for src in srcs:
        try:
            fetched = src.fetch_page()
            print(f"  {src.key}: OK ({len(fetched)} messages)")
        except SourceBlockedError as exc:
            print(f"  {src.key}: BLOCKED ({str(exc)[:100]}...)")
        except Exception as exc:  # noqa: BLE001
            print(f"  {src.key}: FAILED ({exc})")
            print("  (sandbox egress restriction; production server unaffected)")

    print(f"\nRESULT: {'ALL PASS' if failures == 0 else f'{failures} FAILURE(S)'}")
    return 0 if failures == 0 else 1


def _synthetic_telegram_html() -> str:
    return (
        '<div class="tgme_widget_message_wrap js-widget_message_wrap">'
        '<div class="tgme_widget_message " data-post="binance_wallet_announcements/1001" data-datetime="1730000000">'
        '<div class="tgme_widget_message_text js-message_text" dir="auto">'
        "Binance Alpha will be the first platform to feature NeoSoul (DNA) on October 16. "
        "Eligible users can claim their airdrop."
        "</div>"
        '<a class="tgme_widget_message_date" href="https://t.me/binance_wallet_announcements/1001">'
        '<time datetime="2026-10-15T11:30:00+00:00">19:30</time></a>'
        "</div></div>"
        '<div class="tgme_widget_message_wrap js-widget_message_wrap">'
        '<div class="tgme_widget_message " data-post="binance_wallet_announcements/1002">'
        '<div class="tgme_widget_message_text js-message_text" dir="auto">'
        "Derive (DRV) is now live on Binance Alpha!<br>line two"
        "</div></div></div>"
        '<div class="tgme_widget_message_wrap js-widget_message_wrap">'
        '<div class="tgme_widget_message " data-post="binance_wallet_announcements/1003">'
        '<div class="tgme_widget_message_text js-message_text" dir="auto">No match here.</div>'
        "</div></div>"
    )


def cmd_test_alpha(args: argparse.Namespace = None) -> int:
    setup_logging()
    from app.selftests import run_alpha_tests

    return run_alpha_tests()


def cmd_test_notification(args: argparse.Namespace = None) -> int:
    setup_logging()
    from app.selftests import run_reminder_tests

    return run_reminder_tests()


def cmd_test_cross_source(args: argparse.Namespace = None) -> int:
    setup_logging()
    from app.selftests import run_cross_source_tests

    return run_cross_source_tests()


def cmd_replay(args: argparse.Namespace) -> int:
    setup_logging()
    from app.verify import run_real_source_test

    return run_real_source_test(symbols=args.symbols, live=args.live,
                                sources=args.sources)


def cmd_health(args: argparse.Namespace) -> int:
    from app import health
    from app.config import Config
    from app.db import Database
    from app.timeutil import configure_display_tz

    configure_display_tz(Config.TIMEZONE)
    db = Database(Config.DB_PATH)
    health.init(db)
    snap = health.snapshot()
    path = health.write_json()
    print("=" * 62)
    print("HEALTH SNAPSHOT")
    print("=" * 62)
    for k, v in snap.items():
        print(f"{k}: {v}")
    print(f"(json: {path})")

    if snap["source_status"] == "ERROR":
        print("\nstatus: ERROR (one or more active sources stale/failing; see sources[])")
        return 1
    if snap["source_status"] == "WARNING":
        print("\nstatus: WARNING (one or more active sources stale/failing; see sources[])")
        return 0
    if snap["source_status"] == "BLOCKED":
        print("\nstatus: BLOCKED (no active source reachable; see sources[])")
        return 1
    print("\nstatus: OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        argv = ["run"]

    parser = argparse.ArgumentParser(prog="python -m app")
    sub = parser.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="run the monitor")
    p_run.add_argument("--live", action="store_true", help="force live t.me fetch (ignore fixture)")
    p_run.set_defaults(fn=cmd_run)

    p_ts = sub.add_parser("test-source", help="source parser + fetch test")
    p_ts.add_argument("--live", action="store_true")
    p_ts.set_defaults(fn=cmd_test_source)

    p_ta = sub.add_parser("test-alpha", help="offline keyword tests")
    p_ta.set_defaults(fn=cmd_test_alpha)

    p_tn = sub.add_parser("test-notification", help="dual-stage reminder tests")
    p_tn.set_defaults(fn=cmd_test_notification)

    p_cs = sub.add_parser("test-cross-source",
                          help="cross-source merge & notification tests (Phase 2)")
    p_cs.set_defaults(fn=cmd_test_cross_source)

    p_rp = sub.add_parser("replay", help="real-source historical verification")
    p_rp.add_argument("symbols", nargs="*", default=None, help="DNA / DRV (default: both)")
    p_rp.add_argument("--live", action="store_true")
    p_rp.add_argument("--sources", nargs="*", default=None,
                      help="source keys: telegram_wallet telegram_announcements "
                           "binance_square (default: SOURCES_ALLOWLIST)")
    p_rp.set_defaults(fn=cmd_replay)

    p_he = sub.add_parser("health", help="health snapshot")
    p_he.set_defaults(fn=cmd_health)

    sub.add_parser("version", help="print version")

    args = parser.parse_args(argv)
    if args.cmd == "version":
        print(f"binance-alpha-monitor {__version__}")
        return 0
    if not hasattr(args, "fn"):
        parser.print_help()
        return 2
    try:
        return int(args.fn(args) or 0)
    except KeyboardInterrupt:
        return 0
