"""Simulated operational clock and business-calendar helpers.

The agent never reads your computer's date. `init` stores the scenario's clock
in the database; commands read it from there and `clock --advance-hours` moves it.
That keeps every example, test and report reproducible years from now.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WEEKDAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december"]


def parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError(f"Timestamp must include a UTC offset: {value!r}")
    return dt


def parse_date(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def fmt_day(d: date) -> str:
    return f"{WEEKDAY_ABBR[d.weekday()]} {d.strftime('%b')} {d.day}"


class BusinessCalendar:
    def __init__(self, policy: dict[str, Any]):
        self.tz = ZoneInfo(policy.get("timezone", "America/New_York"))
        days = policy.get("working_days", ["Mon", "Tue", "Wed", "Thu", "Fri"])
        self.working_weekdays = {WEEKDAY_ABBR.index(d) for d in days}
        self.holidays = {date.fromisoformat(h) for h in policy.get("holidays", [])}
        hours = policy.get("working_hours", {"start": "09:00", "end": "17:30"})
        self.start = time.fromisoformat(hours["start"])
        self.end = time.fromisoformat(hours["end"])

    def is_workday(self, d: date) -> bool:
        return d.weekday() in self.working_weekdays and d not in self.holidays

    def next_workday(self, d: date) -> date:
        d = d + timedelta(days=1)
        while not self.is_workday(d):
            d += timedelta(days=1)
        return d

    def on_or_next_workday(self, d: date) -> date:
        while not self.is_workday(d):
            d += timedelta(days=1)
        return d

    def add_workdays(self, start: date, n: int) -> date:
        """Finish date of a task that starts on `start` (a workday) and takes `n` workdays (n>=1)."""
        d = self.on_or_next_workday(start)
        for _ in range(max(n, 1) - 1):
            d = self.next_workday(d)
        return d

    def workdays_between(self, a: date, b: date) -> int:
        """Signed number of workdays from a to b (exclusive of a, inclusive of b)."""
        if a == b:
            return 0
        sign = 1 if b > a else -1
        lo, hi = (a, b) if b > a else (b, a)
        count, d = 0, lo
        while d < hi:
            d += timedelta(days=1)
            if self.is_workday(d):
                count += 1
        return sign * count

    def is_working_time(self, dt: datetime) -> bool:
        local = dt.astimezone(self.tz)
        return self.is_workday(local.date()) and self.start <= local.time() < self.end

    def next_working_time(self, dt: datetime) -> datetime:
        local = dt.astimezone(self.tz)
        if self.is_working_time(local):
            return local
        d = local.date()
        if self.is_workday(d) and local.time() < self.start:
            return datetime.combine(d, self.start, self.tz)
        return datetime.combine(self.next_workday(d), self.start, self.tz)

    def add_working_hours(self, dt: datetime, hours: float) -> datetime:
        """Move forward by `hours` of working time (used for follow-up spacing)."""
        remaining = timedelta(hours=hours)
        cur = self.next_working_time(dt)
        while remaining > timedelta(0):
            end_today = datetime.combine(cur.date(), self.end, self.tz)
            available = end_today - cur
            if remaining <= available:
                return cur + remaining
            remaining -= available
            cur = datetime.combine(self.next_workday(cur.date()), self.start, self.tz)
        return cur


_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTH_DAY = re.compile(r"\b(" + "|".join(m[:3] for m in MONTHS) + r")[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.I)
_DATE_NUMERIC = re.compile(r"(?<![\d-])(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
_ORDINAL = re.compile(r"\bthe\s+(\d{1,2})(?:st|nd|rd|th)\b", re.I)
_WEEKDAY = re.compile(r"\b(next\s+)?(" + "|".join(WEEKDAYS) + r")\b", re.I)
_RELATIVE = re.compile(r"\b(today|tomorrow|end of (?:the )?day|eod)\b", re.I)
_COMMIT_WORDS = re.compile(r"(ready|have|finish|finished|done|deliver|by|on|until|planning on|expect|land|complete)\W*(?:\w+\W+){0,3}$", re.I)
_NEGATION = re.compile(r"\bnot\s*$", re.I)


def find_date_mentions(text: str, reference: datetime) -> list[dict[str, Any]]:
    """Every concrete date mentioned in `text`, resolved relative to `reference`.

    More specific forms win when they overlap or sit next to each other
    ("Monday 3/16" -> 3/16, "Wednesday the 18th" -> the 18th). Ranges such as
    "next week" are never resolved: choosing a day would invent a commitment.
    """
    ref = reference.date()
    found: list[dict[str, Any]] = []

    def add(m: re.Match[str], d: date | None, rank: int) -> None:
        if d is not None:
            found.append({"start": m.start(), "end": m.end(), "date": d, "text": m[0].strip(), "rank": rank})

    for m in _ISO.finditer(text):
        add(m, _safe_date(int(m[1]), int(m[2]), int(m[3])), 0)
    for m in _MONTH_DAY.finditer(text):
        month = [x[:3] for x in MONTHS].index(m[1][:3].lower()) + 1
        add(m, _roll_year(ref, month, int(m[2])), 1)
    for m in _DATE_NUMERIC.finditer(text):
        if m[3]:
            year = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
            add(m, _safe_date(year, int(m[1]), int(m[2])), 2)
        else:
            add(m, _roll_year(ref, int(m[1]), int(m[2])), 2)
    for m in _ORDINAL.finditer(text):
        day = int(m[1])
        month = ref.month if day >= ref.day else ref.month % 12 + 1
        add(m, _roll_year(ref, month, day), 3)
    for m in _WEEKDAY.finditer(text):
        target = WEEKDAYS.index(m[2].lower())
        delta = (target - ref.weekday()) % 7 or 7
        if m[1] and (ref + timedelta(days=delta)).isocalendar()[1] == ref.isocalendar()[1]:
            delta += 7
        add(m, ref + timedelta(days=delta), 4)
    for m in _RELATIVE.finditer(text):
        add(m, ref + timedelta(days=1) if m[1].lower() == "tomorrow" else ref, 5)

    found.sort(key=lambda f: (f["rank"], f["start"]))
    kept: list[dict[str, Any]] = []
    for f in found:
        clash = any(f["start"] < k["end"] + 6 and k["start"] < f["end"] + 6 for k in kept)
        if not clash:
            kept.append(f)
    kept.sort(key=lambda f: f["start"])
    for f in kept:
        before = text[: f["start"]]
        f["negated"] = bool(_NEGATION.search(before[-8:]))
        f["commitment_cue"] = bool(_COMMIT_WORDS.search(before[-40:]))
        f["date"] = f["date"].isoformat()
    return kept


def resolve_date_expression(text: str, reference: datetime) -> tuple[date | None, str | None]:
    """Pick the date most likely to be the commitment: not negated, preferring one after a
    commitment cue ("ready Monday", "planning on the 18th"), else the last mention."""
    mentions = [m for m in find_date_mentions(text, reference) if not m["negated"]]
    if not mentions:
        return None, None
    cued = [m for m in mentions if m["commitment_cue"]]
    best = (cued or mentions)[-1]
    return date.fromisoformat(best["date"]), best["text"]


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _roll_year(ref: date, month: int, day: int) -> date | None:
    d = _safe_date(ref.year, month, day)
    if d and (ref - d).days > 180:
        d = _safe_date(ref.year + 1, month, day)
    return d


def mentions_unresolvable_range(text: str) -> bool:
    return bool(re.search(r"\bnext week\b|\bend of (the )?(week|month)\b|\bsoon\b|\bfew days\b|\blater\b", text, re.I))
