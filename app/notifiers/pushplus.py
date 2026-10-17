"""PushPlus WeChat provider (spec §20-21, §27).

POSTs to the PushPlus HTTP API with the user's token. Retries a maximum of 3
times (t=0, t=30s, t=2min) and then gives up — no infinite retry. Retries are
bounded inside a single send call so they can never create a duplicate
notification row in our own database (dedup is enforced separately by the
(event_id, notification_type) constraint).
"""

import json
import logging
import time
import urllib.error
import urllib.request

from app.notifiers.base import NotificationProvider

log = logging.getLogger("binance-alpha.pushplus")

_DEFAULT_API = "https://www.pushplus.plus/send"


class PushPlusWeChatProvider(NotificationProvider):
    name = "pushplus"
    dry_run = False

    def __init__(self, token: str, api_url: str = _DEFAULT_API,
                 retry_delays: list[int] | None = None):
        self.token = token
        self.api_url = api_url
        self.retry_delays = retry_delays or [0, 30, 120]

    def _post_once(self, payload: dict) -> tuple[bool, str | None]:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.api_url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(body)
            code = parsed.get("code")
            if code == 200:
                return True, None
            return False, f"pushplus api error: {parsed.get('msg') or body[:200]}"
        except (urllib.error.URLError, urllib.error.HTTPError, OSError,
                ValueError) as exc:  # noqa: PERF203
            return False, f"pushplus request failed: {exc}"

    def send(self, title: str, content: str, url: str | None = None) -> tuple[bool, str | None]:
        # html template renders line breaks as <br>
        payload = {
            "token": self.token,
            "title": title[:100],
            "content": content.replace("\n", "<br>"),
            "template": "html",
        }
        last_err: str | None = None
        for attempt, delay in enumerate(self.retry_delays, start=1):
            if delay:
                time.sleep(delay)
            ok, err = self._post_once(payload)
            if ok:
                return True, None
            last_err = err
            log.warning("pushplus attempt %d/%d failed: %s",
                        attempt, len(self.retry_delays), err)
        return False, last_err
