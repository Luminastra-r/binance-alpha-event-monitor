"""Historical real-source verification (spec §14-§15, §32, Phase 2 multi-source).

  python -m app replay DNA
  python -m app replay DRV
  python -m app replay                    # both
  python -m app replay DNA --sources telegram_wallet,telegram_announcements
  python -m app replay --live             # live fetch instead of fixtures

Pipeline: fetch each requested official source (live on a production server;
sandbox uses saved official-page fixtures) -> run every message through the
real matcher/extractor/event engine -> grade the parsed canonical event against
independently-known facts for DNA / DRV.

Provenance is declared per source (never mixed):
  official_fixture  : saved from the official channel page (verbatim capture)
  live_fetch        : fetched right now from the official channel
  synthetic         : hand-made sample (parser/pipeline tests only, never PASS)
  blocked           : official source reachable only via a method outside v1
                      scope (e.g. Binance Square) -> reported as BLOCKED, NO.

The expected facts are used ONLY to grade the report — they are never injected
into the detection pipeline (no hardcoding to force PASS).
"""

import logging
import os
import re
import tempfile
from datetime import timedelta, timezone
from pathlib import Path

from app.db import Database
from app.event_engine import process_message
from app.sources.base import SourceBlockedError
from app.timeutil import parse_iso, utcnow

log = logging.getLogger("binance-alpha.verify")

# replay must never send real WeChat messages
from app.notifiers import base as _notifier_base  # noqa: E402

_notifier_base.force_dry_run = True
_notifier_base.reset_provider()

# Independently-known facts for grading (spec §14-§15).
EXPECTED = {
    "DNA": {
        "project": "NeoSoul",
        "symbol": "DNA",
        "start_utc": "2026-10-16T08:00:00Z",      # = 2026-10-16 16:00 +08:00
        "points": 245,
        "amount": 400,
        "cost": 15,
    },
    "DRV": {
        "project": "Derive",
        "symbol": "DRV",
        "start_utc": "2026-10-14T10:00:00Z",      # = 2026-10-14 18:00 +08:00
        "points": 245,
        "amount": 70,
        "cost": 15,
    },
}

_LINE = "=" * 62

# key -> (builder, provenance)
SOURCE_BUILDERS = {
    "telegram_wallet": ("app.sources.base.build_wallet_telegram_source",
                        "official_fixture"),
    "telegram_announcements": ("app.sources.base.build_announcements_telegram_source",
                               "official_fixture"),
    "binance_square": ("app.sources.base.build_square_source", "blocked"),
}


def _build(key: str, live: bool):
    from importlib import import_module

    mod, fn = SOURCE_BUILDERS[key][0].rsplit(".", 1)
    builder = getattr(import_module(mod), fn)
    if live:
        os.environ.pop("TELEGRAM_FIXTURE", None)
        os.environ.pop("TELEGRAM_ANNOUNCEMENTS_FIXTURE", None)
        os.environ.pop("SQUARE_FIXTURE", None)
    return builder()


def _fmt_cn(dt):
    """2026-10-16 16:00 +08:00"""
    d = dt.astimezone(timezone(timedelta(hours=8)))
    return d.strftime("%Y-%m-%d %H:%M +08:00")


def _provenance_of(source) -> str:
    from app.sources.square import BinanceSquareSource

    if isinstance(source, BinanceSquareSource):
        if source.mode == "fixture":
            return "official_fixture" if "fixture" in (source.fixture_path or "") else "synthetic"
        return "blocked"
    name = type(source).__name__
    if name == "TelegramTextPageSource":
        return "official_fixture"
    return "live_fetch"


