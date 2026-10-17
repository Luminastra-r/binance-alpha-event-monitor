"""Offline self-tests (spec §30-§31).

  python -m app test-alpha          -> keyword matching table (DNA/DRV + negatives)
  python -m app test-notification   -> dual-stage reminder scenarios + restart recovery
  python -m app test-feishu         -> Feishu webhook channel tests (Phase 3)

All exit non-zero if any assertion fails.
"""

import logging
import tempfile
from datetime import timedelta
from pathlib import Path

from app.db import Database
from app.event_engine import process_message
from app.models import SourceMessage
from app.timeutil import UTC, iso_utc, utcnow

log = logging.getLogger("binance-alpha.selftests")

# self-tests must never send real WeChat messages
from app.notifiers import base as _notifier_base  # noqa: E402

_notifier_base.force_dry_run = True
_notifier_base.reset_provider()

# --------------------------------------------------------------------------
# spec §30 canonical cases
# --------------------------------------------------------------------------

_POSITIVE_CASES = [
    (
        "DNA",
        "Binance Alpha will be the first platform to feature NeoSoul (DNA) on October 16. "
        "Eligible users can claim their airdrop using Binance Alpha Points on the Alpha Events page "
        "once trading opens.",
    ),
    (
        "DRV",
        "Please get ready to claim the Binance Alpha airdrop and trade today at 10:00 (UTC). "
        "Users with at least 245 Binance Alpha Points can claim the token on a first-come, "
        "first-served basis.",
    ),
    (
        "DRV LIVE",
        "Derive (DRV) is now live on Binance Alpha! Users with at least 245 Binance Alpha Points "
        "can claim an airdrop of 70 DRV tokens on a first-come, first-served basis. Claiming the "
        "airdrop will consume 15 Binance Alpha Points.",
    ),
]

_NEGATIVE_CASES = [
    ("Trading Competition", "Binance Alpha Trading Competition"),
    ("Prediction Markets", "Binance Wallet Prediction Markets"),
    ("DeFi", "Binance Wallet DeFi"),
    ("Referral", "Binance Wallet Referral Programme"),
]

# Real channel negatives (must NOT match) — verbatim from the official channel
_REAL_NEGATIVES = [
    ("DGAI Competition",
     "Join the #Binance Alpha DGAI Trading Competition now! Trade DGAI with $200K worth of rewards "
     "up for grabs. Click [Join] on the Binance App event page to start tracking trade volume. "
     "New: Rising Trader Boost — Qualified Alpha competition traders can unlock a 1.2x trading "
     "volume boost. Only buy volume counts (selling is excluded)."),
    ("ACU Competition",
     "Join the #Binance Alpha ACU Trading Competition now! Trade ACU with $100K worth of rewards "
     "up for grabs. Early Bird Multiplier is on — Day 1 trades enjoy a 3.0x multiplier. "
     "Only buy volume counts (selling is excluded)."),
    ("DeFi API",
     "Introducing DeFi API! Building DeFi into your app just got easier. Powered by Web3 API "
     "infrastructure, the new DeFi API enables developers to integrate DeFi capabilities without "
     "building individual protocol integrations from scratch. 10 EVM chains: Including BSC, "
     "Ethereum, Base, and Robinhood Chain."),
    ("Referral Programme",
     "Exploring Web3 with friends? The Binance Wallet Referral Programme lets you share your "
     "experience with people you know. Get your referral link in the app. Rewards, rates and "
     "eligibility are set out in the programme terms."),
    ("Prediction Market",
     "The European Football League continues! Manchester City vs. Paris Saint-Germain Prediction "
     "Market is now live. Follow the match, support your team, and trade your view!"),
    ("TRON Carnival",
     "Maximize Your Returns with Binance Wallet TRON Carnival Season 3! Subscribe to JustLend DAO "
     "promoted pools on TRON DAO, JUST DAO, SUN.io and USDD via #BinanceWallet DeFi to unlock "
     "attractive APRs up to 27.7%!"),
]

