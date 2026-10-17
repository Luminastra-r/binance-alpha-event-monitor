"""Deterministic field extraction from Binance Alpha announcement messages.

Extracts project name / symbol / start time / points threshold / points cost /
airdrop amount / claim rule from raw message text. Unknown fields stay None —
we never guess, and never fill values from search results or common sense.

Time rules:
  * all internal times are aware UTC datetimes
  * explicit "(UTC)" / "(UTC+8)" offsets are honored
  * a bare date/time with no offset is assumed to be Asia/Shanghai (per spec)
  * "today/tomorrow at HH:MM (UTC)" is resolved against the message's own
    publication date (display timezone calendar day)
"""

import re
from datetime import datetime, timedelta, timezone

from app.models import ExtractedFields
from app.timeutil import DISPLAY_TZ, display_tz, utcnow

# ---------------------------------------------------------------------------
# month / tz tables
# ---------------------------------------------------------------------------

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

_TZ_OFFSETS = {
    "utc": 0, "gmt": 0, "cet": 1, "cest": 2, "bst": 1, "eet": 2, "eest": 3,
    "edt": -4, "est": -5, "cdt": -5, "cst": -6, "mdt": -6, "mst": -7,
    "pdt": -7, "pst": -8, "hkt": 8, "sgt": 8, "jst": 9, "kst": 9, "ist": 5.5,
    "aest": 10, "aedt": 11, "nzst": 12, "nzdt": 13, "wib": 7, "wita": 8, "wit": 9,
}

# Symbols that are almost certainly timezone/unit markers, never tokens
_SYMBOL_STOPLIST = set(_TZ_OFFSETS.keys()) | {
    "am", "pm", "et", "pt", "ct", "mt", "utc+8", "gmt+8",
    "usdt", "usdc", "busd", "fdusd", "tusd", "dai", "bnb", "btc", "eth", "sol",
}

_TZ_NAME_RE = "|".join(sorted(_TZ_OFFSETS, key=len, reverse=True))

# ---------------------------------------------------------------------------
# patterns
# ---------------------------------------------------------------------------

# "October 16, 2026, at 08:00 (UTC)" / "Oct 16 2026 08:00 UTC" / "October 16 at 08:00"
_R_MONTH_DATETIME = re.compile(
    r"\b(?P<mon>[A-Za-z]{3,9})\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s+"
    r"(?:(?P<year>\d{4}),?\s+)?(?:at\s+)?"
    r"(?P<h>\d{1,2}):(?P<m>\d{2})(?:\s*(?P<ampm>am|pm))?"
    r"(?:\s*(?:\(?(?:UTC|GMT)\s*(?P<off>[+-]\d{1,2})(?::?\d{2})?\)?|\(?(?P<tz>{tz})\)?))?"
    .replace("{tz}", _TZ_NAME_RE),
    re.IGNORECASE,
)

# "2026-10-16 08:00" / "2026/10/16 08:00" / "2026-10-16T08:00"
_R_NUM_DATETIME = re.compile(
    r"\b(?P<year>\d{4})[-/](?P<mon>\d{1,2})[-/](?P<day>\d{1,2})"
    r"[T\s]+(?P<h>\d{1,2}):(?P<m>\d{2})(?::\d{2})?"
    r"(?:\s*(?:\(?(?:UTC|GMT)\s*(?P<off>[+-]\d{1,2})(?::?\d{2})?\)?|\(?(?P<tz>{tz})\)?|Z))?"
    .replace("{tz}", _TZ_NAME_RE),
    re.IGNORECASE,
)

# "today at 10:00 (UTC)" / "tomorrow at 16:00 (UTC+8)"
_R_RELATIVE_TIME = re.compile(
    r"\b(?P<rel>today|tomorrow)\b[^.\n]{0,40}?(?:at\s+)?"
    r"(?P<h>\d{1,2}):(?P<m>\d{2})(?:\s*(?P<ampm>am|pm))?"
    r"(?:\s*(?:\(?(?:UTC|GMT)\s*(?P<off>[+-]\d{1,2})(?::?\d{2})?\)?|\(?(?P<tz>{tz})\)?))?"
    .replace("{tz}", _TZ_NAME_RE),
    re.IGNORECASE,
)

