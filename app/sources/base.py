"""SourceProvider abstraction and the official-source factory.

v2: explicit allowlist — only first-party official Binance sources are ever
built for production. Third-party aggregators exist only as stubs and are
never constructed (their messages must never trigger a WeChat notification).

Every source has a stable `key` (telegram_wallet / binance_square /
telegram_announcements) used as the message `source` column, the per-source
checkpoint, and per-source health records.
"""

from abc import ABC, abstractmethod

from app.models import SourceMessage


class SourceError(Exception):
    """Base class for source-layer failures."""


class SourceBlockedError(SourceError):
    """The source exists but cannot be reached with an allowed lightweight
    method (no browser automation, no login, no third-party API service).
    Recorded as per-source status BLOCKED — never reported as a generic
    SOURCE_ERROR, and never faked as PASS."""


class SourceParseError(SourceError):
    """The upstream page/response was fetched but its structure changed or is
    unexpected. The caller must NOT treat this as "source OK, no messages"."""


class SourceProvider(ABC):
    key: str = "base"
    name: str = "base"
    official: bool = False
    description: str = ""

    @abstractmethod
    def fetch_page(self) -> list[SourceMessage]:
        """Fetch the latest page of messages (newest-first is allowed; the
        pipeline sorts by publication time before processing)."""
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<{type(self).__name__} key={self.key} official={self.official}>"


def build_wallet_telegram_source():
    from app.config import Config

    if Config.TELEGRAM_FIXTURE:
        from app.sources.telegram_text import TelegramTextPageSource

        return TelegramTextPageSource(
            fixture_path=Config.TELEGRAM_FIXTURE,
            channel=Config.TELEGRAM_CHANNEL,
            key="telegram_wallet",
        )
    if not Config.TELEGRAM_SOURCE_ENABLED:
        raise RuntimeError("TELEGRAM_SOURCE_ENABLED=false and no TELEGRAM_FIXTURE set")
    from app.sources.telegram_html import TelegramPublicHtmlSource

    return TelegramPublicHtmlSource(
        channel=Config.TELEGRAM_CHANNEL,
        key="telegram_wallet",
        description="Binance Wallet official Telegram channel (public web preview)",
        timeout=Config.TELEGRAM_FETCH_TIMEOUT,
        max_retries=Config.TELEGRAM_MAX_RETRIES,
        user_agent=Config.TELEGRAM_USER_AGENT,
    )


def build_announcements_telegram_source():
    from app.config import Config

    if Config.TELEGRAM_ANNOUNCEMENTS_FIXTURE:
        from app.sources.telegram_text import TelegramTextPageSource

        return TelegramTextPageSource(
            fixture_path=Config.TELEGRAM_ANNOUNCEMENTS_FIXTURE,
            channel=Config.TELEGRAM_ANNOUNCEMENTS_CHANNEL,
            key="telegram_announcements",
        )
    from app.sources.telegram_html import TelegramPublicHtmlSource

    return TelegramPublicHtmlSource(
        channel=Config.TELEGRAM_ANNOUNCEMENTS_CHANNEL,
        key="telegram_announcements",
        description=("Binance official English announcements Telegram "
                     "(supplementary: only Alpha airdrop/Alpha Box posts are matched)"),
        timeout=Config.TELEGRAM_FETCH_TIMEOUT,
        max_retries=Config.TELEGRAM_MAX_RETRIES,
        user_agent=Config.TELEGRAM_USER_AGENT,
    )


def build_square_source():
    from app.config import Config
    from app.sources.square import BinanceSquareSource

    extra_headers = {}
    if Config.SQUARE_EXTRA_HEADERS:
        import json as _json

        try:
            extra_headers = _json.loads(Config.SQUARE_EXTRA_HEADERS)
        except ValueError:
            extra_headers = {}
    return BinanceSquareSource(
        profile=Config.SQUARE_PROFILE,
        api_base=Config.SQUARE_API_BASE,
        mode=Config.SQUARE_MODE,
        fixture_path=Config.SQUARE_FIXTURE or None,
        extra_headers=extra_headers,
        timeout=Config.SQUARE_TIMEOUT,
        max_retries=Config.SQUARE_MAX_RETRIES,
    )


def build_sources() -> list[SourceProvider]:
    """Build the enabled official sources (explicit allowlist)."""
    from app.config import Config

    builders = {
        "telegram_wallet": build_wallet_telegram_source,
        "binance_square": build_square_source,
        "telegram_announcements": build_announcements_telegram_source,
    }
    sources: list[SourceProvider] = []
    for key in Config.SOURCES_ALLOWLIST:
        builder = builders.get(key)
        if builder is None:
            # unknown keys in the allowlist are ignored (defensive; the list is
            # explicit and only official keys are recognized)
            continue
        sources.append(builder())
    if not sources:
        raise RuntimeError("no enabled sources (check SOURCES_ALLOWLIST)")
    return sources


def build_source() -> SourceProvider:
    """Compatibility alias: the primary (wallet Telegram) source."""
    return build_wallet_telegram_source()