# Real messages captured from the official binance_announcements Telegram
# channel (Phase 2, live capture) — Alpha competitions and other activities
# must NOT be reported as Alpha airdrops.
_ANNOUNCEMENTS_NEGATIVES = [
    ("Ann. COAI Competition",
     "Binance Alpha Trading Competition: Trade ChainOpera AI (COAI) and Share $200K Worth of "
     "Rewards (2026-09-24). Binance Wallet is excited to launch the COAI Trading Competition on "
     "Binance Alpha! During the Promotion Periods, trade ChainOpera AI (COAI) in your Binance "
     "Wallet (Keyless) or via Binance Alpha to receive exclusive token rewards. Any user who is "
     "eligible to trade Binance Alpha tokens is eligible to participate in this trading "
     "competition."),
    ("Ann. XDP Competition",
     "Binance Alpha Trading Competition: Trade Doppler Finance (XDP) and Share $200K Worth of "
     "Rewards (2026-09-29). Binance Wallet is excited to launch the XDP Trading Competition on "
     "Binance Alpha! During the Promotion Periods, trade Doppler Finance (XDP) in your Binance "
     "Wallet (Keyless) or via Binance Alpha to receive exclusive token rewards."),
    ("Ann. O Competition",
     "Binance Alpha Trading Competition: Trade o1.exchange (O) and Share $200K Worth of Rewards "
     "(2026-09-30). Binance Wallet is excited to launch the O Trading Competition on Binance "
     "Alpha! During the Promotion Periods, trade o1.exchange (O) in your Binance Wallet "
     "(Keyless) or via Binance Alpha to receive exclusive token rewards."),
    ("Ann. Starter Carnival",
     "The Binance Starter Carnival: Complete Tasks to Share $100,000 Worth of Rewards in BTC. "
     "The Binance Starter Carnival new edition is now on! Eligible users can complete new user "
     "deposit and trading tasks to get a share of a BTC token voucher reward pool valued at "
     "$100,000."),
    ("Ann. Earn APR",
     "Binance Earn: Enjoy Up to 7% APR with USDT Flexible Products. Binance Earn is excited to "
     "renew the campaign for the USDT Simple Earn Flexible Products!"),
    ("Ann. Token Airdrop (non-Alpha)",
     "Binance Will Support the SpaceX (SPCXB) Airdrop for MarsCoin (MARSCOIN) Holders and the "
     "Invesco QQQ Trust (QQQB) Airdrop for 牛来 (牛来) Holders."),
]

# Alpha Box airdrop announcements must match (Phase 2 P1 requirement)
_ALPHA_BOX_POSITIVES = [
    ("Alpha Box Airdrop",
     "Binance Alpha Box airdrop is live! Users with at least 245 Binance Alpha Points can claim "
     "an airdrop on a first-come, first-served basis."),
]

# Real channel positives — verbatim from the official channel
_REAL_POSITIVES = [
    ("DNA PRE-ALERT",
     "Binance Alpha will be the first platform to feature NeoSoul (DNA) on October 16. "
     "Eligible users can claim their airdrop using Binance Alpha Points on the Alpha Events page "
     "once trading opens. Further details will be announced soon."),
    ("DNA DETAIL",
     "Binance Alpha is the first platform to feature NeoSoul (DNA), with Alpha debut and trading "
     "starting on October 16, 2026, at 08:00 (UTC). Users with at least 245 Binance Alpha Points "
     "can claim an airdrop of 400 DNA tokens on a first-come, first-served basis. If the reward "
     "pool is not fully distributed, the score threshold will automatically decrease by 5 points "
     "every 5 minutes. Please note that claiming the airdrop will consume 15 Binance Alpha Points. "
     "Users must confirm their claim on the Alpha Events page within 24 hours."),
    ("DRV PRE-ALERT",
     "Please get ready to claim the Binance Alpha airdrop and trade today at 10:00 (UTC). "
     "Users with at least 245 Binance Alpha Points can claim the token on a first-come, "
     "first-served basis until the airdrop pool is fully distributed or the airdrop event expires. "
     "Further details will be announced soon."),
    ("DRV LIVE",
     "Derive (DRV) is now live on Binance Alpha! Users with at least 245 Binance Alpha Points "
     "can claim an airdrop of 70 DRV tokens on a first-come, first-served basis. If the reward "
     "pool is not fully distributed, the score threshold will automatically decrease by 5 points "
     "every 5 minutes. Please note that claiming the airdrop will consume 15 Binance Alpha Points. "
     "Users must confirm their claim on the Alpha Events page within 24 hours."),
]


def _make_msg(text: str, published_at=None, tag: str = "t",
              source: str = "selftest") -> SourceMessage:
    return SourceMessage(
        source=source,
        source_message_id=f"{tag}_{abs(hash(text)) % 10**10}",
        raw_text=text,
        published_at=published_at or utcnow(),
        message_url=f"https://{source}/{tag}",
    )


