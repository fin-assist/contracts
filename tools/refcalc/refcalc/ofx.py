"""T-Bank OFX 2.0.2 reader (docs/calc.md, section 2; Confluence «Формат выписки Т-Банка (OFX)»).

The format is flat XML, so a small scanner is enough. A transaction that cannot be counted becomes an unread line
with the number of the line its `<STMTTRN>` tag is on.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Optional

TAG = re.compile(r"<([A-Z0-9.]+)>([^<]*)")
ACCOUNT = re.compile(r"<BANKACCTFROM>(.*?)</BANKACCTFROM>", re.S)
STMTTRNRS = re.compile(r"<STMTTRNRS>(.*?)</STMTTRNRS>", re.S)
TRN = re.compile(r"<STMTTRN>(.*?)</STMTTRN>", re.S)
ZONE = re.compile(r"\[([+-]?\d+)(?::[A-Z]+)?]")


@dataclass
class Account:
    id: str
    type: str

    @property
    def is_other_bank(self) -> bool:
        return self.id.startswith("OB$")

    @property
    def mask(self) -> Optional[str]:
        return None if self.is_other_bank else "··" + self.id[-4:]


@dataclass
class Transaction:
    account_id: str
    fitid: str
    is_debit: bool
    posted_at: dt.datetime
    amount: int  # kopecks, without sign
    name: str
    memo: str
    currency: str
    line: int


@dataclass
class UnreadLine:
    line: int
    date: Optional[dt.datetime]
    reason: str
    account_id: Optional[str]
    fitid: Optional[str]


@dataclass
class Document:
    accounts: list = field(default_factory=list)
    transactions: list = field(default_factory=list)
    unread: list = field(default_factory=list)
    # DTSTART / DTEND by account id.
    periods: dict = field(default_factory=dict)


def parse_date(raw: str) -> Optional[dt.datetime]:
    """`20260930175338.000[+3:MSK]` → aware datetime; no zone → +03:00 (docs/calc.md, section 2)."""
    digits = ""
    for c in raw.strip():
        if not c.isdigit():
            break
        digits += c
    if len(digits) < 8:
        return None
    digits = (digits + "000000")[:14]
    try:
        local = dt.datetime.strptime(digits, "%Y%m%d%H%M%S")
    except ValueError:
        return None
    m = ZONE.search(raw)
    hours = int(m.group(1)) if m else 3
    return local.replace(tzinfo=dt.timezone(dt.timedelta(hours=hours)))


def unescape(s: str) -> str:
    return (s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'")
            .replace("&amp;", "&"))


def fields(block: str) -> dict:
    return {m.group(1): m.group(2).strip() for m in TAG.finditer(block)}


def parse(text: str) -> Document:
    line_starts = [0] + [i + 1 for i, c in enumerate(text) if c == "\n"]

    def line_at(offset: int) -> int:
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    doc = Document()
    for stmt in STMTTRNRS.finditer(text):
        body, body_offset = stmt.group(1), stmt.start(1)
        acc = ACCOUNT.search(body)
        if not acc:
            continue
        af = fields(acc.group(1))
        if "ACCTID" not in af:
            continue
        account = Account(af["ACCTID"], af.get("ACCTTYPE", "CHECKING"))
        doc.accounts.append(account)
        head = body.split("<BANKTRANLIST>", 1)[1].split("<STMTTRN>", 1)[0] if "<BANKTRANLIST>" in body else ""
        lf = fields(head)
        start, end = parse_date(lf.get("DTSTART", "")), parse_date(lf.get("DTEND", ""))
        if start and end:
            doc.periods[account.id] = (start, end)
        for trn in TRN.finditer(body):
            line = line_at(body_offset + trn.start())
            f = fields(trn.group(1))
            date = parse_date(f["DTPOSTED"]) if "DTPOSTED" in f else None
            try:
                amount = Decimal(f["TRNAMT"].strip()) if "TRNAMT" in f else None
            except InvalidOperation:
                amount = None
            if amount is not None and not amount.is_finite():
                amount = None
            fitid = f.get("FITID", "").strip() or None

            def unread(reason, d=date):
                doc.unread.append(UnreadLine(line, d, reason, account.id, fitid))

            if date is None:
                unread("no_date" if "DTPOSTED" not in f else "bad_date", None)
            elif amount is None:
                unread("bad_amount")
            elif fitid is None:
                unread("truncated")
            else:
                doc.transactions.append(Transaction(
                    account_id=account.id,
                    fitid=fitid,
                    is_debit=amount < 0 or f.get("TRNTYPE", "").strip().upper() == "DEBIT",
                    posted_at=date,
                    amount=int((abs(amount) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)),
                    name=unescape(f.get("NAME", "").strip()),
                    memo=unescape(f.get("MEMO", "").strip()),
                    currency=f.get("CURSYM", "").strip() or "RUB",
                    line=line,
                ))
    return doc
