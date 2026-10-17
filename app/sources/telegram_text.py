"""Text-fixture source.

Used ONLY for sandbox/offline verification and tests (when TELEGRAM_FIXTURE is
set). The fixture content is copied verbatim from the official Binance Wallet
Telegram channel page (t.me/s/binance_wallet_announcements) fetched through the
platform fetch tool, because the sandbox network cannot reach t.me directly.
The production server uses TelegramPublicHtmlSource against the live channel.

Fixture format (see tests/fixtures/*.txt):

    ---MSG
    ts=2026-10-14T08:01:00Z
    <verbatim message text, possibly multi-line>
    ---MSG
    ts=...
    <text>
"""

import hashlib
import re
from pathlib import Path

from app.models import SourceMessage
from app.sources.base import SourceProvider
from app.timeutil import parse_iso

_MSG_SPLIT = re.compile(r"^---MSG\s*$", re.MULTILINE)


def parse_text_fixture(text: str, channel: str, source_key: str = "telegram_wallet") -> list[SourceMessage]:
    messages: list[SourceMessage] = []
    blocks = _MSG_SPLIT.split(text)
    for block in blocks:
        block = block.strip("\n")
        if not block:
            continue
        lines = block.split("\n")
        # skip comment/header blocks
        if lines[0].lstrip().startswith("#"):
            continue
        published_at = None
        if lines and lines[0].startswith("ts="):
            published_at = parse_iso(lines[0][3:].strip())
            lines = lines[1:]
        body = "\n".join(lines).strip()
        if not body:
            continue
        msg_id = "fx_" + hashlib.sha1(body.encode("utf-8")).hexdigest()[:12]
        messages.append(
            SourceMessage(
                source=source_key,
                source_message_id=msg_id,
                raw_text=body,
                published_at=published_at,
                message_url=f"https://t.me/{channel}",
            )
        )
    return messages


class TelegramTextPageSource(SourceProvider):
    name = "telegram_text_fixture"
    official = True
    description = "Telegram channel (fixture, sandbox verification only)"

    def __init__(self, fixture_path: str, channel: str,
                 key: str = "telegram_wallet"):
        self.fixture_path = fixture_path
        self.channel = channel
        self.key = key
        self.name = key

    def fetch_page(self) -> list[SourceMessage]:
        text = Path(self.fixture_path).read_text(encoding="utf-8")
        return parse_text_fixture(text, self.channel, self.key)
