"""FIN-413/411: rolling facts use coverage anchors and exact operation slices."""
import datetime as dt
import unittest
from zoneinfo import ZoneInfo

from refcalc.api import ApiError
from tests import test_spec_examples as spec


class RollingPeriods(unittest.TestCase):
    backend = spec.SpecExamples.backend
    block = spec.SpecExamples.block
    tile = spec.SpecExamples.tile

    def test_boundaries_follow_exclusive_calendar_shift_with_clamping(self):
        cases = [
            ("20260930", "rolling_year", "2025-10-01", "2026-10-01"),
            ("20260930", "rolling_quarter", "2026-07-01", "2026-10-01"),
            ("20261030", "rolling_year", "2025-10-31", "2026-10-31"),
            ("20260915", "rolling_quarter", "2026-06-16", "2026-09-16"),
            ("20261005", "rolling_quarter", "2026-07-06", "2026-10-06"),
            ("20240530", "rolling_quarter", "2024-02-29", "2024-05-31"),
            ("20230530", "rolling_quarter", "2023-02-28", "2023-05-31"),
            ("20240228", "rolling_year", "2023-02-28", "2024-02-29"),
            ("20261231", "rolling_quarter", "2026-10-01", "2027-01-01"),
        ]
        for last, kind, start, end in cases:
            with self.subTest(last=last, kind=kind):
                b = self.backend("2027-01-10T12:00:00+03:00")
                b.upload("a.ofx", spec.stmt("20220101", last, [("d", "20220102120000", -1, "Shop", "Продукты")]))
                s = b.analytics(kind)
                self.assertEqual(start + "T00:00:00+03:00", s["period"]["range"]["from"])
                self.assertEqual(end + "T00:00:00+03:00", s["period"]["range"]["to"])
                self.assertEqual(kind, s["params"]["date"])
                self.assertEqual({"previous": None, "next": None}, s["navigation"])
                self.assertFalse(s["period"]["is_current"])
                self.assertEqual([{ "key": kind, "range": s["period"]["range"], "coverage": "complete"}],
                                 b.periods(kind)["periods"])

    def test_exact_edges_refunds_modes_dedup_and_full_month_navigation(self):
        b = self.backend("2026-10-31T12:00:00+03:00")
        text = spec.stmt("20241001", "20261030", [
            ("old", "20241003120000", -100, "Old", "Продукты"),
            ("outside", "20251030120000", -100, "Outside", "Продукты"),
            ("start", "20251031000000", -10.01, "Shop", "Продукты"),
            ("salary", "20260101120000", 100, "Salary", "Зарплата"),
            ("person", "20261030120000", -5, "Person", "Переводы"),
            ("own", "20261030130000", -40, "Между своими счетами", "Переводы"),
            ("refund", "20261030140000", 2.01, "Shop", "Продукты"),
            ("end", "20261030235959", -3, "Last", "Продукты"),
            ("after", "20261031000000", -999, "Future", "Продукты"),
        ])
        b.upload("a.ofx", text)
        b.upload("duplicate.ofx", text)
        for mode, amount in (("with", 1600), ("without", 1100)):
            s = b.analytics("rolling_year", mode=mode)
            self.assertEqual(amount, self.tile(s, "expense")["value"])
            self.assertEqual(10000, self.tile(s, "income")["value"])
            self.assertEqual(10000 - amount, self.tile(s, "balance")["value"])
            self.assertEqual(amount // 365, self.tile(s, "daily_expense")["value"])
            chart = self.block(s, "monthly_chart")[0]["data"]
            points = chart["points"]
            self.assertEqual(13, len(points))
            self.assertEqual(amount, sum(p["value"] for p in points))
            self.assertEqual("2025-10-31T00:00:00+03:00", points[0]["range"]["from"])
            self.assertEqual("2026-10-31T00:00:00+03:00", points[-1]["range"]["to"])
            self.assertEqual("partial", points[0]["coverage"])
            self.assertEqual("2025-10", points[0]["action"]["date"])
            self.assertEqual("2026-10", points[-1]["action"]["date"])
            self.assertNotIn("highlight_index", chart)
            self.assertNotIn("forecast", [t["id"] for t in self.block(s, "tiles")[0]["data"]["tiles"]])
            self.assertFalse(any(x.get("id") in ("balance", "regular_payments") for x in s["blocks"]))
            rows = self.block(s, "expense_categories")[0]["data"]
            self.assertEqual(amount, rows["total"]["value"])
            self.assertEqual(s["period"]["range"]["from"], rows["total"]["action"]["filter"]["from"])
        self.assertGreater(self.tile(b.analytics("all_time"), "expense")["value"], 20000)

    def test_short_history_gaps_unknown_zero_and_no_month_threshold(self):
        b = self.backend("2026-10-31T12:00:00+03:00")
        b.upload("a.ofx", spec.stmt("20260820", "20260831", [("d", "20260821120000", -31, "Shop", "Продукты")]))
        b.upload("empty.ofx", spec.stmt("20261001", "20261030", []))
        s = b.analytics("rolling_quarter")
        self.assertEqual("2026-07-31T00:00:00+03:00", s["period"]["range"]["from"])
        self.assertEqual("2026-08-20T00:00:00+03:00", s["period"]["data_from"])
        self.assertEqual("partial", s["period"]["coverage"])
        self.assertEqual(3100 // 42, self.tile(s, "daily_expense")["value"])
        self.assertEqual([{"from": "2026-09-01T00:00:00+03:00", "to": "2026-10-01T00:00:00+03:00"}], s["period"]["gaps"])
        points = self.block(s, "monthly_chart")[0]["data"]["points"]
        self.assertEqual([None, 3100, None, 0], [p["value"] for p in points])
        self.assertEqual(["no_data", "partial", "no_data", "partial"], [p["coverage"] for p in points])
        self.assertNotIn("action", points[0])
        self.assertEqual("2026-10", points[-1]["action"]["date"])
        b.delete_upload("empty.ofx")
        short = b.analytics("rolling_quarter")
        self.assertEqual("ready", self.block(short, "monthly_chart")[0]["status"])
        self.assertEqual("2026-09-01T00:00:00+03:00", short["period"]["range"]["to"])
        b.delete_upload("a.ofx")
        self.assertEqual({"has_data": False}, b.analytics("rolling_quarter"))
        self.assertEqual({"periods": []}, b.periods("rolling_quarter"))

    def test_quarter_keeps_minor_units_and_opens_the_full_edge_month(self):
        b = self.backend("2026-10-31T12:00:00+03:00")
        b.upload("a.ofx", spec.stmt("20260601", "20261005", [
            ("outside", "20260705120000", -999, "Outside", "Продукты"),
            ("edge", "20260706000000", -0.01, "Edge", "Продукты"),
            ("middle", "20260801120000", -0.01, "Middle", "Продукты"),
            ("last", "20261005235959", -0.01, "Last", "Продукты")]))
        s = b.analytics("rolling_quarter")
        self.assertEqual(3, self.tile(s, "expense")["value"])
        self.assertEqual(0, self.tile(s, "daily_expense")["value"])
        points = self.block(s, "monthly_chart")[0]["data"]["points"]
        self.assertEqual([1, 1, 0, 1], [p["value"] for p in points])
        self.assertEqual("2026-07-06T00:00:00+03:00", points[0]["range"]["from"])
        self.assertEqual("2026-10-06T00:00:00+03:00", points[-1]["range"]["to"])
        action = points[0]["action"]
        month = b.analytics(action["period"], action["date"], s["params"]["transfer_mode"])
        self.assertEqual(99901, self.tile(month, "expense")["value"])
        self.assertEqual("2026-07-01T00:00:00+03:00", month["period"]["range"]["from"])
        self.assertEqual("2026-08-01T00:00:00+03:00", month["period"]["range"]["to"])

    def test_anchor_has_no_operation_or_today_dependency_and_daily_uses_today_ceiling(self):
        b = self.backend("2026-10-10T12:00:00+03:00")
        b.upload("a.ofx", spec.stmt("20261001", "20261030", [("d", "20261003120000", -31, "Shop", "Продукты")]))
        s = b.analytics("rolling_quarter")
        self.assertEqual("2026-10-31T00:00:00+03:00", s["period"]["range"]["to"])
        self.assertEqual(310, self.tile(s, "daily_expense")["value"])
        b.now = dt.datetime.fromisoformat("2026-11-01T12:00:00+03:00")
        later = b.analytics("rolling_quarter")
        self.assertEqual(s["period"]["range"], later["period"]["range"])
        self.assertEqual(3100 // 30, self.tile(later, "daily_expense")["value"])

    def test_time_zone_change_reanchors_dates_with_dst_offsets(self):
        b = self.backend("2026-11-01T12:00:00+03:00")
        b.upload("a.ofx", spec.stmt("20260701", "20261030", [("d", "20261030230000", -10, "Shop", "Продукты")]))
        for zone, start, end in (("Europe/Moscow", "2026-07-31T00:00:00+03:00", "2026-10-31T00:00:00+03:00"),
                                 ("Europe/Berlin", "2026-07-31T00:00:00+02:00", "2026-10-31T00:00:00+01:00"),
                                 ("Asia/Tokyo", "2026-08-01T00:00:00+09:00", "2026-11-01T00:00:00+09:00")):
            b.zone = b.ledger.zone = ZoneInfo(zone)
            s = b.analytics("rolling_quarter")
            self.assertEqual({"from": start, "to": end}, s["period"]["range"])

    def test_keys_are_singletons_and_cannot_be_mixed_or_used_as_calendar_anchors(self):
        b = self.backend("2026-10-31T12:00:00+03:00")
        b.upload("a.ofx", spec.stmt("20261001", "20261030", [("d", "20261003120000", -1, "Shop", "Продукты")]))
        for kind in ("rolling_year", "rolling_quarter"):
            self.assertEqual(b.analytics(kind), b.analytics(kind, kind))
            for key in ("2026", "2026-10", "all_time", "rolling_quarter" if kind == "rolling_year" else "rolling_year"):
                with self.subTest(kind=kind, key=key), self.assertRaises(ApiError) as caught:
                    b.analytics(kind, key)
                self.assertEqual((400, "VALIDATION_ERROR"), (caught.exception.status, caught.exception.code))
            with self.assertRaises(ApiError):
                b.analytics("month", kind)

    def test_only_empty_statements_preserve_empty_screen_with_coverage_period_list(self):
        b = self.backend("2026-10-31T12:00:00+03:00")
        b.upload("empty.ofx", spec.stmt("20261001", "20261030", []))
        self.assertEqual({"has_data": False}, b.analytics("rolling_year"))
        self.assertEqual(1, len(b.periods("rolling_year")["periods"]))
