"""Analytics screen 6.1 and periods 6.2: metrics of docs/calc.md, sections 4–5, laid out as api.md blocks.

Texts are plain placeholders: the fixtures do not compare them (README, «Правила сравнения»).
"""
from __future__ import annotations

import calendar
import datetime as dt
import re
from fractions import Fraction
from typing import Optional

from .ledger import Coverage, Ledger, stable_id
from .registry import BANK_FEES_NAME, OWN_TRANSFER, TRANSFERS_NAME, UNCATEGORIZED
from .util import add_months, day_of, end_of_day, iso, month_key, month_start, round_half_up, start_of_day, tdiv
from .views import Scope, selection_id

STALE_DAYS = 14
FORECAST_FROM_DAY = 10
REGULAR_TOLERANCE_PCT = 15
NOTABLE_MIN = 100_000
SMALL_MIN_COUNT = 8
SMALL_MAX_AVG = 50_000
MONTHS_IN = {"month": 1, "quarter": 3, "year": 12}
ROLLING_MONTHS = {"rolling_quarter": 3, "rolling_year": 12}
SINGLETONS = {"all_time", *ROLLING_MONTHS}
FEE_WORDS = {
    "комиссия", "комиссии", "комиссию", "комиссией", "комиссий", "комиссиям", "комиссиями", "комиссиях",
    "процент", "процента", "проценту", "процентом", "проценте", "проценты", "процентов", "процентам", "процентами",
    "процентах",
}
WORD = re.compile(r"[a-zа-яё]+")
EXCLUDED_IN_OBSERVATIONS = {OWN_TRANSFER}


def parse_supports(supports: Optional[str]):
    """Absent/empty means the v1 catalog; repeated types advertise their greatest maximum."""
    if not supports:
        return None
    out = {}
    for part in supports.split(","):
        kind, sep, raw = part.partition("@")
        digits = raw[1:] if raw.startswith("+") else raw
        if not kind or not sep or not digits.isascii() or not digits.isdecimal():
            raise ValueError("invalid supports")
        version = int(raw)
        if version < 1 or version > 2147483647:
            raise ValueError("invalid supports")
        out[kind] = max(out.get(kind, 0), version)
    return out


# ---- periods ----

