"""Event engine (v2): matching -> extraction -> cross-source association.

Layers (spec §3):
  1. Source Message  : raw platform messages, deduped by (source, id).
  2. Canonical Event : one Alpha airdrop event, merged across official sources.
  3. Notification    : per canonical event (DISCOVERED / T-10 deduped).

Association rules (spec §3, §16, §26):
  * Known symbol  -> candidates are events keyed alpha:{SYM}:{date} (incl.
    suffixed keys). Reuse the compatible one; a *conflicting* start time on
    the same symbol+day creates a separate event (alpha:{SYM}:{date}:{n})
    instead of merging.
  * Unknown symbol -> every announcement gets its OWN unique key
    alpha:unknown:{date}:{hash} (never one merged alpha:unknown:date).
  * Claiming       -> a symbol-revealing message claims an unknown event only
    when it is unambiguous (single unknown on the date, or exactly one
    start-compatible unknown). Otherwise it stays pending (never force-merge).
  * Cross-channel  -> identical/similar unknown announcements on the same date
    merge by content similarity; symbol-known reposts merge by symbol+date.
  * Provenance     -> event_sources records every official source per event
    with per-channel first/last seen.
  * Notifications  -> DISCOVERED once per event; EVENT_UPDATED only for
    material changes (start/end/points/amount/rule/status); pure enrichment
    or cross-posting never notifies.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import timedelta

from app import db as db_mod
from app.extractor import extract_fields
from app.matcher import match_reason
from app.models import AlphaEvent, EventChanges, SourceMessage
from app.timeutil import display_date_key, utcnow

log = logging.getLogger("binance-alpha.engine")

EVENT_LIVE_HINT = "now live on binance alpha"
EVENT_PRE_HINT_1 = "first platform to feature"
EVENT_PRE_HINT_2 = "get ready to claim"

# two start times closer than this are "the same event"; beyond it they
# conflict (same symbol+day but a different airdrop window -> separate event)
START_CONFLICT_TOLERANCE = timedelta(hours=2)
# content similarity threshold for merging symbol-unknown announcements
SIMILARITY_THRESHOLD = 0.9

# fields whose change is a material update worth a WeChat EVENT_UPDATED
MATERIAL_FIELDS = {
    "start_time", "end_time", "points_threshold", "points_cost",
    "airdrop_amount", "claim_rule", "status",
}

_CANCEL_WORDS = ("cancel", "cancelled", "canceled", "取消")


@dataclass
class ProcessResult:
    matched: bool = False
    reason: dict | None = None
    event: AlphaEvent | None = None
    created: bool = False
    changes: EventChanges | None = None
    actions: list = field(default_factory=list)


def classify_event_type(normalized: str, existing: AlphaEvent | None,
                        fields=None) -> str:
    """Deterministic event-type classification (spec §12)."""
    if EVENT_LIVE_HINT in normalized:
        return "ALPHA_AIRDROP_LIVE"
    if existing is not None:
        return "ALPHA_AIRDROP_UPDATE"
    if EVENT_PRE_HINT_2 in normalized:
        return "ALPHA_AIRDROP_PRE_ALERT"
    if "will be the first platform to feature" in normalized:
        return "ALPHA_AIRDROP_PRE_ALERT"
    if EVENT_PRE_HINT_1 in normalized:
        has_detail = bool(
            (fields is not None) and (fields.start_time or fields.airdrop_amount)
        )
        return "ALPHA_AIRDROP_DETAIL" if has_detail else "ALPHA_AIRDROP_PRE_ALERT"
    return "ALPHA_AIRDROP_DETAIL"


def _diff_models(old: AlphaEvent | None, new: AlphaEvent) -> EventChanges:
    """Compare an event before/after an update to detect what changed."""
    changes = EventChanges()
    if old is None:
        return changes
    for name in (
        "project_name", "symbol", "start_time", "end_time",
        "points_threshold", "points_cost", "airdrop_amount", "claim_rule", "status",
    ):
        ov, nv = getattr(old, name), getattr(new, name)
        if ov != nv:
            changes.fields[name] = (ov, nv)
    changes.start_time_changed = "start_time" in changes.fields
    changes.material = any(name in MATERIAL_FIELDS for name in changes.fields)
    return changes


def event_date_key(fields, published_at) -> str:
    """Calendar date (display tz) that keys the event."""
    if fields.start_time:
        return display_date_key(fields.start_time)
    if fields.event_date:
        return display_date_key(fields.event_date)
    if published_at:
        return display_date_key(published_at)
    return display_date_key(utcnow())


def _unknown_key(date_key: str, msg) -> str:
    """Unique per-message key for a symbol-unknown announcement."""
    h = hashlib.sha1(f"{msg.source}:{msg.source_message_id}".encode()).hexdigest()[:8]
    return f"alpha:unknown:{date_key}:{h}"


def _start_compatible(event: AlphaEvent, fields) -> bool:
    """True when an event's known start does not conflict with the message."""
    if event.start_time and fields.start_time:
        if abs((event.start_time - fields.start_time).total_seconds()) \
                > START_CONFLICT_TOLERANCE.total_seconds():
            return False
    if event.project_name and fields.project_name:
        if event.project_name != fields.project_name:
            return False
    return True