def run_alpha_tests() -> int:
    from app.matcher import matches_alpha_airdrop
    from app.normalize import normalize_text

    print("=" * 62)
    print("OFFLINE KEYWORD TESTS  (spec §30)")
    print("=" * 62)
    failures = 0

    print("\n-- positive (must MATCH) --")
    for name, text in _POSITIVE_CASES + _REAL_POSITIVES + _ALPHA_BOX_POSITIVES:
        norm = normalize_text(text)
        ok = matches_alpha_airdrop(norm)
        print(f"  {name:<18} {'PASS' if ok else 'FAIL'}")
        failures += 0 if ok else 1

    print("\n-- negative (must NOT match) --")
    for name, text in _NEGATIVE_CASES + _REAL_NEGATIVES + _ANNOUNCEMENTS_NEGATIVES:
        norm = normalize_text(text)
        ok = not matches_alpha_airdrop(norm)
        print(f"  {name:<18} {'PASS' if ok else 'FAIL'}")
        failures += 0 if ok else 1

    # --- field extraction on the real texts ------------------------------
    print("\n-- extraction (real channel texts) --")
    from app.extractor import extract_fields

    from app.timeutil import utcnow as _utc

    dna_detail = _REAL_POSITIVES[1][1]
    drv_live = _REAL_POSITIVES[3][1]
    drv_pre = _REAL_POSITIVES[2][1]

    pub = _utc()
    f1 = extract_fields(dna_detail, pub)
    checks = [
        ("DNA project", f1.project_name == "NeoSoul", f1.project_name),
        ("DNA symbol", f1.symbol == "DNA", f1.symbol),
        ("DNA points", f1.points_threshold == 245, f1.points_threshold),
        ("DNA amount", f1.airdrop_amount == 400, f1.airdrop_amount),
        ("DNA cost", f1.points_cost == 15, f1.points_cost),
        ("DNA start UTC", iso_utc(f1.start_time) == "2026-10-16T08:00:00Z", iso_utc(f1.start_time)),
    ]
    # DRV pre-alert: relative "today at 10:00 (UTC)" anchored to Oct 14
    drv_pub = _utc().replace(year=2026, month=10, day=14, hour=8, minute=1)
    f2 = extract_fields(drv_pre, drv_pub)
    checks += [
        ("DRV-pre start UTC", iso_utc(f2.start_time) == "2026-10-14T10:00:00Z", iso_utc(f2.start_time)),
        ("DRV-pre points", f2.points_threshold == 245, f2.points_threshold),
    ]
    f3 = extract_fields(drv_live, drv_pub)
    checks += [
        ("DRV-live project", f3.project_name == "Derive", f3.project_name),
        ("DRV-live symbol", f3.symbol == "DRV", f3.symbol),
        ("DRV-live amount", f3.airdrop_amount == 70, f3.airdrop_amount),
        ("DRV-live cost", f3.points_cost == 15, f3.points_cost),
    ]

    for name, ok, got in checks:
        print(f"  {name:<18} {'PASS' if ok else 'FAIL'}  (got: {got})")
        failures += 0 if ok else 1

    # --- event type classification ---------------------------------------
    from app.event_engine import classify_event_type

    print("\n-- event type classification --")
    from app.normalize import normalize_text

    pub2 = _utc()
    f_pre = extract_fields(_REAL_POSITIVES[0][1], pub2)
    f_detail = extract_fields(_REAL_POSITIVES[1][1], pub2)
    f_live = extract_fields(_REAL_POSITIVES[3][1], pub2)
    type_checks = [
        ("PRE-ALERT", normalize_text(_REAL_POSITIVES[0][1]), None, f_pre, "ALPHA_AIRDROP_PRE_ALERT"),
        ("DETAIL(new)", normalize_text(_REAL_POSITIVES[1][1]), None, f_detail, "ALPHA_AIRDROP_DETAIL"),
        ("UPDATE(existing)", normalize_text(_REAL_POSITIVES[1][1]), "existing", f_detail, "ALPHA_AIRDROP_UPDATE"),
        ("LIVE", normalize_text(_REAL_POSITIVES[3][1]), None, f_live, "ALPHA_AIRDROP_LIVE"),
    ]
    for name, norm, existing, fields, expect in type_checks:
        got = classify_event_type(norm, "existing" if existing else None, fields)
        ok = got == expect
        print(f"  {name:<18} {'PASS' if ok else 'FAIL'}  (got: {got}, want: {expect})")
        failures += 0 if ok else 1

    print(f"\nRESULT: {'ALL PASS' if failures == 0 else f'{failures} FAILURE(S)'}")
    return 0 if failures == 0 else 1


# ==========================================================================
# spec §31 dual-stage reminder tests
# ==========================================================================

_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]


def _event_msg(project: str, symbol: str, start_dt, amount: int = 100,
               points: int = 245, cost: int = 15, tag: str = "evt",
               source: str = "selftest") -> SourceMessage:
    d = start_dt.astimezone(UTC)
    text = (
        f"Binance Alpha will feature {project} ({symbol}) with Alpha debut and trading "
        f"starting on {_MONTHS[d.month - 1]} {d.day}, {d.year}, at {d.strftime('%H:%M')} (UTC). "
        f"Users with at least {points} Binance Alpha Points can claim an airdrop of {amount} "
        f"{symbol} tokens on a first-come, first-served basis. Claiming the airdrop will consume "
        f"{cost} Binance Alpha Points."
    )
    return _make_msg(text, published_at=utcnow(), tag=tag, source=source)


def _fmt(dt) -> str:
    return dt.astimezone(UTC).strftime("%H:%M:%S")


