"""Operations of one user and the accounting rules of spec FIN-106, sections 2–4."""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Optional

from . import ofx
from .registry import OWN_TRANSFER, REFUND, TRANSFERS_NAME, UNCATEGORIZED, Category, Registry
from .util import add_months, day_of, month_start

SELF_TRANSFER_NAMES = {"Между своими счетами", "Себе в другой банк", "Себе из другого банка"}
OWN_TRANSFER_NAME = "Между своими счетами"
PAIR_WINDOW = dt.timedelta(hours=72)
REFUND_AMOUNT_WINDOW = dt.timedelta(days=60)
NS = uuid.UUID("6f1d3a52-5a0c-4b8e-9a5e-1f6a2b7c0d01")


def stable_id(*parts: str) -> str:
    return str(uuid.uuid5(NS, "|".join(parts)))


@dataclass
class Op:
    key: tuple
    account: ofx.Account
    fitid: str
    is_debit: bool
    at: dt.datetime
    amount: int
    name: str
    bank: Category
    currency: str
    memo: str = ""  # bank category as in the statement, cut to 64 characters
    uploads: set = field(default_factory=set)
    manual: Optional[str] = None
    pair: Optional[str] = None
    unpaired: bool = False
    category: Category = None
    # Refund target: None — not a refund; the uncategorized category — a refund without an expense category.
    target: Optional[Category] = None

    @property
    def id(self) -> str:
        return stable_id("op", *self.key)

    @property
    def is_refund(self) -> bool:
        return self.category.id == REFUND

    @property
    def is_own(self) -> bool:
        return self.category.id == OWN_TRANSFER


@dataclass
class Upload:
    id: str
    file_name: str
    uploaded_at: dt.datetime
    doc: ofx.Document
    first_day: Optional[dt.date]
    last_day: Optional[dt.date]


class Coverage:
    """Covered days: the union of the periods of all finished uploads (spec section 4)."""

    def __init__(self, ranges: list, today: dt.date):
        self.today = today
        merged = []
        for a, b in sorted(ranges):
            if merged and a <= merged[-1][1] + dt.timedelta(days=1):
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        self.days = merged

    @property
    def empty(self) -> bool:
        return not self.days

    @property
    def first(self) -> Optional[dt.date]:
        return self.days[0][0] if self.days else None

    @property
    def last(self) -> Optional[dt.date]:
        return self.days[-1][1] if self.days else None

    def covered(self, d: dt.date) -> bool:
        return any(a <= d <= b for a, b in self.days)

    def gaps(self) -> list:
        """Uncovered day intervals between covered ones: (first day, day after the last)."""
        return [(b + dt.timedelta(days=1), a2) for (_, b), (a2, _) in zip(self.days, self.days[1:])]

    def gaps_within(self, start: dt.date, end_excl: dt.date) -> list:
        return [g for g in self.gaps() if g[0] < end_excl and g[1] > start]

    def bounds(self, start: dt.date, end_excl: dt.date):
        first = last = None
        for a, b in self.days:
            s, e = max(a, start), min(b, end_excl - dt.timedelta(days=1))
            if s > e:
                continue
            if first is None:
                first = s
            last = e
        return (first, last) if first else None

    def count_days(self, start: dt.date, end_excl: dt.date) -> int:
        n = 0
        for a, b in self.days:
            s, e = max(a, start), min(b, end_excl - dt.timedelta(days=1))
            if s <= e:
                n += (e - s).days + 1
        return n

    def full(self, start: dt.date, end_excl: dt.date) -> bool:
        """Ended and every day covered."""
        return end_excl - dt.timedelta(days=1) < self.today and \
            self.count_days(start, end_excl) == (end_excl - start).days

    def full_month(self, m: dt.date) -> bool:
        return self.full(m, add_months(m, 1))

    def has_data_month(self, m: dt.date) -> bool:
        return self.bounds(m, add_months(m, 1)) is not None

    def months_with_data(self) -> list:
        if self.empty:
            return []
        out, m = [], month_start(self.first)
        while m <= self.last:
            if self.has_data_month(m):
                out.append(m)
            m = add_months(m, 1)
        return out

    def full_months(self) -> list:
        return [m for m in self.months_with_data() if self.full_month(m)]

    def coverage_of(self, start: dt.date, end_excl: dt.date) -> str:
        if self.bounds(start, end_excl) is None:
            return "no_data"
        return "complete" if self.full(start, end_excl) else "partial"