def _tokens(text: str) -> set:
    return set(text.replace("\n", " ").split())


def _similar_text(a: str, b: str) -> bool:
    """Token-set Jaccard similarity for merging identical-ish announcements."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    inter = len(ta & tb)
    if inter == 0:
        return False
    return inter / len(ta | tb) >= SIMILARITY_THRESHOLD


def _is_cancellation(normalized: str) -> bool:
    low = normalized
    return any(w in low for w in _CANCEL_WORDS)


def _pick_claimable_unknown(db, date_key: str, fields):
    """Choose an alpha:unknown event to claim, or None when ambiguous."""
    unknowns = db.find_unknown_events_on_date(date_key)
    claimable = [u for u in unknowns if _start_compatible(u, fields)]
    if not claimable:
        return None
    if len(claimable) == 1:
        return claimable[0]
    # several candidates: keep pending unless the start time picks exactly one
    if fields.start_time:
        by_start = [
            u for u in claimable
            if u.start_time and abs(
                (u.start_time - fields.start_time).total_seconds()
            ) <= START_CONFLICT_TOLERANCE.total_seconds()
        ]
        if len(by_start) == 1:
            return by_start[0]
    return None  # ambiguous -> stay pending, do not force-merge


def _resolve_event(db, fields, date_key: str, msg):
    """Decide how a matched message attaches to the canonical event set.

    Returns dict: mode in ('create','update','claim'), event (model or None),
    key, created (bool), conflict_key (str|None).
    """
    if fields.symbol:
        sym = fields.symbol.upper()
        cands = db.find_events_by_symbol_on_date(sym, date_key)
        compat = [e for e in cands if _start_compatible(e, fields)]
        if compat:
            # prefer the most recently updated compatible event
            best = max(compat, key=lambda e: e.last_updated_at or utcnow())
            return {"mode": "update", "event": best, "key": best.event_key,
                    "created": False, "conflict_key": None}
        unk = _pick_claimable_unknown(db, date_key, fields)
        if unk is not None:
            key = f"alpha:{sym}:{date_key}"
            return {"mode": "claim", "event": unk, "key": key,
                    "created": False, "conflict_key": None}
        key = f"alpha:{sym}:{date_key}"
        if cands:  # same symbol+day exists but all conflict on start -> new event
            key = f"alpha:{sym}:{date_key}:{len(cands) + 1}"
        return {"mode": "create", "event": None, "key": key,
                "created": True, "conflict_key": None}

    # ---- symbol unknown ------------------------------------------------
    if fields.start_time:
        near = db.find_events_by_start_proximity(
            date_key, fields.start_time, int(START_CONFLICT_TOLERANCE.total_seconds()))
        # prefer unknown events (cross-channel pre-alert), then any event
        near.sort(key=lambda e: (e.symbol is not None, e.last_updated_at or utcnow()))
        if near:
            best = near[0]
            return {"mode": "update", "event": best, "key": best.event_key,
                    "created": False, "conflict_key": None}
    else:
        # no symbol, no start: merge only if an unknown announcement on the
        # same date is essentially the same text (cross-channel duplicate)
        for u in db.find_unknown_events_on_date(date_key):
            first_text = db.get_event_first_message_text(u.id)
            if first_text and _similar_text(msg.normalized_text, first_text):
                return {"mode": "update", "event": u, "key": u.event_key,
                        "created": False, "conflict_key": None}
    key = _unknown_key(date_key, msg)
    return {"mode": "create", "event": None, "key": key,
            "created": True, "conflict_key": None}


def process_message(db: "db_mod.Database", msg, notify: bool = True) -> ProcessResult:
    """Process a single (already persisted) message.

    Returns a ProcessResult describing what happened. When notify=True the
    engine also dispatches notifications/reminders through app.notifications.
    """
    reason = match_reason(msg.normalized_text)
    if not reason["matched"]:
        return ProcessResult(matched=False, reason=reason)

    from app import notifications, reminders

    fields = extract_fields(msg.raw_text, msg.published_at)
    date_key = event_date_key(fields, msg.published_at)
    resv = _resolve_event(db, fields, date_key, msg)
    result = ProcessResult(matched=True, reason=reason)
    model = resv["event"]

    if resv["mode"] == "create":
        etype = classify_event_type(msg.normalized_text, None, fields)
        event_id, model = db.create_event(resv["key"], fields, source=msg.source,
                                          source_url=msg.message_url)
        db.set_event_type(event_id, etype)
        model = db.get_event_by_id(event_id)
        result.created = True
    elif resv["mode"] == "claim":
        old = model
        # guard: the canonical key may already exist (race with another
        # message) -> fall back to updating that event instead
        target = db.get_event_by_key(resv["key"])
        if target is not None and target["id"] != old.id:
            model = db.event_row_to_model(target)
            etype = classify_event_type(msg.normalized_text, model, fields)
            db.update_event(model.id, fields, etype)
            model = db.get_event_by_id(model.id)
            result.changes = _diff_models(old, model)
        else:
            etype = classify_event_type(msg.normalized_text, model, fields)
            db.claim_unknown_event(model.id, resv["key"], fields, etype)
            model = db.get_event_by_id(model.id)
            result.changes = _diff_models(old, model)
    else:  # update
        old = model
        etype = classify_event_type(msg.normalized_text, model, fields)
        db.update_event(model.id, fields, etype)
        model = db.get_event_by_id(model.id)
        result.changes = _diff_models(old, model)

    result.event = model

    # provenance: record this official source against the canonical event
    db.upsert_event_source(model.id, msg.source, msg.source_message_id,
                           msg.message_url)

    # cancellation handling (material by definition)
    cancelled_now = _is_cancellation(msg.normalized_text)
    if cancelled_now and model.status != "CANCELLED":
        db.update_event_status(model.id, "CANCELLED")
        model = db.get_event_by_id(model.id)
        if result.changes is None:
            result.changes = EventChanges()
        result.changes.fields["status"] = (None, "CANCELLED")
        result.changes.material = True

    if notify:
        if result.created:
            started = bool(model.start_time) and utcnow() >= model.start_time
            title, content = notifications.build_discovered(model, started=started)
            notifications.send_notification(db, model, "DISCOVERED", title, content)
            result.actions.append("DISCOVERED")
        elif result.changes and result.changes.material:
            title, content = notifications.build_updated(model, result.changes)
            notifications.send_notification(db, model, "EVENT_UPDATED", title, content)
            result.actions.append("EVENT_UPDATED")

        if model.start_time:
            status = reminders.upsert_start_reminder(db, model)
            if status == "CREATED":
                result.actions.append("REMINDER_CREATED")
        else:
            db.cancel_pending_reminders(model.id, "START_MINUS_10_MINUTES")

    return result
