"""Feishu (Lark) custom bot webhook provider (Phase 3).

Sends a plain-text message to a Feishu group via a custom-bot webhook:

    https://open.feishu.cn/open-apis/bot/v2/hook/<token>

The pipeline keeps using NotificationProvider.send(title, content, url=None);
this provider is one of the fan-out channels assembled by
app.notifiers.base.get_provider(). Retries follow the same bounded policy as
PushPlus (max 3 attempts: t=0, t=30s, t=2min) and can never create a duplicate
notification row (dedup lives in the database, not here).

Payload rules (Feishu open API):
  * msg_type = "text"
  * content.text must stay below 4096 UTF-8 bytes -> conservative truncation
  * success response is {"code": 0, "msg": "success"}
"""

import json
import logging
import time
import urllib.error
import urllib.request

from app.notifiers.base import NotificationProvider

log = logging.getLogger("binance-alpha.feishu")

FEISHU_WEBHOOK_PREFIX = "https://open.feishu.cn/open-apis/bot/v2/hook/"
# Feishu caps the text field at 4096 UTF-8 bytes; stay safely under it.
_TEXT_BYTE_LIMIT = 4000
_DEFAULT_TIMEOUT = 15


def validate_webhook_url(url: str) -> bool:
    """A Feishu bot webhook must point at the official open-apis hook path."""
    if not url or not isinstance(url, str):
        return False
    return url.strip().startswith(FEISHU_WEBHOOK_PREFIX)


def truncate_utf8(text: str, limit: int = _TEXT_BYTE_LIMIT) -> str:
    """Truncate text so its UTF-8 encoding fits within `limit` bytes without
    cutting a multi-byte character in half."""
    if text is None:
        return ""
    if len(text.encode("utf-8")) <= limit:
        return text
    data = text.encode("utf-8")[:limit]
    # strip a trailing partial multi-byte sequence
    while data and data[-1] & 0xC0 == 0x80:
        data = data[:-1]
    return data.decode("utf-8", errors="ignore")


def build_text_payload(title: str, content: str) -> dict:
    """Assemble the Feishu custom-bot text envelope."""
    text = f"{title}\n{content}" if title else content
    return {"msg_type": "text", "content": {"text": truncate_utf8(text)}}


class FeishuBotWebhookProvider(NotificationProvider):
    name = "feishu_bot"
    dry_run = False

    def __init__(self, webhook_url: str,
                 retry_delays: list[int] | None = None,
                 timeout: int = _DEFAULT_TIMEOUT):
        self.webhook_url = webhook_url.strip()
        self.retry_delays = retry_delays or [0, 30, 120]
        self.timeout = timeout

    def _post_once(self, payload: dict) -> tuple[bool, str | None]:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.webhook_url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(body)
            if parsed.get("code") == 0:
                return True, None
            return False, f"feishu api error: {parsed.get('msg') or body[:200]}"
        except (urllib.error.URLError, urllib.error.HTTPError, OSError,
                ValueError) as exc:  # noqa: PERF203
            return False, f"feishu request failed: {exc}"

    def send(self, title: str, content: str, url: str | None = None) -> tuple[bool, str | None]:
        payload = build_text_payload(title, content)
        last_err: str | None = None
        for attempt, delay in enumerate(self.retry_delays, start=1):
            if delay:
                time.sleep(delay)
            ok, err = self._post_once(payload)
            if ok:
                return True, None
            last_err = err
            log.warning("feishu attempt %d/%d failed: %s",
                        attempt, len(self.retry_delays), err)
        return False, last_err
