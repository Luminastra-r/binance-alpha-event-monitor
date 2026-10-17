"""NotificationProvider abstraction (spec §19).

The business logic never talks to PushPlus directly — it calls
NotificationProvider.send(title, content, url=None). v1 ships with
PushPlusWeChatProvider; a WxPusher provider can be added later without
touching the pipeline.
"""

import logging
from abc import ABC, abstractmethod

log = logging.getLogger("binance-alpha.notify")


class NotificationProvider(ABC):
    name: str = "base"
    dry_run: bool = False

    @abstractmethod
    def send(self, title: str, content: str, url: str | None = None) -> tuple[bool, str | None]:
        """Send a notification. Returns (ok, error_message)."""
        raise NotImplementedError


class DryRunProvider(NotificationProvider):
    """Used when no PushPlus token is configured. Logs the would-be message
    and reports success so the pipeline keeps running (spec §19)."""

    name = "dry_run"
    dry_run = True

    def send(self, title: str, content: str, url: str | None = None) -> tuple[bool, str | None]:
        log.info("[DRY-RUN] notification would be sent: %s\n%s", title, content)
        return True, None


_provider: NotificationProvider | None = None

# Tests / replay set this to True so they never send real WeChat messages
# even when a valid PUSHPLUS_TOKEN is configured in the environment.
force_dry_run = False


def get_provider() -> NotificationProvider:
    global _provider
    if _provider is None:
        from app.config import Config

        if not force_dry_run and Config.pushplus_ready:
            from app.notifiers.pushplus import PushPlusWeChatProvider

            _provider = PushPlusWeChatProvider(
                token=Config.PUSHPLUS_TOKEN,
                api_url=Config.PUSHPLUS_API_URL,
                retry_delays=Config.PUSHPLUS_RETRY_DELAYS,
            )
            log.info("NotificationProvider = PushPlusWeChatProvider (enabled)")
        else:
            _provider = DryRunProvider()
            log.warning(
                "PUSHPLUS_TOKEN missing -> DRY-RUN mode. Set PUSHPLUS_ENABLED=true "
                "and PUSHPLUS_TOKEN=<token> to enable real WeChat delivery."
            )
    return _provider


def reset_provider() -> None:
    """Clear the cached provider (used by tests)."""
    global _provider
    _provider = None
