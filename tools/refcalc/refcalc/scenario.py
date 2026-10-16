"""Runs fixtures/calc/scenario.json through the reference calculation and writes or checks the expected responses.

    python3 -m refcalc.scenario ../../fixtures/calc --write   # regenerate expected/*.json
    python3 -m refcalc.scenario ../../fixtures/calc --check   # fail if a committed file differs
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit
from zoneinfo import ZoneInfo

from .api import ApiError, Backend
from .compare import redact
from .registry import Registry
from .util import parse_iso


def dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def filter_query(f: dict) -> dict:
    """operations_filter → query parameters of 4.1 (`selection_name` is for display only)."""
    return {k: v for k, v in f.items() if k != "selection_name" and v is not None}


class Runner:
    def __init__(self, root: str):
        self.root = root
        with open(os.path.join(root, "scenario.json"), encoding="utf-8") as f:
            self.scenario = json.load(f)
        self.backend = Backend(Registry.load(os.path.join(root, "categories.json")),
                               ZoneInfo(self.scenario["time_zone"]))
        self.responses = {}
        with open(os.path.join(root, "categories.json"), encoding="utf-8") as f:
            self.known = frozenset(c["id"] for c in json.load(f)["categories"])
        self.raw = {}  # expect path → (schema kind, response before redaction)

    def get(self, request: str):
        url = urlsplit(request)
        q = dict(parse_qsl(url.query))
        b = self.backend
        if url.path == "/v1/operations":
            if not q or set(q) == {"before"}:
                return b.feed(q.get("before"))
            if "all_time" in q:
                q["all_time"] = q["all_time"] == "true"
            return b.search(q)
        if url.path == "/v1/categories":
            return b.categories()
        if url.path == "/v1/analytics":
            return b.analytics(q.get("period", "month"), q.get("date"), q.get("transfer_mode", "with"))
        if url.path == "/v1/analytics/periods":
            return b.periods(q.get("period", "month"))
        raise ValueError(f"unknown request {request}")

    def resolve(self, ref: dict):
        """{"step": "<name>", "path": ["a", 0, {"id": "x"}, "b"]} → a value from an earlier response; {"id": …} picks
        the first element of an array with that `id` (a block of 6.1)."""
        v = self.responses[ref["step"]]
        for p in ref["path"]:
            v = next(x for x in v if x.get("id") == p["id"]) if isinstance(p, dict) else v[p]
        return v

    def run(self):
        out = {}
        for step in self.scenario["steps"]:
            if "now" in step:
                self.backend.now = parse_iso(step["now"])
            do = step["do"]
            try:
                request = None
                if do == "upload":
                    with open(os.path.join(self.root, step["file"]), encoding="utf-8") as f:
                        resp = self.backend.upload(os.path.basename(step["file"]), f.read())
                elif do == "get":
                    request = step.get("request")
                    if "filter_from" in step:
                        f = filter_query(self.resolve(step["filter_from"]))
                        request = "GET /v1/operations?" + urlencode(f)
                    resp = self.get(request.split(" ", 1)[1])
                elif do == "change_category":
                    resp = self.backend.change_category(step["operation"]["account"], step["operation"]["fitid"],
                                                        step["category_id"])
                elif do == "dismiss_regular_payment":
                    # As an implementation does it: the payment_id of the row with this title in the card.
                    screen = self.get(step["request"].split(" ", 1)[1])
                    card = next(b for b in screen["blocks"] if b.get("id") == "regular_payments")
                    row = next(r for r in card["data"]["items"] if r["title"] == step["title"])
                    self.backend.dismiss(row["button"]["payment_id"])
                    resp = None
                elif do == "delete_upload":
                    self.backend.delete_upload(os.path.basename(step["file"]))
                    resp = None
                else:
                    raise ValueError(f"unknown step {do}")
            except ApiError as e:
                resp = {"status": e.status, "error": {"code": e.code}}
            self.responses[step["name"]] = resp
            if step.get("expect"):
                out[step["expect"]] = redact(resp, known=self.known)
                kind = do if do != "get" else urlsplit((request or "").split(" ", 1)[1]).path
                self.raw[step["expect"]] = (kind, resp)
        return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("root")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", action="store_true")
    g.add_argument("--check", action="store_true")
    p.add_argument("--openapi", help="validate the responses against this openapi.yaml")
    args = p.parse_args(argv)
    runner = Runner(args.root)
    expected = runner.run()
    if args.openapi:
        from .schema import SchemaCheck
        check = SchemaCheck(args.openapi)
        invalid = 0
        for rel, (kind, resp) in runner.raw.items():
            for e in check.errors(kind, resp):
                invalid += 1
                print(f"{rel}: {e}")
        if invalid:
            print(f"{invalid} schema errors")
            return 1
        print(f"schema OK: {len(runner.raw)} responses")
    stale = []
    for rel, body in expected.items():
        path = os.path.join(args.root, rel)
        text = dump(body)
        if args.write:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        else:
            try:
                with open(path, encoding="utf-8") as f:
                    if f.read() != text:
                        stale.append(rel)
            except FileNotFoundError:
                stale.append(rel)
    on_disk = {os.path.join("expected", n) for n in os.listdir(os.path.join(args.root, "expected"))}
    extra = sorted(on_disk - set(expected))
    if args.check:
        for rel in stale:
            print(f"differs from the reference calculation: {rel}")
        for rel in extra:
            print(f"not produced by the scenario: {rel}")
        if stale or extra:
            print("regenerate: python3 -m refcalc.scenario fixtures/calc --write (from tools/refcalc)")
            return 1
        print(f"expected responses OK: {len(expected)} files")
    else:
        for rel in extra:
            os.remove(os.path.join(args.root, rel))
        print(f"written {len(expected)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
