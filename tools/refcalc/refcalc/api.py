"""Responses of the client API (api.md 3.3, 4.1, 4.3, 5.1, 6.1, 6.2) computed by the reference calculation."""
from __future__ import annotations

import datetime as dt
import calendar
from decimal import Decimal, InvalidOperation
from typing import Optional
from urllib.parse import urlencode
import re
import hashlib
import json
from dataclasses import asdict
import uuid
from copy import deepcopy

from .analytics import Analytics, Ctx, SINGLETONS, parse_supports, period_bounds
from .ledger import Ledger, stable_id
from .recalculation import Recalculation
from .regular_projection import compose_regular
from .registry import OWN_TRANSFER, REFUND, TRANSFERS_NAME, UNCATEGORIZED, Registry
from .util import add_months, day_of, end_of_day, iso, month_key, month_start, parse_iso, start_of_day
from .views import Scope, account, details, item, records, selection_id

MAX_SEARCH = 10_000
FEATURES = [  # (code, name, threshold kind, required) — spec section 7
    ("comparison", "Сравнение с прошлым месяцем", "full", 1),
    ("monthly_chart", "Расходы по месяцам", "with_data", 1),
    ("typical", "Обычные расходы", "full", 2),
    ("regular_payments", "Регулярные платежи", "full", 2),
    ("notable_spending", "Заметные траты", "full", 3),
    ("small_frequent", "Мелкие частые траты", "full", 1),
    ("year_forecast", "Прогноз на год", "full", 12),
]


class ApiError(Exception):
    def __init__(self, status: int, code: str, details=None, retryable=None):
        super().__init__(code)
        self.status, self.code = status, code
        self.details = details
        self.retryable = retryable


