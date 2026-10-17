"""Central configuration, loaded from environment variables (never hardcoded).

See .env.example in the project root for the full list.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    try:
        return int(v)
    except ValueError:
        return default


class Config:
    # --- general -----------------------------------------------------------
    APP_ENV = os.getenv("APP_ENV", "production")
    TIMEZONE = os.getenv("TIMEZONE", "Asia/Shanghai")

    # --- Telegram source ----------------------------------------------------
    # v2: explicit allowlist of official sources only (no third-party feeds).
    # Recognized keys: telegram_wallet, binance_square, telegram_announcements
    SOURCES_ALLOWLIST = [
        s.strip() for s in os.getenv(
            "SOURCES_ALLOWLIST",
            "telegram_wallet,binance_square,telegram_announcements",
        ).split(",") if s.strip()
    ]
    TELEGRAM_SOURCE_ENABLED = _env_bool("TELEGRAM_SOURCE_ENABLED", True)
    TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL", "binance_wallet_announcements")
    TELEGRAM_FETCH_TIMEOUT = _env_int("TELEGRAM_FETCH_TIMEOUT", 15)
    TELEGRAM_MAX_RETRIES = _env_int("TELEGRAM_MAX_RETRIES", 2)
    TELEGRAM_USER_AGENT = os.getenv(
        "TELEGRAM_USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    )
    # Sandbox/offline verification: point to a fixture text file instead of t.me
    TELEGRAM_FIXTURE = os.getenv("TELEGRAM_FIXTURE", "").strip()

    # --- Binance official English announcements Telegram (supplementary) ---
    TELEGRAM_ANNOUNCEMENTS_ENABLED = _env_bool("TELEGRAM_ANNOUNCEMENTS_ENABLED", True)
    TELEGRAM_ANNOUNCEMENTS_CHANNEL = os.getenv(
        "TELEGRAM_ANNOUNCEMENTS_CHANNEL", "binance_announcements")
    TELEGRAM_ANNOUNCEMENTS_FIXTURE = os.getenv(
        "TELEGRAM_ANNOUNCEMENTS_FIXTURE", "").strip()

    # --- Binance Square (Binance Wallet official account) -------------------
    SQUARE_ENABLED = _env_bool("SQUARE_ENABLED", True)
    SQUARE_PROFILE = os.getenv("SQUARE_PROFILE", "binancewallet")
    SQUARE_API_BASE = os.getenv("SQUARE_API_BASE", "https://www.binance.com")
    # blocked = default: no stable lightweight public API exists (bapi needs
    # browser fingerprint headers; robots.txt disallows automated access).
    # attempt = try the documented bapi endpoints anyway (expect data:null).
    # fixture = sandbox/offline verification against a saved envelope.
    SQUARE_MODE = os.getenv("SQUARE_MODE", "blocked").strip().lower()
    SQUARE_FIXTURE = os.getenv("SQUARE_FIXTURE", "").strip()
    # optional JSON object of extra headers (bnc-uuid, csrftoken, device-info...)
    SQUARE_EXTRA_HEADERS = os.getenv("SQUARE_EXTRA_HEADERS", "").strip()
    SQUARE_TIMEOUT = _env_int("SQUARE_TIMEOUT", 15)
    SQUARE_MAX_RETRIES = _env_int("SQUARE_MAX_RETRIES", 1)

    # --- dynamic polling -----------------------------------------------------
    POLL_INTERVAL_IDLE_SECONDS = _env_int("POLL_INTERVAL_IDLE_SECONDS", 1800)
    POLL_INTERVAL_UPCOMING_SECONDS = _env_int("POLL_INTERVAL_UPCOMING_SECONDS", 900)
    POLL_INTERVAL_NEAR_EVENT_SECONDS = _env_int("POLL_INTERVAL_NEAR_EVENT_SECONDS", 300)
    POLL_INTERVAL_COUNTDOWN_SECONDS = _env_int("POLL_INTERVAL_COUNTDOWN_SECONDS", 60)
    # jitter applied to the loop wait and between sources (avoid synchronized hits)
    POLL_JITTER_FRACTION = max(0.0, min(0.5, _env_int("POLL_JITTER_FRACTION", 10) / 100.0))
    SOURCE_STAGGER_MAX_SECONDS = _env_int("SOURCE_STAGGER_MAX_SECONDS", 3)

    # --- T-10 reminder -------------------------------------------------------
    REMINDER_MINUTES_BEFORE_START = _env_int("REMINDER_MINUTES_BEFORE_START", 10)
    # tick frequency of the in-process reminder scheduler (30-60s recommended,
    # independent of the poll interval)
    REMINDER_TICK_SECONDS = max(5, _env_int("REMINDER_TICK_SECONDS", 30))
    # A reminder is considered "missed" (not resent) if it is older than this.
    # Small grace absorbs tick latency and brief restarts; anything beyond it
    # is treated as an expired T-10 window per spec ("错过不补发").
    REMINDER_FIRE_GRACE_SECONDS = _env_int("REMINDER_FIRE_GRACE_SECONDS", 90)
    # how long after start_time an airdrop is considered over (then poll = idle)
    AIRDROP_WINDOW_HOURS = _env_int("AIRDROP_WINDOW_HOURS", 2)

    # --- PushPlus (WeChat) ----------------------------------------------------
    PUSHPLUS_ENABLED = _env_bool("PUSHPLUS_ENABLED", False)
    PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN", "").strip()
    PUSHPLUS_API_URL = os.getenv("PUSHPLUS_API_URL", "https://www.pushplus.plus/send")
    # retry delays in seconds: attempt at t=0, t=30, t=150  (3 attempts max)
    PUSHPLUS_RETRY_DELAYS = [0, 30, 120]

    # --- Feishu (Lark) custom bot webhook (Phase 3) ---------------------------
    FEISHU_ENABLED = _env_bool("FEISHU_ENABLED", False)
    FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    FEISHU_RETRY_DELAYS = [0, 30, 120]
    FEISHU_TIMEOUT = _env_int("FEISHU_TIMEOUT", 15)

    # --- storage / health ----------------------------------------------------
    DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "monitor.db"))
    HEALTH_PATH = os.getenv("HEALTH_PATH", str(BASE_DIR / "data" / "health.json"))

    HEALTH_FETCH_WARNING_MINUTES = _env_int("HEALTH_FETCH_WARNING_MINUTES", 10)
    HEALTH_FETCH_ERROR_MINUTES = _env_int("HEALTH_FETCH_ERROR_MINUTES", 30)
    MAX_CONSECUTIVE_FETCH_ERRORS = _env_int("MAX_CONSECUTIVE_FETCH_ERRORS", 10)

    # plain class attributes (NOT properties): true only when a channel is
    # enabled AND its credential/URL is present (and valid for Feishu)
    pushplus_ready = PUSHPLUS_ENABLED and bool(PUSHPLUS_TOKEN)
    feishu_ready = (
        FEISHU_ENABLED
        and bool(FEISHU_WEBHOOK_URL)
        and FEISHU_WEBHOOK_URL.startswith("https://open.feishu.cn/open-apis/bot/v2/hook/")
    )
