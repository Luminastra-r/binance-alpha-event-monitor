"""NotificationProvider abstraction (spec §19, Phase 3 multi-channel).

The business logic never talks to PushPlus / Feishu directly — it calls
NotificationProvider.send(title, content, url=None). get_provider() returns:

  * FanoutProvider over every ENABLED real channel (PushPlus, Feishu bot) —
    each channel is delivered independently, at least one success => ok,
    every channel failing => failure with per-channel errors;
  * DryRunProvider when no real channel is configured (or tests force it).

A WxPusher provider can be added later by implementing NotificationProvider
and registering it in get_provider() — the pipeline never changes.
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
    """Used when no real notification channel is configured. Logs the would-be
    message and reports success so the pipeline keeps running (spec §19)."""

    name = "dry_run"
    dry_run = True

    def send(self, title: str, content: str, url: str | None = None) -> tuple[bool, str | None]:
        log.info("[DRY-RUN] notification would be sent: %s\n%s", title, content)
        return True, None


class FanoutProvider(NotificationProvider):
    """Delivers a message to every enabled channel.

    Each channel is tried with its own bounded retries (inside the provider).
    The fan-out itself does not add retries, so a message can never be
    duplicated by this layer. One channel failing does not block the others.
    """

    name = "fanout"
    dry_run = False

    def __init__(self, channels: list[NotificationProvider]):
        self.channels = channels
        if not channels:
            raise ValueError("FanoutProvider requires at least one channel")

    @property
    def dry_run(self) -> bool:  # type: ignore[override]
        return all(c.dry_run for c in self.channels)

    def send(self, title: str, content: str, url: str | None = None) -> tuple[bool, str | None]:
        errors: list[str] = []
        delivered = 0
        for ch in self.channels:
            ok, err = ch.send(title, content, url)
            if ok:
                delivered += 1
            else:
                errors.append(f"{ch.name}: {err}")
        if delivered:
            if errors:
                log.warning("fanout: %d/%d channels ok, failures: %s",
                            delivered, len(self.channels), "; ".join(errors))
            return True, None
        return False, "; ".join(errors) if errors else "no channels"

    def __repr__(self) -> str:
        return f"<FanoutProvider channels={[c.name for c in self.channels]}>"


_provider: NotificationProvider | None = None

# Tests / replay set this to True so they never send real messages even when a
# valid channel is configured in the environment.
force_dry_run = False


def _build_channels() -> list[NotificationProvider]:
    """Assemble the enabled real channels from configuration."""
    from app.config import Config

    channels: list[NotificationProvider] = []

    if Config.pushplus_ready:
        from app.notifiers.pushplus import PushPlusWeChatProvider

        channels.append(PushPlusWeChatProvider(
            token=Config.PUSHPLUS_TOKEN,
            api_url=Config.PUSHPLUS_API_URL,
            retry_delays=Config.PUSHPLUS_RETRY_DELAYS,
        ))

    if Config.feishu_ready:
        from app.notifiers.feishu import FeishuBotWebhookProvider

        channels.append(FeishuBotWebhookProvider(
            webhook_url=Config.FEISHU_WEBHOOK_URL,
            retry_delays=Config.FEISHU_RETRY_DELAYS,
        ))
    return channels


def get_provider() -> NotificationProvider:
    global _provider
    if _provider is None:
        if force_dry_run:
            _provider = DryRunProvider()
            log.info("NotificationProvider = DryRunProvider (force_dry_run)")
            return _provider

        channels = _build_channels()
        if channels:
            _provider = FanoutProvider(channels)
            log.info("NotificationProvider = FanoutProvider (%s)",
                     ", ".join(c.name for c in channels))
        else:
            _provider = DryRunProvider()
            log.warning(
                "no notification channel configured (PUSHPLUS_TOKEN / "
                "FEISHU_WEBHOOK_URL missing) -> DRY-RUN mode."
            )
    return _provider


def reset_provider() -> None:
    """Clear the cached provider (used by tests)."""
    global _provider
    _provider = None