class Backend:
    def __init__(self, registry: Registry, zone):
        self.reg = registry
        self.zone = zone
        self.ledger = Ledger(registry, zone)
        self.now: dt.datetime = None
        self._chart_owner = uuid.uuid4().hex
        self._chart_cursors = {}
        self._regular_snapshots = {}
        self._regular_absent = set()
        self._regular_references = {}
        self.recalculation = Recalculation(self._publish_regular_snapshots)

    @property
    def today(self) -> dt.date:
        return day_of(self.now, self.zone)

    def at(self, d: dt.date) -> str:
        return iso(start_of_day(d, self.zone))

    # ---- 3.1 ----

    def import_config(self, period_from: Optional[str] = None, period_to: Optional[str] = None) -> dict:
        """Server-owned download range; both requested calendar days are inclusive."""
        def invalid(field, code):
            raise ApiError(400, "VALIDATION_ERROR", [{"field": field, "code": code}])

        def parse(value, field):
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                invalid(field, "INVALID_FORMAT")
            try:
                return dt.date.fromisoformat(value)
            except ValueError:
                invalid(field, "INVALID_FORMAT")

        def boundary(day, field):
            try:
                instant = start_of_day(day, self.zone).astimezone(dt.timezone.utc)
                return instant.astimezone(self.zone)
            except (ValueError, OverflowError):
                invalid(field, "OUT_OF_RANGE")

        start = parse(period_from, "period_from") if period_from is not None else None
        last = parse(period_to, "period_to") if period_to is not None else None
        if (period_from is None) != (period_to is None):
            invalid("period_from" if period_from is None else "period_to", "REQUIRED")
        ats = [o.at for o in self.ledger.ops.values()]
        if period_from is not None:
            if start > last:
                invalid("period_from", "OUT_OF_RANGE")
            if last > self.today:
                invalid("period_to", "OUT_OF_RANGE")
        else:
            start = day_of(max(ats), self.zone) if ats else self.today.replace(
                year=self.today.year - 1, day=min(self.today.day, calendar.monthrange(self.today.year - 1, self.today.month)[1]))
            last = self.today
        try:
            end = last + dt.timedelta(days=1)
        except OverflowError:
            invalid("period_to", "OUT_OF_RANGE")
        begin, finish = boundary(start, "period_from"), boundary(end, "period_to")
        if begin.astimezone(dt.timezone.utc) >= finish.astimezone(dt.timezone.utc):
            invalid("period_to", "OUT_OF_RANGE")
        epoch = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
        def millis(at):
            delta = at.astimezone(dt.timezone.utc) - epoch
            return delta.days * 86_400_000 + delta.seconds * 1_000 + delta.microseconds // 1000
        query = urlencode({"appName": "supreme", "appVersion": "0.0.1", "origin": "web,ib5,platform",
                           "start": millis(begin), "end": millis(finish), "format": "ofx"})
        out = {"time_zone": str(self.zone), "download_url": "https://www.tbank-online.com/mybank/api/operations/timeline/public/legacy/v1/export_operations?" + query,
               "login_url": "https://www.tbank.ru/login/", "max_file_size_bytes": 10485760, "accepted_formats": ["OFX"],
               "suggested_period": {"from": iso(begin), "to": iso(finish)}}
        if ats:
            out["loaded"] = {"first_operation_at": iso(min(ats)), "last_operation_at": iso(max(ats)),
                             "operation_count": len(ats)}
        return out

    # ---- 3.2 / 3.3 ----

    def upload(self, file_name: str, text: str) -> dict:
        before = self.ledger.coverage(self.today)
        is_first = not self.ledger.uploads
        up, added, duplicates = self.ledger.add_upload(file_name, text, self.now)
        self.recalculation.calculate_now()
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
            "time_zone": str(self.zone),
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
        self.recalculation.calculate_now()

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
            return {"time_zone": str(self.zone), "items": [], "summary": [], "state": state}
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
        return {"time_zone": str(self.zone), "items": [item(r) for r in page], "summary": summary, "state": state,
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
        res = {"time_zone": str(self.zone), "items": [item(r) for r in out], "total_count": len(out)}
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
            selection = self.ledger.selections[sel]
            if isinstance(selection, tuple) and selection[0] == 'regular_all':
                block = self._source_regular_block(selection[1], selection[2])
                names = {row['title'] for row in (block or {}).get('data', {}).get('items', [])}
                return lambda o: o.is_debit and o.name in names
            return selection
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
        self.recalculation.calculate_now()
        return {"items": items, "replaced_id": replaced}

    def _source_regular_block(self, date, mode):
        """Source recognition shared by the producer and live operation search, never Apply."""
        source = Analytics(self.ledger, self.now, regular_limit=None)
        if not self.ledger.ops or source.cov.empty:
            return None
        start, end, period_key = period_bounds('month', dt.date.fromisoformat(date + '-01'))
        if source.cov.bounds(start, end) is None:
            return None
        scope = Scope(self.ledger, mode)
        visible = scope.visible()
        ctx = Ctx(source, 'month', start, end, period_key, mode, scope, visible,
                  scope.in_days(visible, start, end), {'coverage': source.coverage_of('month', start, end)})
        block = ctx.regular_payments()
        for row in block['data'].get('items', []):
            self._regular_references[row['button']['payment_id']] = row['title']
        return block

    def _store_regular_snapshot(self, date, mode, version=None):
        """Reference publisher: retain the full metric before limiting its displayed rows."""
        key = date, mode
        version = self.recalculation.ready_version if version is None else version
        existing = self._regular_snapshots.get(key)
        if existing and existing['version'] == version:
            return
        block = self._source_regular_block(date, mode)
        if block is None:
            self._regular_snapshots.pop(key, None)
            self._regular_absent.add(key)
            return
        self._regular_absent.discard(key)
        self._regular_snapshots[key] = {'version': version,
            'params': {'period': 'month', 'date': date, 'transfer_mode': mode},
            'currency': 'RUB', 'time_zone': str(self.zone), 'block': block}

    def _publish_regular_snapshots(self, version):
        # Existing producer delivery installs new bases before final; Apply never invokes this.
        for date, mode in set(self._regular_snapshots) | self._regular_absent:
            self._store_regular_snapshot(date, mode, version)

    def apply_regular_dismissal(self, payment_id, target, date, mode, fail_compose=False):
        """Own saved metric only. Failure rolls back analytics, never the source assignment."""
        def compose(excluded):
            saved = self._regular_snapshots.get((date, mode))
            if saved is None and (date, mode) in self._regular_absent:
                raise ApiError(400, 'VALIDATION_ERROR', [{'field': 'date', 'code': 'OUT_OF_RANGE'}], retryable=False)
            if fail_compose or saved is None:
                raise ApiError(500, 'INTERNAL_ERROR', retryable=True)
            return {**{k: deepcopy(saved[k]) for k in ('params', 'currency', 'time_zone')},
                    'required_calc_version': target, 'blocks': [compose_regular(saved['block'], excluded)]}
        return self.recalculation.apply_dismissal(payment_id, target, compose)

    def dismiss(self, payment_id: str, dismissed=True, marker_ack=True, defer_calculation=False,
                date=None, mode=None, fail_compose=False):
        """PUT returns the full projection; DELETE retains its bodyless asynchronous result."""
        if dismissed:
            for field, valid in [('date', isinstance(date, str) and bool(re.fullmatch(r'[0-9]{4}-(0[1-9]|1[0-2])', date)) and date[:4] != '0000'),
                                 ('transfer_mode', mode in ('with', 'without'))]:
                if not valid:
                    raise ApiError(400, 'VALIDATION_ERROR', [{'field': field, 'code': 'REQUIRED' if (date if field == 'date' else mode) is None else 'INVALID_FORMAT'}])
        if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", payment_id):
            raise ApiError(400, "VALIDATION_ERROR", [{"field": "id", "code": "INVALID_FORMAT"}])
        name = self._regular_references.get(payment_id)
        if name is None:
            name = next((o.name for o in self.ledger.ops.values() if stable_id("payment", o.name) == payment_id), None)
        if name is None:
            raise ApiError(404, "NOT_FOUND")
        if dismissed:
            self._store_regular_snapshot(date, mode)
        changed = (name in self.ledger.dismissed) != dismissed
        try:
            if dismissed:
                target = self.recalculation.assign(payment_id, True, marker_ack)['calc_version']
            elif changed:
                self.recalculation.restore(payment_id)
        except OverflowError:
            raise ApiError(500, "INTERNAL_ERROR")
        if dismissed:
            self.ledger.dismissed.add(name)
        else:
            self.ledger.dismissed.discard(name)
        result = self.apply_regular_dismissal(payment_id, target, date, mode, fail_compose) if dismissed else None
        if not defer_calculation:
            self.recalculation.run_pending()
        return result

    # ---- 5.1, 6.1, 6.2 ----

    def categories(self) -> dict:
        return {"categories": [c.api() for c in self.reg.all()]}

    def analytics(self, period: str = "month", date: Optional[str] = None, mode: str = "with",
                  supports: Optional[str] = None) -> dict:
        if (period in SINGLETONS and date not in (None, period)) or (date in SINGLETONS and date != period):
            raise ApiError(400, "VALIDATION_ERROR", [{"field": "date", "code": "INVALID_FORMAT",
                                                     "message": "has invalid format"}])
        try:
            parse_supports(supports)
        except ValueError:
            raise ApiError(400, "VALIDATION_ERROR", [{"field": "supports", "code": "INVALID_FORMAT",
                                                     "message": "has invalid format"}])
        out = Analytics(self.ledger, self.now, lambda anchor: self._chart_page(mode, anchor=anchor)).screen(
            period, date, mode, supports)
        # Reference financial responses are calculated immediately except explicit delayed-marker scenarios.
        if out['has_data'] and self.recalculation.state_version == 0 and not self.recalculation.assignments:
            self.recalculation.calculate_now()
        if out['has_data']:
            out['time_zone'] = str(self.zone)
        if out['has_data'] and period == 'month':
            selected = out['params']['date']
            self._store_regular_snapshot(selected, mode)
            saved = self._regular_snapshots.get((selected, mode))
            if saved and self.recalculation.overlays:
                for i, block in enumerate(out['blocks']):
                    if block.get('id') == 'regular_payments':
                        out['blocks'][i] = compose_regular(saved['block'], set(self.recalculation.overlays))
        out['calculation'] = self.recalculation.state()
        if 'state' in out:
            out['state']['recalculating'] = out['calculation']['recalculating']
        return out

    def _chart_revision(self, mode):
        # A reference-only fingerprint, not a prescribed production token/storage scheme.
        state = (self._chart_owner, mode, str(self.zone), self.today,
                 self.ledger.coverage(self.today).days,
                 [(key, asdict(op)) for key, op in sorted(self.ledger.ops.items())],
                 sorted(self.ledger.manual.items()), sorted(self.ledger.unpaired), sorted(self.ledger.pairs.items()))
        def encode(value):
            if isinstance(value, (dt.date, dt.datetime)):
                return value.isoformat()
            if isinstance(value, (set, frozenset)):
                return sorted(value)
            raise TypeError(type(value).__name__)
        wire = json.dumps(state, default=encode, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(wire.encode()).hexdigest()

    def _chart_page(self, mode, anchor=None, index=None):
        a = Analytics(self.ledger, self.now)
        first, last = month_start(a.cov.first), month_start(a.cov.last)
        distance = (last.year - first.year) * 12 + last.month - first.month
        if index is None:
            distance_to_anchor = 0 if anchor is None else (last.year - anchor.year) * 12 + last.month - anchor.month
            index = min(distance // 60, max(0, distance_to_anchor // 60))
        end = add_months(last, 1 - index * 60)
        # Avoid computing a date before year 1 when the oldest page is short.
        count = min(60, distance + 1 - index * 60)
        start = add_months(end, -count)
        scope = Scope(self.ledger, mode)
        visible = scope.visible()
        ctx = Ctx(a, "month", start, end, month_key(start), mode, scope, visible, [], {})
        points = []
        for i in range(count):
            m = add_months(start, i)
            e = add_months(m, 1)
            covered = a.cov.bounds(m, e) is not None
            amount = scope.expense(scope.in_days(visible, m, e)) if covered else None
            point = ctx.point(m, amount)
            if covered:
                point["action"] = {"type": "select_analytics_period", "period": "month", "date": month_key(m)}
            points.append(point)
        revision = self._chart_revision(mode)
        page = {"revision": revision}
        for key, target in (("previous", index + 1), ("next", index - 1)):
            if 0 <= target <= distance // 60:
                cursor = uuid.uuid4().hex
                self._chart_cursors[cursor] = (revision, mode, target)
                page[key] = cursor
        return {"id": "monthly_chart", "type": "chart", "v": 1, "title": "Расходы по месяцам",
                "alt_text": "Расходы по месяцам всей загруженной истории", "status": "ready",
                "data": {"kind": "bar", "value_kind": "amount", "points": points, "page": page},
                "help_text": "Столбики показывают полные календарные месяцы всей загруженной истории. "
                             "Прокрутите к краю графика, чтобы загрузить соседние месяцы. "
                             "Нажмите на месяц с данными, чтобы открыть его аналитику. "
                             "Итоги и выводы экрана относятся к выбранному периоду."}

    def chart_page(self, cursor=None):
        def invalid(code):
            raise ApiError(400, "VALIDATION_ERROR", [{"field": "cursor", "code": code}])
        if cursor is None or cursor == "":
            invalid("REQUIRED")
        if not isinstance(cursor, str) or len(cursor) > 2048 or cursor not in self._chart_cursors:
            invalid("INVALID_FORMAT")
        revision, mode, index = self._chart_cursors[cursor]
        if revision != self._chart_revision(mode):
            invalid("OUT_OF_RANGE")
        return {"block": self._chart_page(mode, index=index), "time_zone": str(self.zone)}

    def periods(self, period: str = "month") -> dict:
        out = Analytics(self.ledger, self.now).periods(period)
        if out['periods']:
            out['time_zone'] = str(self.zone)
        return out
