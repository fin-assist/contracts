"""Records of the API (a single operation or an own-transfer pair) and the analytics scope (spec 3.6)."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

from .ledger import Ledger, Op, stable_id
from .registry import OWN_TRANSFER, TRANSFERS_NAME, UNCATEGORIZED
from .util import day_of, iso

PAIR_TITLE = "Перевод между счетами"


@dataclass
class Record:
    op: Optional[Op] = None
    debit: Optional[Op] = None
    credit: Optional[Op] = None
    pair_id: Optional[str] = None

    @property
    def is_pair(self) -> bool:
        return self.pair_id is not None

    @property
    def at(self) -> dt.datetime:
        return self.debit.at if self.is_pair else self.op.at

    @property
    def amount(self) -> int:
        return self.debit.amount if self.is_pair else self.op.amount

    @property
    def id(self) -> str:
        return self.pair_id if self.is_pair else self.op.id

    @property
    def sort_key(self):
        """Newest first; equal moments — by (account, FITID) of the record, descending (README, «Порядок»)."""
        return (self.at, (self.debit if self.is_pair else self.op).key)


def records(ledger: Ledger) -> list:
    out, seen = [], set()
    for op in ledger.ops.values():
        if op.pair:
            if op.pair in seen:
                continue
            seen.add(op.pair)
            d, c = (ledger.ops[k] for k in ledger.pairs[op.pair])
            out.append(Record(debit=d, credit=c, pair_id=op.pair))
        else:
            out.append(Record(op=op))
    out.sort(key=lambda r: r.sort_key, reverse=True)
    return out


def account(a) -> dict:
    d = {"type_name": "Счёт другого банка" if a.is_other_bank else ("Накопительный" if a.type.upper() == "SAVINGS" else "Текущий")}
    if a.mask:
        d["mask"] = a.mask
    return d


def note(op: Op) -> Optional[str]:
    if op.is_own:
        return "не в тратах"
    if op.is_refund:
        return "возврат" if op.target.id == UNCATEGORIZED else f"возврат · {op.target.name}"
    return None


def item(r: Record) -> dict:
    """`items[]` of 4.1."""
    if r.is_pair:
        cat = r.debit.category
        return {
            "id": r.id, "kind": "own_transfer", "occurred_at": iso(r.at), "amount": r.amount, "currency": r.debit.currency,
            "title": PAIR_TITLE, "category_id": cat.id, "category_name": cat.name, "category_icon": cat.icon,
            "is_category_manual": False, "note": "не в тратах",
            "from_account": account(r.debit.account), "to_account": account(r.credit.account), "status": "posted",
        }
    op = r.op
    d = {
        "id": op.id, "kind": "expense" if op.is_debit else "income", "occurred_at": iso(op.at), "amount": op.amount,
        "currency": op.currency, "title": op.name, "category_id": op.category.id, "category_name": op.category.name,
        "category_icon": op.category.icon, "is_category_manual": op.manual is not None, "note": note(op),
        "account": account(op.account), "status": "posted",
    }
    return d


def details(r: Record) -> dict:
    """Records of 4.3 are whole, as in 4.2."""
    d = item(r)
    d["source_name"] = "Выписка Т-Банка"
    if r.is_pair:
        for side, op in (("from_account", r.debit), ("to_account", r.credit)):
            d[side]["spoken"] = "счёт"
        d["note_details"] = "Не считаем тратой и доходом"
        return d
    op = r.op
    d["account"]["spoken"] = "счёт"
    d["description"] = op.name or None
    d["bank_category"] = op.memo or None
    if op.is_own:
        d["note_details"] = "Не считаем тратой и доходом"
    elif op.is_refund:
        d["note_details"] = "Уменьшает расходы"
    else:
        d["note_details"] = None
    return d


class Scope:
    """What analytics counts (spec 3.6)."""

    def __init__(self, ledger: Ledger, mode: str):
        self.ledger = ledger
        self.mode = mode
        self.zone = ledger.zone

    def visible(self) -> list:
        return [o for o in self.ledger.ops.values()
                if not o.pair and not (self.mode == "without" and o.category.name == TRANSFERS_NAME)]

    def in_days(self, ops: list, start: dt.date, end_excl: dt.date) -> list:
        return [o for o in ops if start <= day_of(o.at, self.zone) < end_excl]

    @staticmethod
    def expense_lines(ops: list) -> list:
        """(op, category, signed amount): debits outside «Между своими счетами», refunds negative in the target."""
        out = []
        for o in ops:
            if o.is_refund:
                out.append((o, o.target, -o.amount))
            elif o.is_debit and not o.is_own:
                out.append((o, o.category, o.amount))
        return out

    @staticmethod
    def incomes(ops: list) -> list:
        return [o for o in ops if not o.is_debit and not o.is_refund and not o.is_own]

    def expense(self, ops: list) -> int:
        return sum(a for _, _, a in self.expense_lines(ops))

    def income(self, ops: list) -> int:
        return sum(o.amount for o in self.incomes(ops))


def selection_id(kind: str, *parts: str) -> str:
    return "s_" + kind + "_" + stable_id(kind, *parts)[:8]