def period_bounds(ptype: str, d: dt.date):
    """(start, end exclusive, key) of the period of `ptype` containing `d`."""
    if ptype == "month":
        s = month_start(d)
        return s, add_months(s, 1), month_key(s)
    if ptype == "quarter":
        s = dt.date(d.year, (d.month - 1) // 3 * 3 + 1, 1)
        return s, add_months(s, 3), f"{s.year:04d}-Q{(s.month - 1) // 3 + 1}"
    s = dt.date(d.year, 1, 1)
    return s, dt.date(d.year + 1, 1, 1), f"{s.year:04d}"


def rolling_bounds(ptype: str, last: dt.date):
    """Shift the exclusive end in calendar months, clamping the target day."""
    end = last + dt.timedelta(days=1)
    month = add_months(end, -ROLLING_MONTHS[ptype])
    start = month.replace(day=min(end.day, calendar.monthrange(month.year, month.month)[1]))
    return start, end, ptype


def parse_key(key: str) -> dt.date:
    if "-Q" in key:
        y, q = key.split("-Q")
        return dt.date(int(y), (int(q) - 1) * 3 + 1, 1)
    if "-" in key:
        y, m = key.split("-")
        return dt.date(int(y), int(m), 1)
    return dt.date(int(key), 1, 1)


class Analytics:
    def __init__(self, ledger: Ledger, now: dt.datetime):
        self.ledger = ledger
        self.zone = ledger.zone
        self.now = now
        self.today = day_of(now, self.zone)
        self.cov: Coverage = ledger.coverage(self.today)

    def at(self, d: dt.date) -> str:
        return iso(start_of_day(d, self.zone))

    def range(self, s: dt.date, e: dt.date) -> dict:
        return {"from": self.at(s), "to": self.at(e)}

    def stale(self) -> bool:
        return self.cov.empty or (self.today - self.cov.last).days > STALE_DAYS

    def data_to(self, start: dt.date, end: dt.date) -> Optional[str]:
        """Time of the last operation inside the period if it is on the last covered day, else 23:59:59 of that day."""
        b = self.cov.bounds(start, end)
        if b is None:
            return None
        last_day = b[1]
        ats = [o.at for o in self.ledger.ops.values() if start <= day_of(o.at, self.zone) < end]
        if ats and day_of(max(ats), self.zone) == last_day:
            return iso(max(ats).astimezone(self.zone))
        return iso(end_of_day(last_day, self.zone))

    def periods_with_data(self, ptype: str) -> list:
        if ptype in ROLLING_MONTHS:
            return [] if self.cov.empty else [rolling_bounds(ptype, self.cov.last)]
        if ptype == "all_time":
            return [] if self.cov.empty else [(self.cov.first, self.cov.last + dt.timedelta(days=1), "all_time")]
        keys = []
        for m in self.cov.months_with_data():
            k = period_bounds(ptype, m)
            if k not in keys:
                keys.append(k)
        return keys

    def coverage_of(self, ptype: str, start: dt.date, end: dt.date) -> str:
        if ptype in ROLLING_MONTHS:
            return "complete" if self.cov.count_days(start, end) == (end - start).days else "partial"
        if ptype == "all_time":
            return "partial" if self.cov.gaps_within(start, end) else "complete"
        return self.cov.coverage_of(start, end)

    def periods(self, ptype: str) -> dict:
        return {"periods": [{"key": k, "range": self.range(s, e), "coverage": self.coverage_of(ptype, s, e)}
                            for s, e, k in reversed(self.periods_with_data(ptype))]}

    # ---- 6.1 ----

    def screen(self, ptype: str, date: Optional[str], mode: str, supports: Optional[str] = None) -> dict:
        if not self.ledger.ops or self.cov.empty:
            return {"has_data": False}
        available = self.periods_with_data(ptype)
        if date and ptype not in SINGLETONS:
            start, end, key = period_bounds(ptype, parse_key(date))
        else:
            start, end, key = available[-1]
        keys = [k for _, _, k in available]
        idx = keys.index(key) if key in keys else None
        scope = Scope(self.ledger, mode)
        V = scope.visible()
        ops = scope.in_days(V, start, end)
        b = self.cov.bounds(start, end)
        period = {"range": self.range(start, end), "is_current": ptype not in SINGLETONS and start <= self.today < end,
                  "coverage": self.coverage_of(ptype, start, end),
                  "data_from": self.at(b[0]) if b else None, "data_to": self.data_to(start, end),
                  "gaps": [self.range(a, z) for a, z in self.cov.gaps_within(start, end)]}
        ctx = Ctx(self, ptype, start, end, key, mode, scope, V, ops, period)
        supported = parse_supports(supports)
        blocks = ctx.tiles_block(2 if supported is not None and supported.get("tile_group", 0) >= 2 else 1)
        chart = ctx.monthly_chart()
        blocks = [blocks] + ([chart] if chart else []) + [ctx.expense_categories(), ctx.income_categories()]
        if ptype == "month":
            blocks += [{"type": "heading", "v": 1, "data": {"text": "Регулярные платежи и заметные траты"}},
                       ctx.regular_payments(), *ctx.notable_spending(), ctx.bank_fees(), ctx.small_frequent()]
        if ptype != "year" and ptype not in SINGLETONS:
            blocks.append(ctx.balance(chart))
        if supported is not None:
            blocks = [block for block in blocks if block["v"] <= supported.get(block["type"], 0)]
        unread = sum(1 for _, line in self.ledger.open_unread()
                     if line.date and start_of_day(start, self.zone) <= line.date < start_of_day(end, self.zone))
        return {
            "has_data": True,
            "params": {"period": ptype, "date": key, "transfer_mode": mode},
            "currency": "RUB",
            "period": period,
            "navigation": {"previous": keys[idx - 1] if idx else None,
                           "next": keys[idx + 1] if idx is not None and idx + 1 < len(keys) else None},
            "blocks": blocks,
            "state": {
                "stale": self.stale(),
                "last_operation_at": iso(max(o.at for o in self.ledger.ops.values())),
                "data_from": self.at(self.cov.first),
                "data_to": self.at(self.cov.last + dt.timedelta(days=1)),
                "full_months": len(self.cov.full_months()),
                "unread_lines_count": unread,
                "calculated_at": iso(self.now),
                "recalculating": False,
            },
        }


def lock(reason: str, required: int = None, available: int = None, available_from: str = None) -> dict:
    d = {"reason": reason, "text": "Пока недоступно"}
    if required is not None:
        d["required"] = required
        d["available"] = available
    if available_from:
        d["available_from"] = available_from
    return d


def value(v: int) -> dict:
    return {"value": v, "value_kind": "amount"}


class Ctx:
    """One 6.1 request: period, mode and the visible operations."""

    def __init__(self, a: Analytics, ptype, start, end, key, mode, scope: Scope, V, ops, period):
        self.a, self.ptype, self.start, self.end, self.key, self.mode = a, ptype, start, end, key, mode
        self.scope, self.V, self.ops, self.period = scope, V, ops, period
        self.cov = a.cov
        self.today = a.today
        self.E = scope.expense(ops)
        self.I = scope.income(ops)
        self.bounds = self.cov.bounds(start, end)
        self.month = month_start(start)

    def filt(self, start=None, end=None, **kw) -> dict:
        d = {"from": self.a.at(start or self.start), "to": self.a.at(end or self.end), **kw, "transfer_mode": self.mode}
        return d

    def open(self, f: dict) -> dict:
        return {"type": "open_operations", "filter": f}

    # ---- tiles ----

    def tiles_block(self, version: int = 1) -> dict:
        if self.period["coverage"] == "no_data":
            return {"id": "tiles", "type": "tile_group", "v": version, "status": "none",
                    "empty_text": "За этот период данных нет", "alt_text": "За этот период данных нет",
                    "data": {"groups": []} if version == 2 else {"tiles": []}}
        tiles = [{"id": "balance", "title": "Доходы минус расходы", "status": "ready", **value(self.I - self.E),
                  "caption": "доля дохода"}]
        expense = {"id": "expense", "title": "Расходы", "status": "ready", **value(self.E), "caption": "период",
                   "action": self.open(self.filt(kind="expense"))}
        tiles.append(expense)
        n = max(1, self.cov.count_days(self.start, min(self.end, self.today + dt.timedelta(days=1))))
        tiles.append({"id": "daily_expense", "title": "Расходы в день", "status": "ready", **value(tdiv(self.E, n)),
                      "caption": "сравнение"})
        tiles.append({"id": "income", "title": "Доходы", "status": "ready", **value(self.I), "show_plus": True,
                      "action": self.open(self.filt(kind="income"))})
        if self.period["is_current"] and not self.a.stale():
            tiles.append(self.forecast())
        data = {"tiles": tiles}
        if version == 2:
            by_id = {tile["id"]: tile for tile in tiles}
            by_id["expense"]["title"] = "Расходы за период"
            by_id["income"]["title"] = "Доходы за период"
            by_id["daily_expense"]["title"] = "Средние расходы в день"
            by_id["daily_expense"]["caption"] = "По дням с данными не позже сегодня; пропуски не учитываются"
            data = {"groups": [{"tiles": [by_id[key] for key in ("expense", "income", "balance")]},
                               {"title": "Дополнительные показатели", "tiles": [by_id[key] for key in
                                ("daily_expense", "forecast") if key in by_id]}]}
        return {"id": "tiles", "type": "tile_group", "v": version, "status": "ready", "alt_text": "плитки",
                "data": data}

    def forecast(self) -> dict:
        t = {"id": "forecast", "title": "Прогноз расходов"}
        D = self.bounds[1] if self.bounds else None
        if self.ptype == "month":
            if self.today.day < FORECAST_FROM_DAY:
                return {**t, "status": "locked", "lock": lock(
                    "too_early_in_month", available_from=self.a.at(self.start.replace(day=FORECAST_FROM_DAY)))}
            last = min(D, self.today) if D else self.today
            T = (last - self.start).days + 1
            L = (self.end - self.start).days
            return {**t, "status": "ready", **value(tdiv(self.E * L, T)), "approx": True, "caption": "к концу периода"}
        required = MONTHS_IN[self.ptype]
        full = sorted(self.cov.full_months(), reverse=True)
        if len(full) < required:
            locked = lock("need_full_months", required, len(full))
            locked["text"] = self.forecast_basis_text(required, full)
            return {**t, "status": "locked", "lock": locked}
        base = full[:required]
        e_base = sum(self.scope.expense(self.scope.in_days(self.V, m, add_months(m, 1))) for m in base)
        d_base = sum((add_months(m, 1) - m).days for m in base)
        rem_from = D + dt.timedelta(days=1) if D else self.start
        R = max(0, (self.end - rem_from).days)
        return {**t, "status": "ready", **value(self.E + round_half_up(Fraction(e_base * R, d_base))), "approx": True,
                "caption": "к концу периода"}

    def forecast_basis_text(self, required: int, full: list) -> str:
        """Explain union coverage without inferring continuity from its endpoints."""
        names = ("январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь")

        def month(m):
            return f"{names[m.month - 1]} {m.year}"

        def ranges(months):
            groups = []
            for m in sorted(months):
                if groups and m == add_months(groups[-1][1], 1):
                    groups[-1] = (groups[-1][0], m)
                else:
                    groups.append((m, m))
            return ", ".join(month(a) if a == b else month(a) + " — " + month(b) for a, b in groups)

        text = (f"Нужно {required} полных месяцев, есть {len(full)}. "
                "База прогноза — вся загруженная история, независимо от выбранного года. "
                "Учитывается завершённый календарный месяц, покрытый выписками по всем дням. "
                "Учтены полные месяцы: " + (ranges(full) or "нет") + ".")
        incomplete, missing = [], []
        if self.cov.empty:
            return text
        m = month_start(self.cov.first)
        while m <= month_start(self.cov.last):
            end = add_months(m, 1)
            bounds = self.cov.bounds(m, end)
            if bounds is None:
                missing.append(m)
            elif m not in full:
                parts = []
                if end > self.today:
                    parts.append("месяц ещё не завершён")
                parts.append(f"данные {bounds[0].isoformat()}–{bounds[1].isoformat()}")
                gaps = self.cov.gaps_within(m, end)
                if gaps:
                    parts.append("пропуски " + ", ".join(
                        f"{max(a, m).isoformat()}–{(min(b, end) - dt.timedelta(days=1)).isoformat()}" for a, b in gaps))
                incomplete.append(month(m) + " (" + "; ".join(parts) + ")")
            m = end
        if incomplete:
            text += " Неполные месяцы: " + ", ".join(incomplete) + "."
        if missing:
            text += " Месяцы без данных: " + ranges(missing) + "."
        return text

    # ---- monthly chart ----

    def chart_months(self) -> list:
        if self.ptype in SINGLETONS:
            out, month = [], month_start(self.start)
            while month < self.end:
                out.append(month)
                month = add_months(month, 1)
            return out
        if self.ptype == "month":
            return [add_months(self.month, -i) for i in range(5, -1, -1)]
        return [add_months(self.start, i) for i in range(MONTHS_IN[self.ptype])]

    def highlight(self) -> Optional[int]:
        return {"month": 5, "quarter": 2}.get(self.ptype)

    def point(self, m: dt.date, val) -> dict:
        e = add_months(m, 1)
        cov = self.cov.coverage_of(m, e)
        p = {"range": self.a.range(m, e), "value": val, "coverage": cov}
        if cov == "partial":
            b = self.cov.bounds(m, e)
            p["data_from"] = self.a.at(b[0])
            p["data_to"] = self.a.data_to(m, e)
            p["note"] = "неполный"
        return p

    def rolling_point(self, m: dt.date, start: dt.date, end: dt.date, val) -> dict:
        bounds = self.cov.bounds(start, end)
        cov = "no_data" if bounds is None else (
            "complete" if start == m and end == add_months(m, 1) and self.cov.full_month(m) else "partial")
        point = {"range": self.a.range(start, end), "value": val, "coverage": cov}
        if cov == "partial":
            point.update(data_from=self.a.at(bounds[0]), data_to=self.a.data_to(start, end), note="неполный")
        return point

    def full_previous(self) -> list:
        """Full previous periods of the same type, newest first, starting not before the first covered month."""
        out, s = [], self.start
        first = month_start(self.cov.first)
        while True:
            s = add_months(s, -MONTHS_IN[self.ptype])
            if s < first:
                return out
            e = add_months(s, MONTHS_IN[self.ptype])
            if self.cov.full(s, e):
                out.append(s)

    def chart_notes(self, points: list) -> list:
        """Rows under «Расходы по месяцам» (docs/calc.md, section 5): month — the comparison «к тому же дню» (the
        previous month is full) and «обычно» (2 full previous months); quarter — the peak month and «обычно» (2 full
        previous quarters); year — the peak month and the total by months. Texts are placeholders."""
        notes = []
        has_peak = any(p["value"] is not None for p in points)
        if self.ptype in SINGLETONS:
            return [{"text": "пиковый месяц", "tone": "primary"}] if has_peak else []
        prev = add_months(self.start, -MONTHS_IN[self.ptype])
        if self.ptype == "month":
            if self.cov.full(prev, self.start):
                notes.append({"text": "сравнение", "tone": "primary"})
        elif has_peak:
            notes.append({"text": "пиковый месяц", "tone": "primary"})
        if self.ptype == "year":
            if has_peak:
                notes.append({"text": "итог по месяцам", "tone": "muted"})
        elif len(self.full_previous()) >= 2:
            notes.append({"text": "обычно", "tone": "muted"})
        return notes

    def monthly_chart(self) -> Optional[dict]:
        with_data = self.cov.months_with_data()
        if self.ptype not in SINGLETONS and len(with_data) == 1 and not self.cov.full_month(with_data[0]):
            return None  # early state: no block (spec section 4)
        base = {"id": "monthly_chart", "type": "chart", "v": 1, "title": "Расходы по месяцам", "alt_text": "график"}
        if self.ptype not in SINGLETONS and len(with_data) < 2:
            return {**base, "status": "locked", "lock": lock("need_months_with_data", 2, len(with_data))}
        points = []
        for m in self.chart_months():
            e = add_months(m, 1)
            s, z = (max(m, self.start), min(e, self.end)) if self.ptype in ROLLING_MONTHS else (m, e)
            bounds = self.cov.bounds(s, z)
            has = bounds is not None
            val = self.scope.expense(self.scope.in_days(self.V, s, z)) if has else None
            point = self.rolling_point(m, s, z, val) if self.ptype in ROLLING_MONTHS else self.point(m, val)
            if has:
                point["action"] = {"type": "select_analytics_period", "period": "month", "date": month_key(m)}
            points.append(point)
        data = {"kind": "bar", "value_kind": "amount", "points": points, "notes": self.chart_notes(points)}
        if self.highlight() is not None:
            data["highlight_index"] = self.highlight()
        return {**base, "status": "ready", "data": data}

    def balance(self, chart: Optional[dict]) -> dict:
        points = []
        if chart and chart.get("status") == "ready":
            for m in self.chart_months():
                e = add_months(m, 1)
                ms = self.scope.in_days(self.V, m, e)
                inc, exp = self.scope.income(ms), self.scope.expense(ms)
                val = (inc - exp) / inc if self.cov.has_data_month(m) and inc > 0 else None
                points.append(self.point(m, val))
        data = {"kind": "bar", "value_kind": "percent", "points": points, "show_values": True,
                "summary": value(self.I - self.E), "caption": "пояснение"}
        if points and self.highlight() is not None:
            data["highlight_index"] = self.highlight()
        return {"id": "balance", "type": "chart", "v": 1, "status": "ready", "title": "Доходы минус расходы",
                "alt_text": "разница", "data": data}

    # ---- categories ----

    def expense_categories(self) -> dict:
        lines = self.scope.expense_lines(self.ops)
        base = {"id": "expense_categories", "type": "ranked_list", "v": 1, "title": "Расходы по категориям",
                "alt_text": "категории"}
        if not lines:
            return {**base, "status": "none", "empty_text": "Расходов нет"}
        groups = {}
        for _, cat, amount in lines:
            g = groups.setdefault(cat.id, [cat, 0, 0])
            g[1] += amount
            g[2] += 1
        items = []
        for cat, total, count in sorted(groups.values(), key=lambda g: (-g[1], g[0].name)):
            it = {"id": cat.id, "icon": cat.icon, "title": cat.name, **value(total),
                  "action": self.open(self.filt(category_id=cat.id, kind="expense"))}
            if self.E > 0 and total >= 0:
                it["share"] = total / self.E
            if cat.name == TRANSFERS_NAME:
                it["note"] = f"{count} переводов"
            items.append(it)
        return {**base, "status": "ready", "data": {
            "total": {"label": "Всего", **value(self.E), "action": self.open(self.filt(kind="expense"))},
            "items": items, "visible_count": 4, "more_label": "Все категории"}}

    def income_categories(self) -> dict:
        incomes = self.scope.incomes(self.ops)
        base = {"id": "income_categories", "type": "ranked_list", "v": 1, "title": "Доходы", "alt_text": "доходы"}
        if not incomes:
            return {**base, "status": "none", "empty_text": "Доходов нет"}
        groups = {}
        for o in incomes:
            g = groups.setdefault(o.category.id, [o.category, 0])
            g[1] += o.amount
        items = []
        for cat, total in sorted(groups.values(), key=lambda g: (-g[1], g[0].name)):
            it = {"id": cat.id, "icon": cat.icon, "title": cat.name, **value(total), "show_plus": True,
                  "action": self.open(self.filt(category_id=cat.id, kind="income"))}
            if self.I > 0:
                it["share"] = total / self.I
            items.append(it)
        return {**base, "status": "ready", "data": {"items": items, "style": "plain"}}

    # ---- observations (month only) ----

    def full_up_to(self) -> list:
        """B: full months up to the selected one inclusive, newest first."""
        return sorted((m for m in self.cov.full_months() if m <= self.month), reverse=True)

    def card(self, cid: str, status: str, data: dict, **extra) -> dict:
        b = {"id": cid, "type": "insight_card", "v": 1, "status": status, "alt_text": "карточка", "data": data}
        b.update(extra)
        return b

    @staticmethod
    def observed(o) -> bool:
        """Common exclusions of observations: «Переводы» and «Между своими счетами» (by target for a refund)."""
        cat = o.target if o.is_refund else o.category
        return cat.name != TRANSFERS_NAME and cat.id not in EXCLUDED_IN_OBSERVATIONS

    def regular_payments(self) -> dict:
        data = {"icon": "repeat", "title": "Подписки и регулярные платежи"}
        B = self.full_up_to()
        if len(B) < 2:
            return self.card("regular_payments", "locked", data, lock=lock("need_full_months", 2, len(B)))
        W = B[:3]
        groups = {}
        for m in W:
            for o in self.scope.in_days(self.V, m, add_months(m, 1)):
                if o.is_debit and o.name and self.observed(o) and not o.is_own:
                    groups.setdefault(o.name, {}).setdefault(m, []).append(o)
        payments = []
        for name, by_month in groups.items():
            if len(by_month) != len(W) or any(len(v) != 1 for v in by_month.values()):
                continue
            amounts = sorted(v[0].amount for v in by_month.values())
            med = amounts[len(amounts) // 2]
            if any(100 * abs(a - med) > REGULAR_TOLERANCE_PCT * med for a in amounts):
                continue
            if name in self.a.ledger.dismissed:
                continue
            payments.append((name, med))
        if not payments:
            return self.card("regular_payments", "none", data, empty_text="Регулярных платежей не нашли")
        payments.sort(key=lambda p: (-p[1], p[0]))
        names = {p[0] for p in payments}
        all_sel = selection_id("regular_all", self.key, self.mode)
        self.a.ledger.selections[all_sel] = lambda o, names=names: o.is_debit and o.name in names
        rows = [{"title": n, "sublabel": "число", **value(med),
                 "action": self.open(self.filt(selection=selection_id("regular", n), selection_name=n)),
                 "button": {"type": "dismiss_regular_payment", "payment_id": stable_id("payment", n),
                            "label": "Не подписка"}} for n, med in payments[:3]]
        data.update({**value(sum(p[1] for p in payments)), "description": "в месяц", "items": rows})
        if len(payments) > 3:
            data["items_more"] = {"label": f"Ещё {len(payments) - 3}", **value(sum(p[1] for p in payments[3:]))}
        if len(B) == 2:
            data["badge"] = "предварительно"
        return self.card("regular_payments", "tentative" if len(B) == 2 else "ready", data,
                         action=self.open(self.filt(selection=all_sel, selection_name="Регулярные платежи")))

    def notable_spending(self) -> list:
        empty = {"icon": "bar-chart", "title": "Заметные траты"}
        before = sorted((m for m in self.cov.full_months() if m < self.month), reverse=True)
        if len(before) < 3:
            return [self.card("notable_spending", "locked", empty, lock=lock("need_full_months", 3, len(before)))]
        base = before[:3]

        def by_category(ops):
            sums = {}
            for o, cat, amount in self.scope.expense_lines(ops):
                if cat.id == UNCATEGORIZED or cat.name == TRANSFERS_NAME or cat.id == OWN_TRANSFER:
                    continue
                g = sums.setdefault(cat.id, [cat, 0])
                g[1] += amount
            return sums

        typical = {cid: (g[0], round_half_up(Fraction(g[1], 3))) for cid, g in by_category(
            [o for m in base for o in self.scope.in_days(self.V, m, add_months(m, 1))]).items()}
        found = []
        for cid, (cat, amount) in by_category(self.ops).items():
            typ = typical.get(cid, (cat, 0))[1]
            if typ > 0 and amount >= NOTABLE_MIN and 2 * amount >= 3 * typ:
                found.append((amount - typ, cat, amount))
        if not found:
            return [self.card("notable_spending", "none", empty, empty_text="Заметных трат не нашли")]
        found.sort(key=lambda f: (-f[0], f[1].name))
        return [self.card("notable_spending", "ready",
                          {"icon": cat.icon, "title": cat.name, **value(amount), "description": "обычно"},
                          action=self.open(self.filt(category_id=cat.id, kind="expense")))
                for _, cat, amount in found]

    @staticmethod
    def is_fee(o, cat) -> bool:
        return cat.name == BANK_FEES_NAME or any(w in FEE_WORDS for w in WORD.findall(o.name.lower().replace("ё", "е")))

    def bank_fees(self) -> dict:
        data = {"icon": "percent", "title": "Комиссии и проценты банку"}
        lines = [(o, a) for o, cat, a in self.scope.expense_lines(self.ops) if self.is_fee(o, cat)]
        total = sum(a for _, a in lines)
        if not lines or total <= 0:
            return self.card("bank_fees", "none", data, empty_text="Комиссий нет")
        data.update({**value(total), "description": "из чего"})
        return self.card("bank_fees", "ready", data, action=self.open(self.filt(
            selection=selection_id("bank_fees"), selection_name="Комиссии и проценты банку")))

    def small_frequent(self) -> dict:
        data = {"icon": "coffee", "title": "Мелкие частые траты"}
        B = self.full_up_to()
        if not B:
            return self.card("small_frequent", "locked", data, lock=lock("need_full_months", 1, 0))
        M = B[0]
        ms, me = M, add_months(M, 1)
        debits = [o for o in self.scope.in_days(self.V, ms, me) if o.is_debit and not o.is_own and self.observed(o)]
        groups, used = [], set()

        def take(key_of, title_of, candidates):
            buckets = {}
            for o in candidates:
                k = key_of(o)
                if k is not None:
                    buckets.setdefault(k, []).append(o)
            for k, lst in buckets.items():
                total = sum(o.amount for o in lst)
                if len(lst) > SMALL_MIN_COUNT and tdiv(total, len(lst)) <= SMALL_MAX_AVG:
                    groups.append((title_of(k, lst), len(lst), total, k))
                    used.update(o.key for o in lst)

        take(lambda o: ("merchant", o.name) if o.name else None, lambda k, l: k[1], debits)
        merchant_used = set(used)
        take(lambda o: ("category", o.category.id) if o.category.id != UNCATEGORIZED else None,
             lambda k, l: l[0].category.name, [o for o in debits if o.key not in used])
        if not groups:
            return self.card("small_frequent", "none", data, empty_text="Мелких частых трат не нашли")
        groups.sort(key=lambda g: (-g[2], g[0]))

        def group_filter(g):
            sel = selection_id("small", *g[3], month_key(M), self.mode)
            kind, key = g[3]
            observed = self.observed

            def test(o, kind=kind, key=key):
                if not o.is_debit or o.is_own or not observed(o):
                    return False
                return o.name == key if kind == "merchant" else (o.category.id == key and o.key not in merchant_used)
            self.a.ledger.selections[sel] = test
            return self.filt(ms, me, selection=sel, selection_name=g[0])

        data.update({**value(sum(g[2] for g in groups)), "approx": True, "description": "первая группа",
                     "items": [{"title": g[0], "sublabel": "раз в месяц", **value(g[2]), "value_suffix": "в месяц",
                                "action": self.open(group_filter(g))} for g in groups[1:]]})
        return self.card("small_frequent", "ready", data, action=self.open(group_filter(groups[0])))
