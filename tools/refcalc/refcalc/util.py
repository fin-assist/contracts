"""Time, rounding and formatting helpers shared by the reference calculation."""
from __future__ import annotations

import datetime as dt
from fractions import Fraction
from zoneinfo import ZoneInfo


def tdiv(a: int, b: int) -> int:
    """Integer division truncating toward zero, like `/` in Go and Kotlin (docs/calc.md, section 1)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


def round_half_up(x: Fraction) -> int:
    """floor(x + 0.5) for any sign, like Math.round (docs/calc.md, section 1)."""
    v = x + Fraction(1, 2)
    return v.numerator // v.denominator


def iso(t: dt.datetime) -> str:
    """RFC 3339 with seconds and the offset of `t` (`2026-09-25T21:40:00+03:00`)."""
    if t.utcoffset() is not None and t.utcoffset().total_seconds() % 60:
        t = t.astimezone(dt.timezone.utc)
    return t.replace(microsecond=0).isoformat()


def start_of_day(d: dt.date, zone: ZoneInfo) -> dt.datetime:
    """Earliest real instant of the civil day, or the next existing day when skipped."""
    midnight = dt.datetime(d.year, d.month, d.day)
    # PEP 495: fold=0/1 use the offsets on either side of a gap/fold.
    # https://peps.python.org/pep-0495/#mind-the-gap
    candidates = sorted({midnight.replace(tzinfo=zone, fold=f).astimezone(dt.timezone.utc)
                         for f in (0, 1)})
    valid = [instant for instant in candidates
             if instant.astimezone(zone).replace(tzinfo=None) == midnight]
    if valid:
        return min(valid).astimezone(zone)
    # A UTC round-trip alone advances by the entire gap, which can overshoot its end.
    # The two synthetic instants bracket the transition; find its first real instant.
    lo, hi = candidates
    tick = dt.timedelta(microseconds=1)
    while hi - lo > tick:
        mid = lo + (hi - lo) // 2
        if mid.astimezone(zone).date() < d:
            lo = mid
        else:
            hi = mid
    return hi.astimezone(zone)


def end_of_day(d: dt.date, zone: ZoneInfo) -> dt.datetime:
    """23:59:59 of the day in the zone (docs/calc.md, section 4)."""
    return dt.datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=zone)


def day_of(t: dt.datetime, zone: ZoneInfo) -> dt.date:
    return t.astimezone(zone).date()


def month_start(d: dt.date) -> dt.date:
    return d.replace(day=1)


def add_months(d: dt.date, n: int) -> dt.date:
    m = d.month - 1 + n
    return dt.date(d.year + m // 12, m % 12 + 1, 1)


def month_key(d: dt.date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def days_between(a: dt.date, b: dt.date) -> int:
    return (b - a).days


def parse_iso(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slug(name: str) -> str:
    """`cat_<транслит>` slug of a bank category (docs/calc.md, 3.1)."""
    out = []
    for c in name.lower():
        if c.isascii() and c.isalnum():
            out.append(c)
        elif c in TRANSLIT:
            out.append(TRANSLIT[c])
        else:
            out.append("_")
    s = "".join(out).strip("_")
    while "__" in s:
        s = s.replace("__", "_")
    return s or "unknown"