def run_reminder_tests() -> int:
    from app import reminders
    from app.scheduler import ReminderScheduler
    from app.templates import build_discovered

    print("=" * 62)
    print("DUAL-STAGE REMINDER TESTS  (spec §31)")
    print("=" * 62)
    failures = 0

    def check(name: str, ok: bool, extra: str = "") -> None:
        nonlocal failures
        print(f"  {name:<34} {'PASS' if ok else 'FAIL'}  {extra}")
        failures += 0 if ok else 1

    tmp = tempfile.mkdtemp(prefix="ba_test_")
    db_path = str(Path(tmp) / "test.db")

    # ---- Scenario 1: start = now + 30 min --------------------------------
    print("\n-- scenario 1: start = now+30min (early discovery) --")
    db = Database(db_path)
    now = utcnow()
    # align to whole minute: the test message text carries HH:MM (no seconds)
    start1 = (now + timedelta(minutes=30)).replace(second=0, microsecond=0)
    res = process_message(db, _event_msg("TestCoin", "TST", start1, tag="s1"), notify=True)
    check("DISCOVERED notification", "DISCOVERED" in res.actions)
    check("REMINDER created", "REMINDER_CREATED" in res.actions)
    pending = db.count_pending_reminders()
    check("1 pending reminder", pending == 1)
    t10 = reminders.scheduled_reminder_time(res.event)
    check("T-10 == start - 10min", t10 is not None and abs((t10 - (start1 - timedelta(minutes=10))).total_seconds()) < 2,
          f"t10={_fmt(t10)}")
    notif_row = None
    with db.session() as conn:
        notif_row = conn.execute(
            "SELECT * FROM notifications WHERE event_id=? AND notification_type='DISCOVERED'",
            (res.event.id,),
        ).fetchone()
    check("DISCOVERED persisted", notif_row is not None and notif_row["status"] in ("SENT", "DRY_RUN"))
    db_path1 = db_path

    # ---- Scenario 2: start = now + 5 min ---------------------------------
    print("\n-- scenario 2: start = now+5min (T-10 window already passed) --")
    db2 = Database(str(Path(tmp) / "test2.db"))
    start2 = (utcnow() + timedelta(minutes=5)).replace(second=0, microsecond=0)
    res2 = process_message(db2, _event_msg("TestCoin2", "TST2", start2, tag="s2"), notify=True)
    check("DISCOVERED sent", "DISCOVERED" in res2.actions)
    check("no T-10 reminder (PAST_WINDOW)", db2.count_pending_reminders() == 0)

    # ---- Scenario 3: start = now - 5 min (already started) ---------------
    print("\n-- scenario 3: start = now-5min (already started) --")
    db3 = Database(str(Path(tmp) / "test3.db"))
    start3 = utcnow() - timedelta(minutes=5)
    res3 = process_message(db3, _event_msg("TestCoin3", "TST3", start3, tag="s3"), notify=True)
    check("DISCOVERED sent", "DISCOVERED" in res3.actions)
    check("no T-10 reminder", db3.count_pending_reminders() == 0)
    title, content = build_discovered(res3.event, started=True)
    check("live content ('已经开始')", "已经开始" in content)

    # ---- Scenario 4: start time changed 16:00 -> 17:00 --------------------
    print("\n-- scenario 4: start time update (16:00 -> 17:00) --")
    db4 = Database(str(Path(tmp) / "test4.db"))
    from app.timeutil import parse_iso

    t_a = parse_iso("2026-10-20T08:00:00Z")  # 16:00 +08:00
    t_b = parse_iso("2026-10-20T09:00:00Z")  # 17:00 +08:00
    r4a = process_message(db4, _event_msg("TimeCoin", "TMC", t_a, tag="s4a"), notify=True)
    r4b = process_message(db4, _event_msg("TimeCoin", "TMC", t_b, tag="s4b"), notify=True)
    with db4.session() as conn:
        rows = conn.execute(
            "SELECT scheduled_at, status FROM event_reminders WHERE event_id=? "
            "ORDER BY id", (r4a.event.id,),
        ).fetchall()
    check("two reminder rows (old cancelled + new)", len(rows) == 2, f"rows={len(rows)}")
    statuses = [r["status"] for r in rows]
    check("old reminder CANCELLED", statuses[0] == "CANCELLED", str(statuses))
    check("new reminder PENDING", statuses[1] == "PENDING")
    check("new T-10 == 16:50 +08:00", parse_iso(rows[1]["scheduled_at"]) == parse_iso("2026-10-20T08:50:00Z"),
          f"scheduled={rows[1]['scheduled_at']}")
    with db4.session() as conn:
        upd = conn.execute(
            "SELECT COUNT(*) c FROM notifications WHERE event_id=? AND notification_type='EVENT_UPDATED'",
            (r4a.event.id,),
        ).fetchone()["c"]
    check("EVENT_UPDATED notification sent", upd == 1)

    # ---- Scenario 5: restart recovery -------------------------------------
    print("\n-- scenario 5: restart recovery from SQLite --")
    db5a = Database(str(Path(tmp) / "test5.db"))
    start5 = utcnow() + timedelta(hours=2)
    r5 = process_message(db5a, _event_msg("RestartCoin", "RSC", start5, tag="s5"), notify=True)
    check("pending reminder before restart", db5a.count_pending_reminders() == 1)
    # simulate restart: new Database instance on the same file
    db5b = Database(str(Path(tmp) / "test5.db"))
    stop = __import__("threading").Event()
    sched = ReminderScheduler(db5b, stop, tick_seconds=5, grace_seconds=90)
    recovered = sched.recover()
    check("recovered from SQLite after restart", recovered == 1)
    # a past-beyond-grace reminder -> MISSED, not sent
    past = utcnow() - timedelta(minutes=5)
    db5b.insert_reminder(r5.event.id, reminders.REMINDER_TYPE_T10, past)
    sched._check_due()
    with db5b.session() as conn:
        missed = conn.execute(
            "SELECT COUNT(*) c FROM event_reminders WHERE event_id=? AND status='MISSED'",
            (r5.event.id,),
        ).fetchone()["c"]
    check("expired reminder marked MISSED (no backfill)", missed == 1)
    # a reminder due inside the grace window -> sent
    grace_due = utcnow() - timedelta(seconds=30)
    db5b.insert_reminder(r5.event.id, reminders.REMINDER_TYPE_T10, grace_due)
    sched._check_due()
    with db5b.session() as conn:
        sent = conn.execute(
            "SELECT COUNT(*) c FROM event_reminders WHERE event_id=? AND status='SENT'",
            (r5.event.id,),
        ).fetchone()["c"]
        t10_notifs = conn.execute(
            "SELECT COUNT(*) c FROM notifications WHERE event_id=? AND notification_type='START_MINUS_10_MINUTES'",
            (r5.event.id,),
        ).fetchone()["c"]
    check("grace-window reminder fired", sent == 1)
    check("T-10 notification sent once", t10_notifs == 1)
    # future reminder stays PENDING
    with db5b.session() as conn:
        pend = conn.execute(
            "SELECT COUNT(*) c FROM event_reminders WHERE event_id=? AND status='PENDING'",
            (r5.event.id,),
        ).fetchone()["c"]
    check("future reminder still PENDING", pend == 1)

    # ---- dedup check -------------------------------------------------------
    print("\n-- dedup: same event + type sent at most once --")
    db6 = Database(str(Path(tmp) / "test6.db"))
    start6 = utcnow() + timedelta(hours=1)
    m1 = _event_msg("DupCoin", "DUPC", start6, tag="s6a")
    m2 = _event_msg("DupCoin", "DUPC", start6, tag="s6b")  # identical event, new message
    process_message(db6, m1, notify=True)
    process_message(db6, m2, notify=True)
    with db6.session() as conn:
        cnt = conn.execute(
            "SELECT COUNT(*) c FROM notifications WHERE notification_type='DISCOVERED'"
        ).fetchone()["c"]
    check("DISCOVERED sent exactly once", cnt == 1, f"count={cnt}")

    print(f"\nRESULT: {'ALL PASS' if failures == 0 else f'{failures} FAILURE(S)'}")
    return 0 if failures == 0 else 1


