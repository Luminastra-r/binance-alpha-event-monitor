"""Telegram public channel Web source.

Fetches https://t.me/s/<channel> over plain HTTP GET (no browser, no MTProto,
no API key) and parses the public message widget. This is the v1 production
source. The preview page exposes the latest ~20 messages, which is sufficient
for a 30-minute idle poll cycle given Binance posts Alpha news hours in advance.
"""

import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser

from app.models import SourceMessage
from app.sources.base import SourceBlockedError, SourceParseError, SourceProvider
from app.timeutil import parse_iso

_MSG_TIME_RE = re.compile(r"(\d+)")


class _TelegramHTMLParser(HTMLParser):
    """Extracts (post_id, iso_time, text) tuples from a t.me/s message widget.

    The parser tracks the top-level message div (data-post attribute), the
    nested js-message_text div, and the <time datetime="..."> element.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.messages: list[tuple[str, str | None, str]] = []
        self._msg: dict | None = None
        self._msg_depth = 0
        self._text_depth = 0
        self._text_parts: list[str] = []

    # -- helpers ------------------------------------------------------------
    def _start_message(self, attrs: dict) -> None:
        self._msg = {
            "post": attrs["data-post"],
            "time_iso": None,
            "time_unix": attrs.get("data-datetime"),
        }
        self._msg_depth = 1
        self._text_depth = 0
        self._text_parts = []

    def _finish_message(self) -> None:
        text = "".join(self._text_parts).strip()
        self.messages.append((self._msg["post"], self._msg.get("time_iso"), text))
        self._msg = None

    # -- HTMLParser callbacks ----------------------------------------------
    def handle_starttag(self, tag: str, attrs) -> None:
        a = dict(attrs)
        if self._msg is None:
            if tag == "div":
                cls = set(a.get("class", "").split())
                if "tgme_widget_message" in cls and a.get("data-post"):
                    self._start_message(a)
            return

        if tag == "div":
            self._msg_depth += 1
            cls = set(a.get("class", "").split())
            if "js-message_text" in cls:
                self._text_depth = self._msg_depth
        elif tag == "time":
            if a.get("datetime"):
                self._msg["time_iso"] = a["datetime"]
        elif self._text_depth and tag in ("br", "p", "div"):
            self._text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._msg is None:
            return
        if tag == "div":
            if self._text_depth and self._msg_depth == self._text_depth:
                self._text_depth = 0
            self._msg_depth -= 1
            if self._msg_depth <= 0:
                self._finish_message()

    def handle_data(self, data: str) -> None:
        if self._msg is not None and self._text_depth:
            self._text_parts.append(data)


def _http_get(url: str, timeout: int, user_agent: str, max_retries: int) -> str:
    """Simple GET with bounded retries. Raises the last error on failure."""
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        if attempt:
            time.sleep(2 * attempt)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en,zh;q=0.9",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace")
        except (urllib.error.URLError, urllib.error.HTTPError,
                OSError, UnicodeDecodeError) as exc:  # noqa: PERF203
            last_exc = exc
    raise last_exc  # type: ignore[misc]


def parse_html_page(html: str, channel: str) -> list[SourceMessage]:
    """Parse the t.me/s/<channel> widget HTML into SourceMessage objects.

    Strict failure handling (P0 fix): if the response does not look like a
    Telegram channel page at all (structure changed / error page / redirect),
    raise SourceParseError. Callers must NOT treat that as "source OK, empty".
    A genuinely empty channel (widget markers present, zero text messages) is
    a valid empty result.
    """
    markers = ("tgme_widget_message", "tgme_page", "tgme_channel_info")
    if channel not in html and "t.me/s/" not in html:
        raise SourceParseError(
            f"t.me page for channel '{channel}' not recognized (structure "
            f"changed or unexpected response); refusing to report SOURCE_OK"
        )
    parser = _TelegramHTMLParser()
    try:
        parser.feed(html)
    except Exception as exc:  # noqa: BLE001
        raise SourceParseError(f"telegram widget HTML parse failed: {exc}") from exc

    if not parser.messages:
        if not any(mk in html for mk in markers):
            raise SourceParseError(
                f"t.me page for channel '{channel}' contains no Telegram "
                f"widget markers (blocked/error page); refusing SOURCE_OK"
            )
        return []  # valid page, channel currently has no text posts

    messages: list[SourceMessage] = []
    seen: set[str] = set()
    for post, time_iso, text in parser.messages:
        if not text.strip():
            continue
        try:
            post_id = post.rsplit("/", 1)[-1]
        except Exception:
            post_id = post
        if post_id in seen:
            continue
        seen.add(post_id)

        published_at = None
        if time_iso:
            published_at = parse_iso(time_iso)
        if published_at is None:
            unix = _MSG_TIME_RE.search(time_iso or "")
            if unix:
                published_at = datetime.fromtimestamp(
                    int(unix.group(1)), tz=timezone.utc)

        messages.append(
            SourceMessage(
                source="telegram_public_html",
                source_message_id=post_id,
                raw_text=text,
                published_at=published_at,
                message_url=f"https://t.me/{channel}/{post_id}",
            )
        )
    return messages


class TelegramPublicHtmlSource(SourceProvider):
    name = "telegram_public_html"
    official = True

    def __init__(self, channel: str, key: str = "telegram_wallet",
                 description: str = "", timeout: int = 15,
                 max_retries: int = 2, user_agent: str = ""):
        self.channel = channel
        self.key = key
        self.name = key
        self.description = description or f"Telegram public channel @{channel}"
        self.timeout = timeout
        self.max_retries = max_retries
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )

    def _url(self, before: str | None = None) -> str:
        base = f"https://t.me/s/{self.channel}"
        return f"{base}?before={before}" if before else base

    def fetch_page(self) -> list[SourceMessage]:
        html = _http_get(
            self._url(), self.timeout, self.user_agent, self.max_retries
        )
        messages = parse_html_page(html, self.channel)
        # re-tag with the per-source key so cross-channel dedup is correct
        for m in messages:
            m.source = self.key
            m.message_url = f"https://t.me/{self.channel}/{m.source_message_id}"
        return messages
