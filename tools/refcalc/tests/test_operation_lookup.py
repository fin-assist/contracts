"""Lookup of a record by `occurred_at` and `id` in 4.3 (api.md 4.2, 4.3; FIN-202)."""
import datetime as dt
import os
import unittest

from refcalc.api import ApiError, Backend
from refcalc.registry import Registry

from .test_spec_examples import MSK, ROOT, SAV, stmt


def new_backend() -> Backend:
    b = Backend(Registry.load(os.path.join(ROOT, "categories.json")), MSK)
    b.now = dt.datetime.fromisoformat("2026-09-26T12:00:00+03:00")
    return b


class OperationLookup(unittest.TestCase):
    def backend_with(self, trns: list):
        b = new_backend()
        b.upload("a.ofx", stmt("20260901", "20260925", trns))
        return b

    def assert_error(self, call, status: int, code: str):
        with self.assertRaises(ApiError) as e:
            call()
        self.assertEqual((e.exception.status, e.exception.code), (status, code))

    def test_matching_time_changes_category(self):
        b = self.backend_with([("d1", "20260910120000", -100, "Кафе", "Рестораны")])
        rec = b.feed()["items"][0]
        got = b.patch_operation(rec["id"], rec["occurred_at"], "cat_gifts")
        self.assertEqual([(i["id"], i["category_id"], i["is_category_manual"]) for i in got["items"]],
                         [(rec["id"], "cat_gifts", True)])

    def test_same_moment_in_another_offset_is_a_match(self):
        b = self.backend_with([("d1", "20260910120000", -100, "Кафе", "Рестораны")])
        rec = b.feed()["items"][0]
        self.assertEqual(rec["occurred_at"], "2026-09-10T12:00:00+03:00")
        got = b.patch_operation(rec["id"], "2026-09-10T09:00:00Z", "cat_gifts")
        self.assertEqual(got["items"][0]["category_id"], "cat_gifts")

    def test_wrong_time_is_a_miss_not_an_error(self):
        # The time may have changed since the client got the list (hold posted, statement re-imported): the record
        # is found by `id` in any year, and the response carries its current `occurred_at`.
        b = self.backend_with([("d1", "20260910120000", -100, "Кафе", "Рестораны")])
        rec = b.feed()["items"][0]
        got = b.patch_operation(rec["id"], "2024-01-01T00:00:00+03:00", "cat_gifts")
        self.assertEqual([(i["id"], i["occurred_at"], i["category_id"]) for i in got["items"]],
                         [(rec["id"], "2026-09-10T12:00:00+03:00", "cat_gifts")])

    def test_pair_is_found_by_the_debit_time(self):
        b = new_backend()
        b.upload("a.ofx", stmt("20260901", "20260925", [("d1", "20260918093000", -20000, "Между своими счетами", "Переводы")], SAV, "SAVINGS"))
        b.upload("b.ofx", stmt("20260901", "20260925", [("c1", "20260918093100", 20000, "Иван", "Переводы")]))
        pair = b.feed()["items"][0]
        self.assertEqual((pair["kind"], pair["occurred_at"]), ("own_transfer", "2026-09-18T09:30:00+03:00"))
        got = b.patch_operation(pair["id"], pair["occurred_at"], "cat_transfers")
        self.assertEqual(got["replaced_id"], pair["id"])
        self.assertEqual(len(got["items"]), 2)

    def test_missing_or_malformed_time_is_a_validation_error(self):
        b = self.backend_with([("d1", "20260910120000", -100, "Кафе", "Рестораны")])
        rec = b.feed()["items"][0]
        for value in (None, "", "yesterday", "2026-09-10", "2026-09-10T12:00:00"):
            with self.subTest(occurred_at=value):
                self.assert_error(lambda: b.patch_operation(rec["id"], value, "cat_gifts"), 400, "VALIDATION_ERROR")
        self.assertEqual(b.feed()["items"][0]["category_id"], rec["category_id"])

    def test_unknown_id_is_not_found_at_any_time(self):
        b = self.backend_with([("d1", "20260910120000", -100, "Кафе", "Рестораны")])
        rec = b.feed()["items"][0]
        self.assert_error(lambda: b.patch_operation("01a10812-0000-7000-8000-000000000000", rec["occurred_at"], "cat_gifts"),
                          404, "NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