class Ledger:
    def __init__(self, registry: Registry, zone):
        self.reg = registry
        self.zone = zone
        self.ops = {}          # key → Op, in import order
        self.pairs = {}        # pair id → (debit key, credit key)
        self.manual = {}       # key → category id; survives re-import (spec 3.4)
        self.unpaired = set()  # keys of operations from a pair the user split
        self.uploads = []      # finished uploads, oldest first
        self.dismissed = set() # descriptions of payments marked «Не подписка»

    # ---- uploads ----

    def upload_days(self, doc: ofx.Document):
        """Period of an upload: min DTSTART .. max DTEND of its accounts, both days included; without them — first
        and last operation (spec section 4)."""
        if doc.periods:
            starts = [p[0] for p in doc.periods.values()]
            ends = [p[1] for p in doc.periods.values()]
            return day_of(min(starts), self.zone), day_of(max(ends), self.zone)
        if doc.transactions:
            ats = [t.posted_at for t in doc.transactions]
            return day_of(min(ats), self.zone), day_of(max(ats), self.zone)
        return None, None

    def coverage(self, today: dt.date, uploads=None) -> Coverage:
        ups = self.uploads if uploads is None else uploads
        return Coverage([(u.first_day, u.last_day) for u in ups if u.first_day], today)

    def add_upload(self, file_name: str, text: str, uploaded_at: dt.datetime):
        doc = ofx.parse(text)
        first, last = self.upload_days(doc)
        up = Upload(stable_id("upload", file_name, uploaded_at.isoformat()), file_name, uploaded_at, doc, first, last)
        accounts = {a.id: a for a in doc.accounts}
        added, duplicates = [], 0
        for t in doc.transactions:
            key = (t.account_id, t.fitid)
            op = self.ops.get(key)
            if op:
                op.uploads.add(up.id)
                duplicates += 1
                continue
            op = Op(key, accounts[t.account_id], t.fitid, t.is_debit, t.posted_at, t.amount, t.name,
                    self.reg.bank(t.memo), t.currency, t.memo.strip()[:64], {up.id})
            op.manual = self.manual.get(key)
            op.unpaired = key in self.unpaired
            self.ops[key] = op
            added.append(op)
        self.uploads.append(up)
        self.uploads.sort(key=lambda u: (u.uploaded_at, u.id))
        self.apply_rules()
        return up, added, duplicates

    def remove_upload(self, upload_id: str) -> None:
        for key in [k for k, op in self.ops.items() if upload_id in op.uploads]:
            op = self.ops[key]
            op.uploads.discard(upload_id)
            if not op.uploads:
                del self.ops[key]
        self.uploads = [u for u in self.uploads if u.id != upload_id]
        self.apply_rules()

    # ---- manual category (api.md 4.3, spec 3.4) ----

    def record_of(self, record_id: str):
        for pid, (d, c) in self.pairs.items():
            if pid == record_id:
                return ("pair", pid)
        for op in self.ops.values():
            if op.id == record_id:
                return ("pair", op.pair) if op.pair else ("op", op.key)
        return None

    def change_category(self, record_id: str, cid: str):
        """Returns (records, replaced_id); raises ValueError for CATEGORY_NOT_ASSIGNABLE, KeyError for NOT_FOUND."""
        rec = self.record_of(record_id)
        if rec is None:
            raise KeyError(record_id)
        cat = self.reg.by_id.get(cid)
        if cat is None or not cat.assignable:
            raise ValueError("not assignable")
        if rec[0] == "pair":
            pid = rec[1]
            debit, credit = (self.ops[k] for k in self.pairs[pid])
            if cid == OWN_TRANSFER:
                return [("pair", pid)], None
            target, other = (debit, credit) if "expense" in cat.kinds else (credit, debit)
            del self.pairs[pid]
            for op in (debit, credit):
                op.pair = None
                op.unpaired = True
                self.unpaired.add(op.key)
            self._set_manual(target, cid)
            other.manual = None
            self.manual.pop(other.key, None)
            self.apply_rules()
            return [("op", debit.key), ("op", credit.key)], pid
        op = self.ops[rec[1]]
        kind = "expense" if op.is_debit else "income"
        if kind not in cat.kinds:
            raise ValueError("kind mismatch")
        self._set_manual(op, cid)
        self.apply_rules()
        if op.pair:
            return [("pair", op.pair)], op.id
        return [("op", op.key)], None

    def _set_manual(self, op: Op, cid: str) -> None:
        op.manual = cid
        self.manual[op.key] = cid
        if cid == OWN_TRANSFER:
            op.unpaired = False
            self.unpaired.discard(op.key)

    # ---- rules (spec 3.2–3.5) ----

    def self_transfer(self, op: Op) -> bool:
        if op.manual:
            return op.manual == OWN_TRANSFER
        if op.unpaired:
            return False
        return op.name in SELF_TRANSFER_NAMES or op.bank.name == OWN_TRANSFER_NAME

    def apply_rules(self) -> None:
        self._pair()
        ordered = sorted(self.ops.values(), key=lambda o: (o.at, o.key))
        for op in ordered:
            op.target = None
            if op.manual:
                op.category = self.reg.get(op.manual)
            elif op.pair:
                op.category = self.reg.get(OWN_TRANSFER)
            elif self.self_transfer(op):
                op.category = self.reg.get(OWN_TRANSFER)
            else:
                op.category = op.bank
        debits = [o for o in ordered if o.is_debit]
        for op in ordered:
            if op.is_debit or op.pair:
                continue
            if op.manual:
                if op.manual == REFUND:
                    op.target = self._target(op, debits)
                continue
            if self.self_transfer(op):
                continue
            if self._is_refund(op, debits):
                op.category = self.reg.get(REFUND)
                op.target = self._target(op, debits)

    def _pair(self) -> None:
        for pid in list(self.pairs):
            d, c = self.pairs[pid]
            if d not in self.ops or c not in self.ops:
                del self.pairs[pid]
        for op in self.ops.values():
            op.pair = None
        for pid, (d, c) in self.pairs.items():
            self.ops[d].pair = pid
            self.ops[c].pair = pid
        credits = [o for o in self.ops.values() if not o.is_debit]
        for debit in sorted((o for o in self.ops.values() if o.is_debit), key=lambda o: (o.at, o.key)):
            if debit.pair or not self.self_transfer(debit):
                continue
            best = None
            for c in credits:
                if c.pair or c.amount != debit.amount or c.account.id == debit.account.id:
                    continue
                if not (self.self_transfer(c) or (not c.manual and c.bank.name == TRANSFERS_NAME)):
                    continue
                gap = abs(c.at - debit.at)
                if gap > PAIR_WINDOW:
                    continue
                rank = (gap, c.at, c.key)
                if best is None or rank < best[0]:
                    best = (rank, c)
            if best:
                c = best[1]
                pid = stable_id("pair", *debit.key)
                self.pairs[pid] = (debit.key, c.key)
                debit.pair = c.pair = pid

    @staticmethod
    def _same_name_debits(op: Op, debits: list) -> list:
        name = op.name.lower()
        if not name:
            return []
        return [d for d in debits if d.name and d.name.lower() == name and d.at < op.at and d.bank.name != TRANSFERS_NAME]

    def _is_refund(self, op: Op, debits: list) -> bool:
        return op.bank.is_expense or bool(self._same_name_debits(op, debits)) or "возврат" in op.name.lower()

    def _target(self, op: Op, debits: list) -> Category:
        def usable(d: Op):
            return d.category.id not in (OWN_TRANSFER, REFUND)

        same = self._same_name_debits(op, debits)
        if same and usable(same[-1]):
            return same[-1].category
        by_amount = [d for d in debits if d.account.id == op.account.id and d.amount == op.amount and d.at < op.at
                     and op.at - d.at <= REFUND_AMOUNT_WINDOW and d.bank.name != TRANSFERS_NAME]
        if by_amount and usable(by_amount[-1]):
            return by_amount[-1].category
        if op.bank.is_expense:
            return op.bank
        return self.reg.get(UNCATEGORIZED)

    # ---- unread lines (api.md 3.6, FIN-79) ----

    def open_unread(self) -> list:
        """(upload, line) pairs of finished uploads not closed by a later upload."""
        out = []
        for i, up in enumerate(self.uploads):
            later = self.uploads[i + 1:]
            for line in up.doc.unread:
                if not any(self._closes(newer, up, line) is not None for newer in later):
                    out.append((up, line))
        return out

    def _closes(self, newer: Upload, old: Upload, line: ofx.UnreadLine):
        acc = line.account_id
        if acc is None:
            return None
        newer_unread = [l for l in newer.doc.unread if l.account_id == acc]
        if line.fitid is not None:
            op = self.ops.get((acc, line.fitid))
            if op and newer.id in op.uploads:
                return True
            if any(l.fitid == line.fitid for l in newer_unread):
                return False
            return None
        period = newer.doc.periods.get(acc)
        if period is None:
            return None
        nd = (day_of(period[0], self.zone), day_of(period[1], self.zone))
        if line.date is not None:
            d = day_of(line.date, self.zone)
            if not nd[0] <= d <= nd[1]:
                return None
            return not any(l.date and day_of(l.date, self.zone) == d for l in newer_unread)
        old_period = old.doc.periods.get(acc)
        if old_period is None:
            return None
        od = (day_of(old_period[0], self.zone), day_of(old_period[1], self.zone))
        if nd[0] > od[0] or nd[1] < od[1]:
            return None
        return not any(l.date is None or od[0] <= day_of(l.date, self.zone) <= od[1] for l in newer_unread)
