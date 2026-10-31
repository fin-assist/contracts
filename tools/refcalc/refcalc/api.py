"""Responses of the client API (api.md 3.3, 4.1, 4.3, 5.1, 6.1, 6.2) computed by the reference calculation."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from typing import Optional

from .analytics import Analytics, parse_supports, period_bounds
from .ledger import Ledger, stable_id
from .registry import OWN_TRANSFER, REFUND, TRANSFERS_NAME, UNCATEGORIZED, Registry
from .util import add_months, day_of, end_of_day, iso, month_key, month_start, parse_iso, start_of_day
from .views import Scope, account, details, item, records, selection_id

MAX_SEARCH = 10_000
FEATURES = [  # (code, name, threshold kind, required) — spec section 7
    ("comparison", "Сравнение с прошлым месяцем", "full", 1),
    ("monthly_chart", "Расходы по месяцам", "with_data", 2),
    ("typical", "Обычные расходы", "full", 2),
    ("regular_payments", "Регулярные платежи", "full", 2),
    ("notable_spending", "Заметные траты", "full", 3),
    ("small_frequent", "Мелкие частые траты", "full", 1),
    ("year_forecast", "Прогноз на год", "full", 12),
]


class ApiError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status, self.code = status, code


class Backend:
    def __init__(self, registry: Registry, zone):
        self.reg = registry
        self.zone = zone
        self.ledger = Ledger(registry, zone)
        self.now: dt.datetime = None

    @property
    def today(self) -> dt.date:
        return day_of(self.now, self.zone)

    def at(self, d: dt.date) -> str:
        return iso(start_of_day(d, self.zone))

    # ---- 3.2 / 3.3 ----

    def upload(self, file_name: str, text: str) -> dict:
        before = self.ledger.coverage(self.today)
        is_first = not self.ledger.uploads
        up, added, duplicates = self.ledger.add_upload(file_name, text, self.now)
        after = self.ledger.coverage(self.today)
        doc = up.doc
        scope = Scope(self.ledger, "with")
        new_out_of_pairs = [o for o in added if not o.pair]
        uncategorized = [o for o in added if o.category.id == UNCATEGORIZED or (o.is_refund and o.target.id == UNCATEGORIZED)]
        ats = [t.posted_at for t in doc.transactions]
        full_after, full_before = after.full_months(), before.full_months()
        with_after, with_before = after.months_with_data(), before.months_with_data()

        def feature(code, name, kind, required):
            have_after = len(full_after) if kind == "full" else len(with_after)
            have_before = len(full_before) if kind == "full" else len(with_before)
            f = {"feature": code, "feature_name": name, "open": have_after >= required,
                 "opened_now": have_after >= required > have_before}
            if not f["open"]:
                f["required_full_months" if kind == "full" else "required_months_with_data"] = required
                f["text"] = f"{name} — нужно {required}, есть {have_after}"
            return f

        incomplete = []
        for m in with_after:
            if after.full_month(m):
                continue
            e = add_months(m, 1)
            b = after.bounds(m, e)
            a = Analytics(self.ledger, self.now)
            incomplete.append({"month": month_key(m), "range": {"from": self.at(m), "to": self.at(e)},
                               "data_from": self.at(b[0]), "data_to": a.data_to(m, e),
                               "gaps": [{"from": self.at(x), "to": self.at(y)} for x, y in after.gaps_within(m, e)],
                               "in_progress": not (e - dt.timedelta(days=1) < self.today)})
        notices = []
        if not doc.transactions:
            notices.append({"code": "empty_statement", "text": "В файле нет операций"})
        elif not any(t.is_debit for t in doc.transactions):
            notices.append({"code": "no_expenses", "text": "В файле нет расходов"})
        result = {
            "operation_count": len(doc.transactions),
            "new_count": len(added),
            "duplicate_count": duplicates,
            "unread_count": len(doc.unread),
            "first_operation_at": iso(min(ats)) if ats else None,
            "last_operation_at": iso(max(ats)) if ats else None,
            "is_first_import": is_first,
            "totals": None if not added else {
                "scope": "all" if is_first else "new",
                "expense": scope.expense(new_out_of_pairs), "income": scope.income(new_out_of_pairs)},
            "currency": "RUB",
            "accounts": [{**account(a), "mask": a.mask, "is_other_bank": a.is_other_bank} for a in doc.accounts],
            "categorized_count": len(added) - len(uncategorized),
            "own_transfer_count": sum(1 for o in added if o.pair or o.is_own),
            "uncategorized_count": len(uncategorized),
            "uncategorized_filters": None,
            "coverage": {
                "full_months": len(full_after),
                "full_months_before": len(full_before),
                "newly_full_months": [month_key(m) for m in full_after if m not in full_before],
                "incomplete_months": incomplete,
                "features": [feature(*f) for f in FEATURES],
            },
            "notices": notices,
        }
        if not added:
            del result["totals"]  # absent, not null: the schema field is allOf + nullable
        if not uncategorized:
            del result["uncategorized_filters"]  # same
        else:
            days = [day_of(o.at, self.zone) for o in added]
            result["uncategorized_filters"] = {
                "from": self.at(min(days)), "to": self.at(max(days) + dt.timedelta(days=1)),
                "selection": selection_id("uncategorized"), "selection_name": "Без категории"}
        return result

    def delete_upload(self, file_name: str) -> None:
        up = next(u for u in self.ledger.uploads if u.file_name == file_name)
        self.ledger.remove_upload(up.id)

    # ---- 4.1 ----

    def feed(self, before: Optional[str] = None) -> dict:
        if before and parse_iso(before) != start_of_day(month_start(day_of(parse_iso(before), self.zone)), self.zone):
            raise ApiError(400, "VALIDATION_ERROR")  # `before` is the start of a month (docs/calc.md, section 6)
        recs = records(self.ledger)
        cov = self.ledger.coverage(self.today)
        a = Analytics(self.ledger, self.now)
        state = {
            "stale": a.stale(),
            "data_to": self.at(cov.last + dt.timedelta(days=1)) if not cov.empty else None,
            "last_operation_at": iso(max(o.at for o in self.ledger.ops.values())) if self.ledger.ops else None,
            "last_uploaded_at": iso(max(u.uploaded_at for u in self.ledger.uploads)) if self.ledger.uploads else None,
        }
        if not recs:
            return {"items": [], "summary": [], "state": state}
        if before:
            end_month = month_start(day_of(parse_iso(before), self.zone) - dt.timedelta(days=1))
        else:
            end_month = month_start(day_of(recs[0].at, self.zone))
        start_month = add_months(end_month, -2)
        s, e = start_of_day(start_month, self.zone), start_of_day(add_months(end_month, 1), self.zone)
        page = [r for r in recs if s <= r.at < e]
        older = any(r.at < s for r in recs)
        scope = Scope(self.ledger, "with")
        V = scope.visible()
        summary = []
        for i in range(3):
            m = add_months(end_month, -i)
            me = add_months(m, 1)
            if not cov.has_data_month(m):
                continue
            ops = scope.in_days(V, m, me)
            row = {"month": month_key(m), "range": {"from": self.at(m), "to": self.at(me)},
                   "expense": scope.expense(ops), "income": scope.income(ops)}
            if cov.coverage_of(m, me) == "partial":
                row["data_to"] = a.data_to(m, me)
            summary.append(row)
        return {"items": [item(r) for r in page], "summary": summary, "state": state,
                "range": {"from": iso(s), "to": iso(e)}, "next_before": iso(s) if older else None}

    def search(self, params: dict) -> dict:
        f = dict(params)
        all_time = f.pop("all_time", False)
        if all_time and ("from" in f or "to" in f):
            raise ApiError(400, "VALIDATION_ERROR")
        default = "from" not in f and "to" not in f and not all_time
        frm = parse_iso(f["from"]) if "from" in f else (
            start_of_day(self.year_ago(), self.zone) if default else None)
        to = parse_iso(f["to"]) if "to" in f else (
            start_of_day(self.today + dt.timedelta(days=1), self.zone) if default else None)
        recs = records(self.ledger)
        within = [r for r in recs if (frm is None or r.at >= frm) and (to is None or r.at < to)]
        mode = f.get("transfer_mode")
        if "selection" in f:
            test = self.selection(f["selection"])
        out = []
        for r in within:
            if mode is not None:
                # Analytics rules: no «Между своими счетами» (pairs and single transfers to self), no «Переводы»
                # with `without` (docs/calc.md, section 6; api.md 4.1).
                if r.is_pair or r.op.is_own or (mode == "without" and r.op.category.name == TRANSFERS_NAME):
                    continue
            if "kind" in f:
                if r.is_pair or r.op.is_own:
                    continue
                expense_like = r.op.is_debit or (mode is not None and r.op.is_refund)
                if (f["kind"] == "expense") != expense_like:
                    continue
            if "category_id" in f:
                cid = f["category_id"]
                if r.is_pair:
                    if cid != OWN_TRANSFER:
                        continue
                else:
                    cat = r.op.target if (mode is not None and r.op.is_refund) else r.op.category
                    if cat.id != cid:
                        continue
            if "amount_from" in f and r.amount < int(f["amount_from"]):
                continue
            if "amount_to" in f and r.amount > int(f["amount_to"]):
                continue
            if "q" in f and not self.matches(r, f["q"]):
                continue
            if "selection" in f and (r.is_pair or not test(r.op)):
                continue
            out.append(r)
        if len(out) > MAX_SEARCH:
            raise ApiError(422, "TOO_MANY_RESULTS")
        res = {"items": [item(r) for r in out], "total_count": len(out)}
        if frm is not None and to is not None:
            res["range"] = {"from": iso(frm), "to": iso(to)}
        if default:
            res["has_older_data"] = any(r.at < frm for r in recs)
        return res

    def year_ago(self) -> dt.date:
        """Today minus 12 months; the 29th of February becomes the 28th."""
        t = self.today
        try:
            return t.replace(year=t.year - 1)
        except ValueError:
            return t.replace(year=t.year - 1, day=28)

    @staticmethod
    def matches(r, q: str) -> bool:
        norm = q.strip().lower().replace("ё", "е")

        def text(s):
            return norm in s.lower().replace("ё", "е")

        try:
            d = Decimal(q.replace(" ", "").replace(",", "."))
            amount = int((d * 100).to_integral_value()) if d.is_finite() else None
        except InvalidOperation:
            amount = None
        last4 = q if len(q) == 4 and q.isdigit() else None
        ops = [r.debit, r.credit] if r.is_pair else [r.op]
        title = "Перевод между счетами" if r.is_pair else r.op.name
        return (text(title) or any(text(o.category.name) or text(o.bank.name) for o in ops)
                or (amount is not None and r.amount == amount)
                or (last4 is not None and any(o.account.id.endswith(last4) for o in ops)))

    def selection(self, sel: str):
        """Opaque selections the reference responses hand out: «Без категории», commissions, a regular payment, all
        regular payments of a card, a group of small frequent spendings."""
        if sel in self.ledger.selections:
            return self.ledger.selections[sel]
        if sel == selection_id("uncategorized"):
            return lambda o: o.category.id == UNCATEGORIZED or (o.is_refund and o.target.id == UNCATEGORIZED)
        if sel == selection_id("bank_fees"):
            from .analytics import Ctx
            return lambda o: ((o.is_debit and not o.is_own) or o.is_refund) and \
                Ctx.is_fee(o, o.target if o.is_refund else o.category)
        names = {o.name for o in self.ledger.ops.values()}
        for n in names:
            if sel == selection_id("regular", n):
                return lambda o, n=n: o.is_debit and o.name == n
        raise ApiError(422, "SELECTION_NOT_FOUND")

    # ---- 4.3 ----

    def change_category(self, account_id: str, fitid: str, category_id: str) -> dict:
        """Scenario step: 4.3 for the record of this operation, with `id` and `occurred_at` of its 4.1 record."""
        op = self.ledger.ops[(account_id, fitid)]
        rec = item(next(r for r in records(self.ledger) if r.id == (op.pair or op.id)))
        return self.patch_operation(rec["id"], rec["occurred_at"], category_id)

    def patch_operation(self, record_id: str, occurred_at: Optional[str], category_id: str) -> dict:
        """4.3. `occurred_at` is required; a time that does not match the record is a miss, not an error: the record
        is then found by `id` alone (api.md 4.2, FIN-202). The reference keeps no partitions, so it always looks up
        by `id` once the time is valid."""
        try:
            moment = parse_iso(occurred_at)
        except (AttributeError, TypeError, ValueError):
            raise ApiError(400, "VALIDATION_ERROR")
        if "T" not in occurred_at or moment.tzinfo is None:  # `datetime` of the contract: a moment with an offset
            raise ApiError(400, "VALIDATION_ERROR")
        if record_id not in {r.id for r in records(self.ledger)}:
            raise ApiError(404, "NOT_FOUND")
        try:
            recs, replaced = self.ledger.change_category(record_id, category_id)
        except ValueError:
            raise ApiError(422, "CATEGORY_NOT_ASSIGNABLE")
        by_id = {r.id: r for r in records(self.ledger)}
        items = []
        for kind, k in recs:
            rid = k if kind == "pair" else self.ledger.ops[k].id
            items.append(details(by_id[rid]))
        return {"items": items, "replaced_id": replaced}

    def dismiss(self, payment_id: str) -> None:
        """6.3 PUT: the payment is bound to its key, the description."""
        name = next((o.name for o in self.ledger.ops.values() if stable_id("payment", o.name) == payment_id), None)
        if name is None:
            raise ApiError(404, "NOT_FOUND")
        self.ledger.dismissed.add(name)

    # ---- 5.1, 6.1, 6.2 ----

    def categories(self) -> dict:
        return {"categories": [c.api() for c in self.reg.all()]}

    def analytics(self, period: str = "month", date: Optional[str] = None, mode: str = "with",
                  supports: Optional[str] = None) -> dict:
        if (period == "all_time" and date not in (None, "all_time")) or (period != "all_time" and date == "all_time"):
            raise ApiError(400, "VALIDATION_ERROR")
        try:
            parse_supports(supports)
        except ValueError:
            raise ApiError(400, "VALIDATION_ERROR")
        return Analytics(self.ledger, self.now).screen(period, date, mode, supports)

    def periods(self, period: str = "month") -> dict:
        return Analytics(self.ledger, self.now).periods(period)
