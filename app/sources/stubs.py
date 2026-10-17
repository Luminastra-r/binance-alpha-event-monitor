"""Future source providers (stubs — intentionally NOT built).

The SourceProvider interface is in place; these are not part of the v1/v2
production allowlist:
  * TelegramMtprotoSource : future official MTProto client (full history).
  * ThirdPartySource      : never built; third-party feeds are excluded from
                            the official allowlist and must never trigger
                            WeChat notifications.
BinanceSquareSource is now implemented for real in app.sources.square.
"""

from app.sources.base import SourceProvider


class TelegramMtprotoSource(SourceProvider):
    """Future: official MTProto client (Telethon) for higher reliability and
    full message history. v2 still uses the public web preview."""

    key = "telegram_mtproto"
    name = "telegram_mtproto"
    official = True

    def fetch_page(self) -> list[SourceMessage]:
        raise NotImplementedError("TelegramMtprotoSource is a future candidate")


class ThirdPartySource(SourceProvider):
    """Explicitly excluded from the production allowlist. Third-party feeds
    may only be used for cross-validation by a human, never as a monitor."""

    key = "third_party"
    name = "third_party"
    official = False

    def fetch_page(self) -> list[SourceMessage]:
        raise NotImplementedError(
            "ThirdPartySource is excluded from the official allowlist"
        )