# ==========================================================================
# Phase 2 §7: cross-source merge & notification regression tests
# ==========================================================================

def run_cross_source_tests() -> int:
    from app import reminders
    from app.event_engine import process_message
    from app.scheduler import ReminderScheduler

    print("=" * 62)
    print("CROSS-SOURCE MERGE TESTS  (Phase 2 §7)")
    print("=" * 62)
    failures = 0

    def check(name: str, ok: bool, extra: str = "") -> None:
        nonlocal failures
        print(f"  {name:<44} {'PASS' if ok else 'FAIL'}  {extra}")
        failures += 0 if ok else 1

    def count_notif(db, ntype: str) -> int:
        with db.session() as conn:
            return conn.execute(
                "SELECT COUNT(*) c FROM notifications WHERE notification_type=?",
                (ntype,),
            ).fetchone()["c"]

    def count_events(db) -> int:
        with db.session() as conn:
            return conn.execute("SELECT COUNT(*) c FROM alpha_events").fetchone()["c"]

    def src_count(db, event_id: int) -> list:
        return [s["source"] for s in db.get_event_sources(event_id)]

    def feed(db, msg, notify: bool = True):
        """Mirror the production poller: persist the message first, then run
        the engine (needed for message-text lookups during association)."""
        db.insert_message_if_new(
            source=msg.source,
            source_message_id=msg.source_message_id,
            raw_text=msg.raw_text,
            normalized_text=msg.normalized_text,
            published_at=msg.published_at,
            message_url=msg.message_url,
        )
        return process_message(db, msg, notify=notify)

    tmp = tempfile.mkdtemp(prefix="ba_xsrc_")

    # ---- 1) TG + Square identical content -> one notification ---------------
    print("\n-- 1) TG + Square identical content -> single event, one notification --")
    db1 = Database(str(Path(tmp) / "t1.db"))
    start1 = (utcnow() + timedelta(hours=2)).replace(second=0, microsecond=0)
    text1 = (
        f"Binance Alpha will be the first platform to feature Omega (OMG) on "
        f"{_MONTHS[start1.month - 1]} {start1.day}, {start1.year}, at "
        f"{start1.strftime('%H:%M')} (UTC). Users with at least 245 Binance Alpha "
        f"Points can claim an airdrop of 120 OMG tokens on a first-come, "
        f"first-served basis. Claiming the airdrop will consume 15 Points."
    )
    m_tg = SourceMessage(source="telegram_wallet", source_message_id="tg_omg_1",
                         raw_text=text1, published_at=utcnow(),
                         message_url="https://t.me/binance_wallet_announcements/1")
    m_sq = SourceMessage(source="binance_square", source_message_id="sq_omg_1",
                         raw_text=text1, published_at=utcnow(),
                         message_url="https://www.binance.com/en/square/post/1")
    r1a = feed(db1, m_tg, notify=True)
    r1b = feed(db1, m_sq, notify=True)
    check("TG creates event", r1a.created)
    check("Square merges (not created)", not r1b.created)
    check("single canonical event", count_events(db1) == 1)
    check("DISCOVERED exactly once", count_notif(db1, "DISCOVERED") == 1,
          f"count={count_notif(db1, 'DISCOVERED')}")
    check("no EVENT_UPDATED on cross-post", count_notif(db1, "EVENT_UPDATED") == 0)
    check("both sources recorded", sorted(src_count(db1, r1a.event.id)) ==
          ["binance_square", "telegram_wallet"], str(src_count(db1, r1a.event.id)))

    # ---- 2) TG + Square different wording, same event ------------------------
    print("\n-- 2) TG + Square different text, same event -> one notification --")
    db2 = Database(str(Path(tmp) / "t2.db"))
    start2 = (utcnow() + timedelta(hours=2)).replace(second=0, microsecond=0)
    tg2 = (
        f"Binance Alpha will be the first platform to feature Nova (NVA) on "
        f"{_MONTHS[start2.month - 1]} {start2.day}, {start2.year}, at "
        f"{start2.strftime('%H:%M')} (UTC). Eligible users can claim their airdrop "
        f"using Binance Alpha Points."
    )
    sq2 = (
        f"Get ready to claim the Binance Alpha airdrop for Nova (NVA)! Trading "
        f"starts {_MONTHS[start2.month - 1]} {start2.day}, {start2.year}, at "
        f"{start2.strftime('%H:%M')} (UTC). Eligible users can claim their airdrop "
        f"using Binance Alpha Points."
    )
    feed(db2, SourceMessage(source="telegram_wallet",
                                       source_message_id="tg_nva_1", raw_text=tg2,
                                       published_at=utcnow()), notify=True)
    r2b = feed(db2, SourceMessage(source="binance_square",
                                             source_message_id="sq_nva_1", raw_text=sq2,
                                             published_at=utcnow()), notify=True)
    check("Square merges same event", not r2b.created)
    check("one event", count_events(db2) == 1)
    check("DISCOVERED once", count_notif(db2, "DISCOVERED") == 1)
    check("no EVENT_UPDATED (rephrase)", count_notif(db2, "EVENT_UPDATED") == 0)

    # ---- 3) three official channels publish same event sequentially ----------
    print("\n-- 3) three official channels, same event -> one event + one T-10 --")
    db3 = Database(str(Path(tmp) / "t3.db"))
    start3 = (utcnow() + timedelta(hours=2)).replace(second=0, microsecond=0)
    msgs3 = [
        ("telegram_wallet", "tg_abc_1", "Binance Alpha will feature ABC (ABC3) on "
         f"{_MONTHS[start3.month - 1]} {start3.day}, {start3.year}, at {start3.strftime('%H:%M')} (UTC). "
         "Eligible users can claim their airdrop using Binance Alpha Points."),
        ("telegram_announcements", "ann_abc_1",
         f"Binance Alpha airdrop details: ABC (ABC3) listing on "
         f"{_MONTHS[start3.month - 1]} {start3.day}, {start3.year}, at {start3.strftime('%H:%M')} (UTC). "
         "Users with at least 245 Alpha Points can claim an airdrop of 200 ABC3 tokens."),
        ("binance_square", "sq_abc_1",
         f"Binance Alpha will feature ABC (ABC3), with Alpha debut and trading starting on "
         f"{_MONTHS[start3.month - 1]} {start3.day}, {start3.year}, at {start3.strftime('%H:%M')} (UTC). "
         "Claim 200 ABC3 tokens with 245 Alpha Points, first-come first-served."),
    ]
    ev3 = None
    for source, mid, text in msgs3:
        r = feed(db3, SourceMessage(source=source, source_message_id=mid,
                                               raw_text=text, published_at=utcnow()), notify=True)
        ev3 = r.event
    check("one event across 3 channels", count_events(db3) == 1)
    check("DISCOVERED once", count_notif(db3, "DISCOVERED") == 1)
    check("T-10 plan exactly one PENDING", db3.count_pending_reminders() == 1)
    check("3 sources recorded", sorted(src_count(db3, ev3.id)) ==
          ["binance_square", "telegram_announcements", "telegram_wallet"],
          str(src_count(db3, ev3.id)))

    # ---- 4) unknown pre-alert -> detail claims; start reveal = EVENT_UPDATED --
    print("\n-- 4) unknown pre-alert claimed by detail message --")
    from app.timeutil import parse_iso
    db4 = Database(str(Path(tmp) / "t4.db"))
    pre4 = "Please get ready to claim the Binance Alpha airdrop. Further details will be announced soon."
    d4_pub = parse_iso("2026-10-20T06:00:00Z")      # 14:00 +08:00, Oct 20
    d4_start = parse_iso("2026-10-20T08:00:00Z")    # 16:00 +08:00, Oct 20
    detail4 = (
        f"Binance Alpha will feature Luna (LNA) with Alpha debut and trading starting on "
        f"{_MONTHS[d4_start.month - 1]} {d4_start.day}, {d4_start.year}, at {d4_start.strftime('%H:%M')} (UTC). "
        "Users with at least 245 Binance Alpha Points can claim an airdrop of 90 LNA tokens. "
        "Claiming the airdrop will consume 15 Binance Alpha Points."
    )
    r4a = feed(db4, SourceMessage(source="telegram_wallet",
                                             source_message_id="tg_lna_pre", raw_text=pre4,
                                             published_at=d4_pub), notify=True)
    check("unknown pre-alert notified immediately", "DISCOVERED" in r4a.actions)
    check("unknown key unique", "alpha:unknown:" in r4a.event.event_key, r4a.event.event_key)
    r4b = feed(db4, SourceMessage(source="binance_square",
                                             source_message_id="sq_lna_detail", raw_text=detail4,
                                             published_at=d4_pub + timedelta(minutes=2)), notify=True)
    check("detail claims the unknown event", r4b.event.event_key == "alpha:LNA:2026-10-20",
          r4b.event.event_key)
    check("still one event", count_events(db4) == 1)
    check("start reveal -> EVENT_UPDATED once", count_notif(db4, "EVENT_UPDATED") == 1)
    check("T-10 created after start known", db4.count_pending_reminders() == 1)

    # ---- 5) two DIFFERENT unknown activities same day -> separate events -----
    print("\n-- 5) two different unknown activities same day stay separate --")
    db5 = Database(str(Path(tmp) / "t5.db"))
    unk_a = "Please get ready to claim the Binance Alpha airdrop for the first project. Further details soon."
    unk_b = "Another Binance Alpha airdrop announcement: a second project will be claimable. Stay tuned!"
    r5a = feed(db5, SourceMessage(source="telegram_wallet",
                                             source_message_id="tg_u1", raw_text=unk_a,
                                             published_at=utcnow()), notify=True)
    r5b = feed(db5, SourceMessage(source="telegram_wallet",
                                             source_message_id="tg_u2", raw_text=unk_b,
                                             published_at=utcnow()), notify=True)
    check("two unknown events (not merged)", count_events(db5) == 2, f"events={count_events(db5)}")
    check("distinct keys", r5a.event.event_key != r5b.event.event_key)
    check("each notified once", count_notif(db5, "DISCOVERED") == 2)

    # ---- 5b) identical unknown text cross-source same day -> merged ----------
    print("\n-- 5b) identical unknown text on Square merges --")
    r5c = feed(db5, SourceMessage(source="binance_square",
                                             source_message_id="sq_u1", raw_text=unk_a,
                                             published_at=utcnow()), notify=True)
    check("Square duplicate merged into first event", not r5c.created)
    check("still two events", count_events(db5) == 2)
    check("DISCOVERED still 2", count_notif(db5, "DISCOVERED") == 2)

    # ---- 6) start time changed via a second channel --------------------------
    print("\n-- 6) start time changed via another channel -> update + new T-10 --")
    db6 = Database(str(Path(tmp) / "t6.db"))
    t_a = parse_iso("2026-10-20T08:00:00Z")  # 16:00 +08:00
    t_b = parse_iso("2026-10-20T09:00:00Z")  # 17:00 +08:00
    r6a = feed(db6, _event_msg("Time2", "TM2", t_a, tag="x6a",
                                          source="telegram_wallet"), notify=True)
    r6b = feed(db6, _event_msg("Time2", "TM2", t_b, tag="x6b",
                                          source="binance_square"), notify=True)
    check("same event updated", r6b.event.id == r6a.event.id)
    check("EVENT_UPDATED once", count_notif(db6, "EVENT_UPDATED") == 1)
    with db6.session() as conn:
        rows = conn.execute(
            "SELECT status FROM event_reminders WHERE event_id=? ORDER BY id",
            (r6a.event.id,),
        ).fetchall()
    statuses = [r["status"] for r in rows]
    check("old T-10 cancelled, new T-10 pending", statuses == ["CANCELLED", "PENDING"],
          str(statuses))

    # ---- 7) T-10 notification fires exactly once (dedup) ---------------------
    print("\n-- 7) T-10 fires once for a multi-source event --")
    db7 = Database(str(Path(tmp) / "t7.db"))
    start7 = (utcnow() + timedelta(hours=2)).replace(second=0, microsecond=0)
    ev7 = None
    for source, mid in (("telegram_wallet", "tg_dedup_1"),
                        ("binance_square", "sq_dedup_1"),
                        ("telegram_announcements", "ann_dedup_1")):
        r = feed(db7, _event_msg("Dedup2", "DD2", start7, tag=mid,
                                            source=source), notify=True)
        ev7 = r.event
    stop = __import__("threading").Event()
    sched = ReminderScheduler(db7, stop, tick_seconds=5, grace_seconds=90)
    sched._check_due()
    # force the due reminder inside the grace window (simulating a T-10 tick)
    with db7.session() as conn:
        conn.execute(
            "UPDATE event_reminders SET scheduled_at=? WHERE event_id=? AND status='PENDING'",
            (iso_utc(utcnow() - timedelta(seconds=10)), ev7.id),
        )
    sched._check_due()
    check("START_MINUS_10_MINUTES sent once", count_notif(db7, "START_MINUS_10_MINUTES") == 1,
          f"count={count_notif(db7, 'START_MINUS_10_MINUTES')}")

    print(f"\nRESULT: {'ALL PASS' if failures == 0 else f'{failures} FAILURE(S)'}")
    return 0 if failures == 0 else 1


