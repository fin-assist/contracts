"""Category registry (spec FIN-106, 3.1): known bank categories with their directions, plus the system ones."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .util import slug

OWN_TRANSFER = "cat_own_transfer"
REFUND = "cat_refund"
UNCATEGORIZED = "cat_uncategorized"
TRANSFERS_NAME = "Переводы"
BANK_FEES_NAME = "Услуги банка"
UNCATEGORIZED_NAME = "Без категории"
NAME_LIMIT = 64


@dataclass
class Category:
    id: str
    name: str
    icon: str
    kinds: list
    system: bool = False
    assignable: bool = True
    note: str = None

    @property
    def is_expense(self) -> bool:
        """An expense bank category: a credit in it is a refund (spec 3.1, 3.3)."""
        return self.kinds == ["expense"]

    def api(self) -> dict:
        d = {"id": self.id, "name": self.name, "icon": self.icon, "is_system": self.system, "kinds": list(self.kinds),
             "assignable": self.assignable}
        if self.note:
            d["note"] = self.note
        return d


@dataclass
class Registry:
    by_name: dict = field(default_factory=dict)
    by_id: dict = field(default_factory=dict)

    @staticmethod
    def load(path: str) -> "Registry":
        r = Registry()
        with open(path, encoding="utf-8") as f:
            for c in json.load(f)["categories"]:
                r.add(Category(c["id"], c["name"], c["icon"], c["kinds"], c.get("system", False),
                               c.get("assignable", True), c.get("note")))
        return r

    def add(self, c: Category) -> None:
        if c.id in self.by_id and self.by_id[c.id].name != c.name:
            raise ValueError(f"duplicate category id {c.id}")
        self.by_name[c.name] = c
        self.by_id[c.id] = c

    def bank(self, memo: str) -> Category:
        """Category of a `MEMO`: empty or literal «Без категории» — the system one; a new one is added on first
        sight with both directions; the name is cut to 64 characters and is the key (spec 3.1, FIN-126)."""
        name = memo.strip()[:NAME_LIMIT]
        if not name or name == UNCATEGORIZED_NAME:
            return self.by_id[UNCATEGORIZED]
        c = self.by_name.get(name)
        if c is None:
            cid, n = "cat_" + slug(name), 2
            while cid in self.by_id:  # another name with the same slug
                cid, n = f"cat_{slug(name)}_{n}", n + 1
            c = Category(cid, name, "tag", ["expense", "income"])
            self.add(c)
        return c

    def get(self, cid: str) -> Category:
        return self.by_id[cid]

    def all(self) -> list:
        return list(self.by_id.values())