def run_symbol(symbol: str, db, sources) -> dict:
    """Run the full pipeline over all sources into ONE canonical-event DB."""
    out = {
        "symbol": symbol,
        "found": False,
        "official": True,
        "preview": False,
        "detail": False,
        "live": False,
        "event": None,
        "sources": [],
        "per_source": [],
    }
    for src in sources:
        prov = _provenance_of(src)
        entry = {"key": src.key, "provenance": prov, "ok": True, "messages": 0,
                 "matched": 0, "blocked": False}
        try:
            fetched = src.fetch_page()
        except SourceBlockedError as exc:
            entry.update({"ok": False, "blocked": True,
                          "error": str(exc)[:160]})
            out["per_source"].append(entry)
            continue
        except Exception as exc:  # noqa: BLE001
            entry.update({"ok": False, "error": str(exc)[:160]})
            out["per_source"].append(entry)
            continue
        fetched.sort(key=lambda m: (m.published_at or utcnow(), m.source_message_id))
        entry["messages"] = len(fetched)
        for m in fetched:
            if re.search(rf"\b{re.escape(symbol)}\b", m.raw_text, re.I):
                out["found"] = True
                entry.setdefault("has_symbol", True)
            res = process_message(db, m, notify=False)
            if not res.matched:
                continue
            entry["matched"] += 1
            norm = m.normalized_text
            if "first platform to feature" in norm or "get ready to claim" in norm:
                out["preview"] = True
            if "airdrop of" in norm or "空投" in norm:
                out["detail"] = True
            if "now live on binance alpha" in norm:
                out["live"] = True
        out["per_source"].append(entry)

    # canonical event: any event row whose symbol matches (post-merge state)
    for ev in db.all_events():
        if ev.symbol == symbol:
            out["event"] = ev
            break
    if out["event"] is not None:
        out["sources"] = [s["source"] for s in db.get_event_sources(out["event"].id)]
    return out


def print_report(symbol: str, ev: dict, exp: dict) -> bool:
    event = ev["event"]
    print(_LINE)
    print("REAL SOURCE TEST")
    print(_LINE)
    print(f"{symbol} Found: {'YES' if ev['found'] else 'NO'}")
    print(f"Official Source: {'YES' if ev['official'] else 'NO'}")
    print(f"Preview: {'YES' if ev['preview'] else 'NO'}")
    print(f"Detail: {'YES' if ev['detail'] else 'NO'}")
    print("Sources evaluated:")
    for s in ev["per_source"]:
        state = "OK" if s["ok"] else ("BLOCKED" if s["blocked"] else "FAILED")
        extra = f" ({s.get('error')})" if not s["ok"] else \
            f" {s['messages']} msgs, {s['matched']} alpha matches"
        print(f"  - {s['key']:<24} {s['provenance']:<18} {state}{extra}")
    if event is None:
        print(f"Project: - (not parsed)")
        print(f"Symbol: -")
        print(f"Start: -")
        print(f"Points: -")
        print(f"Amount: -")
        print(f"Cost: -")
        print("Result: FAIL")
        return False

    exp_start = parse_iso(exp["start_utc"])
    checks = {
        "Project": (event.project_name == exp["project"], event.project_name, exp["project"]),
        "Symbol": (event.symbol == exp["symbol"], event.symbol, exp["symbol"]),
        "Start": (event.start_time is not None and event.start_time == exp_start,
                  _fmt_cn(event.start_time) if event.start_time else None, _fmt_cn(exp_start)),
        "Points": (event.points_threshold == exp["points"], event.points_threshold, exp["points"]),
        "Amount": (event.airdrop_amount == exp["amount"], event.airdrop_amount, exp["amount"]),
        "Cost": (event.points_cost == exp["cost"], event.points_cost, exp["cost"]),
    }
    ok = ev["found"] and ev["official"] and all(c[0] for c in checks.values())
    for label, (passed, got, want) in checks.items():
        mark = "PASS" if passed else "FAIL"
        print(f"{label}: {got}   [{mark}] (expected {want})")
    print(f"Event sources recorded: {ev['sources']}")
    print(f"Result: {'PASS' if ok else 'FAIL'}")
    return ok


def run_real_source_test(symbols: list[str] | None = None, live: bool = False,
                         sources: list[str] | None = None) -> int:
    from app.config import Config

    symbols = symbols or ["DNA", "DRV"]
    sources = sources or list(Config.SOURCES_ALLOWLIST)

    tmp = tempfile.mkdtemp(prefix="ba_verify_")
    db = Database(str(Path(tmp) / "verify.db"))

    built = {}
    for key in sources:
        try:
            built[key] = _build(key, live)
        except Exception as exc:  # noqa: BLE001
            print(f"  source {key}: BUILD FAILED ({exc})")

    all_ok = True
    for sym in symbols:
        exp = EXPECTED.get(sym)
        if exp is None:
            print(f"unknown symbol {sym}; expected one of {list(EXPECTED)}")
            continue
        ev = run_symbol(sym, db, list(built.values()))
        ok = print_report(sym, ev, exp)
        all_ok = all_ok and ok
        print()
    print(_LINE)
    print(f"OVERALL: {'ALL PASS' if all_ok else 'SOME FAIL'}")
    print("Note: Binance Square is SOURCE_BLOCKED (no public API, no browser")
    print("automation in v1); its parser is tested offline (test-source) with")
    print("synthetic envelopes — never presented as an official PASS.")
    print(_LINE)
    return 0 if all_ok else 1