# ==========================================================================
# Phase 3: Feishu (Lark) custom-bot webhook channel tests
# ==========================================================================

def run_feishu_tests() -> int:
    from app.notifiers import base as nb
    from app.notifiers.feishu import (
        FEISHU_WEBHOOK_PREFIX,
        build_text_payload,
        truncate_utf8,
        validate_webhook_url,
    )

    print("=" * 62)
    print("FEISHU WEBHOOK CHANNEL TESTS  (Phase 3)")
    print("=" * 62)
    failures = 0

    def check(name: str, ok: bool, extra: str = "") -> None:
        nonlocal failures
        print(f"  {name:<44} {'PASS' if ok else 'FAIL'}  {extra}")
        failures += 0 if ok else 1

    # ---- 1) webhook URL validation ----------------------------------------
    print("\n-- 1) webhook URL validation --")
    good = FEISHU_WEBHOOK_PREFIX + "abc123token"
    check("valid webhook accepted", validate_webhook_url(good) is True)
    check("empty rejected", validate_webhook_url("") is False)
    check("None rejected", validate_webhook_url(None) is False)
    check("http scheme rejected", validate_webhook_url(good.replace("https://", "http://")) is False)
    check("foreign host rejected",
          validate_webhook_url("https://example.com/open-apis/bot/v2/hook/x") is False)
    check("missing path rejected", validate_webhook_url("https://open.feishu.cn/") is False)

    # ---- 2) payload construction -------------------------------------------
    print("\n-- 2) payload construction --")
    payload = build_text_payload("🚨 标题", "第一行\n第二行")
    check("msg_type=text", payload.get("msg_type") == "text", str(payload.get("msg_type")))
    text = payload["content"]["text"]
    check("title embedded", "🚨 标题" in text)
    check("content embedded", "第一行\n第二行" in text)

    # ---- 3) UTF-8 byte truncation ------------------------------------------
    print("\n-- 3) UTF-8 truncation (4096-byte limit) --")
    short = "short message"
    check("short kept intact", truncate_utf8(short) == short)
    long_text = "空投提醒" * 3000  # 4 chars x 3 bytes -> ~12000 bytes
    cut = truncate_utf8(long_text)
    check("long truncated", len(cut) < len(long_text))
    check("fits byte limit", len(cut.encode("utf-8")) <= 4000,
          f"bytes={len(cut.encode('utf-8'))}")
    # a half-cut multibyte char would be dropped by re-decoding, breaking equality
    check("no broken multibyte char", cut.encode("utf-8").decode("utf-8") == cut,
          f"tail={cut[-3:]!r}")
    sweep_ok = all(
        truncate_utf8(long_text, k).encode("utf-8").decode("utf-8") == truncate_utf8(long_text, k)
        for k in range(1, 200, 7)
    )
    check("sweep byte limits, no half char", sweep_ok)

    # ---- 4) FanoutProvider behaviour (fake channels, no network) -----------
    print("\n-- 4) multi-channel fan-out --")

    class _Fake(nb.NotificationProvider):
        def __init__(self, name: str, ok: bool, dry: bool = False):
            self.name = name
            self._ok = ok
            self.dry_run = dry

        def send(self, title, content, url=None):
            return (True, None) if self._ok else (False, f"{self.name} boom")

    both_ok = nb.FanoutProvider([_Fake("a", True), _Fake("b", True)])
    ok, err = both_ok.send("t", "c")
    check("all channels ok -> ok", ok, f"err={err}")

    partial = nb.FanoutProvider([_Fake("a", True), _Fake("b", False)])
    ok, err = partial.send("t", "c")
    check("one channel fails -> still ok", ok)
    check("failure reported in log only", err is None)

    all_fail = nb.FanoutProvider([_Fake("a", False), _Fake("b", False)])
    ok, err = all_fail.send("t", "c")
    check("all channels fail -> not ok", ok is False)
    check("errors joined", "boom" in (err or ""), f"err={err}")

    # ---- 5) provider selection ---------------------------------------------
    print("\n-- 5) provider selection (force_dry_run / no config) --")
    nb.reset_provider()
    saved_force = nb.force_dry_run
    try:
        nb.force_dry_run = True
        prov = nb.get_provider()
        check("force_dry_run -> DryRunProvider", isinstance(prov, nb.DryRunProvider))
    finally:
        nb.force_dry_run = saved_force
        nb.reset_provider()

    print(f"\nRESULT: {'ALL PASS' if failures == 0 else f'{failures} FAILURE(S)'}")
    return 0 if failures == 0 else 1