# "at 08:00 (UTC)" with no date -> anchored to the message's publication day.
# The timezone part is optional: a bare time defaults to Asia/Shanghai.
_R_BARE_TIME = re.compile(
    r"(?:at\s+)(?P<h>\d{1,2}):(?P<m>\d{2})(?:\s*(?P<ampm>am|pm))?"
    r"(?:\s*(?:\(?(?:UTC|GMT)\s*(?P<off>[+-]\d{1,2})(?::?\d{2})?\)?|\(?(?P<tz>{tz})\)?))?"
    .replace("{tz}", _TZ_NAME_RE),
    re.IGNORECASE,
)

# Chinese: "10月16日 16:00（北京时间）"
_R_CN_DATETIME = re.compile(
    r"(?P<mon>\d{1,2})月(?P<day>\d{1,2})日[,\s]*(?P<h>\d{1,2}):(?P<m>\d{2})"
    r"(?:\s*(?:\(?(?:UTC|GMT|北京时间)\s*(?P<off>[+-]\d{1,2})?\)?))?"
)

# month + day, no time (used as event calendar date when start time unknown)
# e.g. "on October 16." / "October 16, 2026"
_R_DATE_ONLY = re.compile(
    r"\b([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.I
)

# "NeoSoul (DNA)" / "Derive (DRV)" — the capitalized name immediately before
# the parenthesized ticker (avoids grabbing whole sentences as the project)
_R_SYMBOL = re.compile(
    r"\b([A-Z][A-Za-z0-9.'\-]+(?:\s+[A-Z][A-Za-z0-9.'\-]+)?)\s*\(([A-Z0-9]{2,12})\)"
)

# "at least 245 Binance Alpha Points" / "with 245+ Alpha Points"
_R_POINTS_THRESHOLD = re.compile(
    r"(?:at least|minimum|min\.?|users with|with|≥|>=|>)\s*"
    r"(\d{2,5})\+?\s*(?:binance\s+)?alpha\s+points"
)

# "claiming the airdrop will consume 15 Binance Alpha Points"
_R_COST_CONSUME = re.compile(
    r"consume\s+(\d{1,6})\s*(?:binance\s+)?alpha\s+points"
)
_R_COST_COST = re.compile(
    r"costs?\s+(\d{1,6})\s*(?:alpha\s+)?points?"
)
_R_COST_PER = re.compile(
    r"(\d{1,6})\s*(?:binance\s+)?alpha\s+points\s+(?:per|each|to\s+claim)"
)
_R_COST_CN = re.compile(r"消耗积分[:：]?\s*(\d{1,6})")

# "an airdrop of 400 DNA tokens" / "airdrop of 70 DRV tokens"
_R_AIRDROP_AMOUNT = re.compile(
    r"airdrop\s+of\s+(\d{1,6})\s*([A-Za-z0-9]{2,12})(?:\s*tokens?)?"
)
_R_AIRDROP_AMOUNT_CN = re.compile(r"空投[数量]*[:：]?\s*(\d{1,6})")

# claim rule
_R_CLAIM_FCFS = re.compile(r"first[-\s]?come,?\s*[-\s]?first[-\s]?served", re.I)
_R_CLAIM_CN = re.compile(r"先到先得")


def _apply_offset(h: int, m: int, ampm: str | None,
                  off: str | None, tz: str | None) -> tuple[int, int, float]:
    """Normalize to 24h and return (hour, minute, utc_offset_hours)."""
    if ampm:
        ampm = ampm.lower()
        if ampm == "pm" and h < 12:
            h += 12
        if ampm == "am" and h == 12:
            h = 0
    if off:
        offset_h = float(off)
    elif tz:
        offset_h = _TZ_OFFSETS.get(tz.lower(), 8.0)
    else:
        # no offset given -> assume the display timezone (Asia/Shanghai)
        offset_h = 8.0
    return h, m, offset_h


def _build_dt(year: int, mon: int, day: int, h: int, m: int,
              ampm: str | None, off: str | None, tz: str | None,
              published_at: datetime | None) -> datetime | None:
    h, m, offset_h = _apply_offset(h, m, ampm, off, tz)
    if year is None:
        year = utcnow().year
    try:
        naive = datetime(year, mon, day, h, m)
    except ValueError:
        return None
    aware = naive.replace(tzinfo=timezone(timedelta(hours=offset_h))).astimezone(timezone.utc)
    # sanity: a parsed start should not be absurdly far from the message date
    if published_at is not None and abs((aware - published_at).days) > 60:
        return None
    return aware


