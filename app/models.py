"""Lightweight dataclasses for the domain objects."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SourceMessage:
    source: str
    source_message_id: str
    raw_text: str
    normalized_text: str = ""
    published_at: datetime | None = None
    message_url: str = ""

    def __post_init__(self) -> None:
        if not self.normalized_text:
            from app.normalize import normalize_text

            self.normalized_text = normalize_text(self.raw_text)


@dataclass
class AlphaEvent:
    id: int = 0
    event_key: str = ""
    project_name: str | None = None
    symbol: str | None = None
    event_type: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    points_threshold: int | None = None
    points_cost: int | None = None
    airdrop_amount: int | None = None
    claim_rule: str | None = None
    source: str = ""
    source_url: str = ""
    first_seen_at: datetime | None = None
    last_updated_at: datetime | None = None
    status: str = "UPCOMING"


@dataclass
class ExtractedFields:
    project_name: str | None = None
    symbol: str | None = None
    start_time: datetime | None = None
    start_source: str | None = None
    event_date: datetime | None = None  # calendar date mentioned without a time
    end_time: datetime | None = None
    points_threshold: int | None = None
    points_cost: int | None = None
    airdrop_amount: int | None = None
    claim_rule: str | None = None


@dataclass
class EventChanges:
    """What changed during an event update."""

    fields: dict = field(default_factory=dict)  # name -> (old, new)
    start_time_changed: bool = False
    # True when at least one MATERIAL field changed (start/end/points/amount/
    # rule/status) — only then a WeChat EVENT_UPDATED is justified
    material: bool = False

    def any(self) -> bool:
        return bool(self.fields)