def _extract_start_time(raw: str, published_at: datetime | None) -> tuple[datetime | None, str | None]:
    text = raw  # keep original case for month names

    for rx, name in ((_R_MONTH_DATETIME, "month-datetime"),
                     (_R_NUM_DATETIME, "numeric-datetime"),
                     (_R_CN_DATETIME, "cn-datetime")):
        mo = rx.search(text)
        if mo:
            g = mo.groupdict()
            mon_s = g["mon"]
            if mon_s.isdigit():
                mon = int(mon_s)
            else:
                mon = _MONTHS.get(mon_s.lower())
            if mon is None:
                continue
            dt = _build_dt(
                int(g["year"]) if g.get("year") else None,
                mon, int(g["day"]),
                int(g["h"]), int(g["m"]), g.get("ampm"),
                g.get("off"), g.get("tz"), published_at,
            )
            if dt:
                return dt, name

    mo = _R_RELATIVE_TIME.search(text)
    if mo:
        g = mo.groupdict()
        base = (published_at or utcnow()).astimezone(display_tz())
        if g["rel"].lower() == "tomorrow":
            base = base + timedelta(days=1)
        dt = _build_dt(base.year, base.month, base.day,
                       int(g["h"]), int(g["m"]), g.get("ampm"),
                       g.get("off"), g.get("tz"), published_at)
        if dt:
            return dt, "relative-day"

    mo = _R_BARE_TIME.search(text)
    if mo:
        g = mo.groupdict()
        base = (published_at or utcnow()).astimezone(display_tz())
        dt = _build_dt(base.year, base.month, base.day,
                       int(g["h"]), int(g["m"]), g.get("ampm"),
                       g.get("off"), g.get("tz"), published_at)
        if dt:
            return dt, "bare-time"

    return None, None


def _extract_event_date(raw: str) -> datetime | None:
    """A month+day mention (no time) — used as the event's calendar date when the
    start time itself is not yet known (e.g. 'on October 16')."""
    mo = _R_DATE_ONLY.search(raw)
    if not mo:
        return None
    mon = _MONTHS.get(mo.group(1).lower())
    if not mon:
        return None
    try:
        year = utcnow().year
        return datetime(year, mon, int(mo.group(2)), 0, 0, tzinfo=display_tz())
    except ValueError:
        return None


def _extract_symbol(raw: str) -> tuple[str | None, str | None]:
    for mo in _R_SYMBOL.finditer(raw):
        project = mo.group(1).strip()
        sym = mo.group(2)
        if sym.lower() in _SYMBOL_STOPLIST:
            continue
        # the parenthesized text must look like a ticker
        if not re.fullmatch(r"[A-Z0-9]{2,8}", sym):
            continue
        # project part must contain at least one letter (reject "10:00" etc.)
        if not re.search(r"[A-Za-z]", project):
            continue
        # avoid matching a parenthetical right after a time/date fragment
        if re.search(r"\d{1,2}:\d{2}\s*$", project):
            continue
        return project, sym.upper()
    return None, None


def _extract_claim_rule(raw: str) -> str | None:
    if _R_CLAIM_FCFS.search(raw):
        return "First come, first served"
    if _R_CLAIM_CN.search(raw):
        return "先到先得"
    return None


def extract_fields(raw: str, published_at: datetime | None) -> ExtractedFields:
    f = ExtractedFields()

    f.project_name, f.symbol = _extract_symbol(raw)

    f.start_time, f.start_source = _extract_start_time(raw, published_at)
    if f.start_time is None:
        f.event_date = _extract_event_date(raw)

    norm_lower = raw.lower()

    mo = _R_POINTS_THRESHOLD.search(norm_lower)
    if mo:
        f.points_threshold = int(mo.group(1))

    for rx in (_R_COST_CONSUME, _R_COST_COST, _R_COST_PER, _R_COST_CN):
        mo = rx.search(norm_lower)
        if mo:
            f.points_cost = int(mo.group(1))
            break

    mo = _R_AIRDROP_AMOUNT.search(norm_lower)
    if mo:
        f.airdrop_amount = int(mo.group(1))
    else:
        mo = _R_AIRDROP_AMOUNT_CN.search(norm_lower)
        if mo:
            f.airdrop_amount = int(mo.group(1))

    f.claim_rule = _extract_claim_rule(raw)
    return f
