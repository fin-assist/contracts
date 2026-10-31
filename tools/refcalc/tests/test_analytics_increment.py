"""FIN-409: the same accounting facts across all-time selection and block versions."""
import copy
import datetime as dt
import os
import unittest

from refcalc.api import ApiError
from refcalc.coverage import Coverage, block_data_schema
from refcalc.schema import SchemaCheck
from tests import test_spec_examples as spec

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
SUPPORTS = "heading@1,text@1,rows@1,chart@1,tile_group@2,ranked_list@1,insight_card@1"


class AnalyticsIncrement(unittest.TestCase):
    backend = spec.SpecExamples.backend
    block = spec.SpecExamples.block
    tile = spec.SpecExamples.tile

    def ledger(self):
        b = self.backend("2026-10-31T12:00:00+03:00")
        b.upload("old.ofx", spec.stmt("20251230", "20251231", [
            ("old", "20251230120000", -100, "Shop", "Продукты")]))
        b.upload("new.ofx", spec.stmt("20261001", "20261030", [
            ("new", "20261003120000", -200, "Shop", "Продукты"),
            ("salary", "20261004120000", 500, "Salary", "Зарплата"),
            ("person", "20261005120000", -50, "Person", "Переводы"),
            ("own", "20261006120000", -40, "Между своими счетами", "Переводы")]))
        return b

    def test_all_time_unites_years_keeps_modes_gaps_and_zero_month_actions(self):
        b = self.ledger()
        for mode, expected in (("with", 35000), ("without", 30000)):
            s = b.analytics("all_time", None, mode)
            self.assertEqual({"period": "all_time", "date": "all_time", "transfer_mode": mode}, s["params"])
            self.assertEqual(expected, self.tile(s, "expense")["value"])
            self.assertEqual(expected // 32, self.tile(s, "daily_expense")["value"])
            self.assertEqual(50000, self.tile(s, "income")["value"])
            self.assertEqual(50000 - expected, self.tile(s, "balance")["value"])
            self.assertEqual({"previous": None, "next": None}, s["navigation"])
            self.assertFalse(s["period"]["is_current"])
            self.assertEqual("partial", s["period"]["coverage"])
            self.assertEqual("2025-12-30T00:00:00+03:00", s["period"]["range"]["from"])
            self.assertEqual("2026-10-31T00:00:00+03:00", s["period"]["range"]["to"])
            self.assertEqual(s["period"]["range"], {"from": s["state"]["data_from"], "to": s["state"]["data_to"]})
            chart = self.block(s, "monthly_chart")[0]["data"]
            self.assertEqual(11, len(chart["points"]))
            self.assertNotIn("highlight_index", chart)
            self.assertEqual({"type": "select_analytics_period", "period": "month", "date": "2025-12"},
                             chart["points"][0]["action"])
            missing = chart["points"][1]
            self.assertIsNone(missing["value"])
            self.assertEqual("no_data", missing["coverage"])
            self.assertNotIn("action", missing)
            self.assertFalse(any(x.get("id") in ("balance", "regular_payments") for x in s["blocks"]))
            self.assertNotIn("forecast", [t["id"] for t in self.block(s, "tiles")[0]["data"]["tiles"]])

    def test_all_time_short_current_coverage_is_complete_but_month_point_is_partial(self):
        b = self.backend("2026-10-10T12:00:00+03:00")
        b.upload("short.ofx", spec.stmt("20261001", "20261010", [
            ("d", "20261003120000", -10, "Shop", "Продукты")]))
        s = b.analytics("all_time")
        self.assertEqual("complete", s["period"]["coverage"])
        listed = b.periods("all_time")["periods"]
        self.assertEqual([{"key": "all_time", "range": s["period"]["range"], "coverage": "complete"}], listed)
        chart = self.block(s, "monthly_chart")[0]
        self.assertEqual("ready", chart["status"])
        self.assertEqual("partial", chart["data"]["points"][0]["coverage"])
        self.assertFalse(s["period"]["is_current"])

    def test_all_time_has_no_twelve_month_truncation_or_year_ambiguity(self):
        b = self.ledger()
        b.upload("older.ofx", spec.stmt("20241001", "20241031", [
            ("older", "20241003120000", -1, "Shop", "Продукты")]))
        points = self.block(b.analytics("all_time"), "monthly_chart")[0]["data"]["points"]
        self.assertEqual(25, len(points))
        self.assertEqual("2024-10", points[0]["action"]["date"])
        self.assertEqual("2026-10", points[-1]["action"]["date"])

    def test_all_time_deletion_updates_boundaries_totals_and_final_empty_state(self):
        b = self.ledger()
        b.delete_upload("old.ofx")
        s = b.analytics("all_time", "all_time", "without")
        self.assertEqual("2026-10-01T00:00:00+03:00", s["period"]["range"]["from"])
        self.assertEqual(20000, self.tile(s, "expense")["value"])
        b.delete_upload("new.ofx")
        self.assertEqual({"has_data": False}, b.analytics("all_time"))
        self.assertEqual({"periods": []}, b.periods("all_time"))

    def test_all_time_empty_statement_does_not_fabricate_zero_fact(self):
        b = self.backend("2026-10-10T12:00:00+03:00")
        b.upload("empty.ofx", spec.stmt("20261001", "20261010", []))
        self.assertEqual({"has_data": False}, b.analytics("all_time"))

    def test_all_time_refund_uses_original_accounting(self):
        b = self.backend("2026-10-10T12:00:00+03:00")
        b.upload("r.ofx", spec.stmt("20251201", "20261010", [
            ("d", "20260930120000", -100, "Ozon", "Маркетплейсы"),
            ("r", "20261003120000", 40, "Ozon", "Маркетплейсы")]))
        s = b.analytics("all_time")
        self.assertEqual(6000, self.tile(s, "expense")["value"])
        self.assertEqual(0, self.tile(s, "income")["value"])
        row = self.block(s, "expense_categories")[0]["data"]["items"][0]
        self.assertEqual(6000, row["value"])

    def test_all_time_key_is_not_a_calendar_key(self):
        b = self.ledger()
        for period, key in (("all_time", "2026"), ("all_time", "2026-10"), ("month", "all_time")):
            with self.subTest(period=period, key=key), self.assertRaises(ApiError) as caught:
                b.analytics(period, key)
            self.assertEqual((400, "VALIDATION_ERROR"), (caught.exception.status, caught.exception.code))

    def test_tile_v2_negotiates_and_preserves_values_without_flattening_groups(self):
        b = self.ledger()
        for period, key in (("month", "2026-10"), ("quarter", "2026-Q4"), ("year", "2026"), ("all_time", "all_time")):
            old = b.analytics(period, key)
            new = b.analytics(period, key, supports=SUPPORTS)
            old_tiles = self.block(old, "tiles")[0]
            grouped = self.block(new, "tiles")[0]
            self.assertEqual(1, old_tiles["v"])
            self.assertEqual(2, grouped["v"])
            self.assertNotIn("tiles", grouped["data"])
            groups = grouped["data"]["groups"]
            self.assertEqual(["expense", "income", "balance"], [t["id"] for t in groups[0]["tiles"]])
            self.assertEqual("Дополнительные показатели", groups[1]["title"])
            self.assertEqual("Средние расходы в день", groups[1]["tiles"][0]["title"])
            self.assertIn("не позже сегодня", groups[1]["tiles"][0]["caption"])
            self.assertEqual({t["id"]: t.get("value") for t in old_tiles["data"]["tiles"]},
                             {t["id"]: t.get("value") for g in groups for t in g["tiles"]})
            self.assertNotIn("caption", groups[0])

    def test_daily_keeps_today_ceiling_and_gaps_out_of_denominator(self):
        b = self.backend("2026-10-10T12:00:00+03:00")
        b.upload("future.ofx", spec.stmt("20261001", "20261031", [
            ("d", "20261003120000", -31, "Shop", "Продукты")]))
        s = b.analytics("all_time", supports=SUPPORTS)
        daily = self.block(s, "tiles")[0]["data"]["groups"][1]["tiles"][0]
        self.assertEqual(310, daily["value"])
        self.assertIn("не позже сегодня", daily["caption"])
        self.assertEqual("complete", s["period"]["coverage"])
        self.assertEqual("complete", b.periods("all_time")["periods"][0]["coverage"])

    def test_v2_no_data_is_not_a_list_of_zero_tiles(self):
        s = self.ledger().analytics("month", "2026-01", supports=SUPPORTS)
        tiles = self.block(s, "tiles")[0]
        self.assertEqual((2, "none", {"groups": []}), (tiles["v"], tiles["status"], tiles["data"]))

    def test_supports_empty_higher_duplicate_and_malformed(self):
        b = self.ledger()
        for supports, version in ((None, 1), ("", 1), ("tile_group@1", 1), ("tile_group@2", 2),
                                  ("tile_group@99", 2), ("tile_group@2,tile_group@1", 2)):
            self.assertEqual(version, self.block(b.analytics("all_time", supports=supports), "tiles")[0]["v"])
        for supports in ("tile_group", "tile_group@abc", "@2", "tile_group@0", "tile_group@2147483648", "tile_group@2,"):
            with self.subTest(supports=supports), self.assertRaises(ApiError) as caught:
                b.analytics("all_time", supports=supports)
            self.assertEqual(400, caught.exception.status)
        self.assertEqual([], b.analytics("all_time", supports="future@3")["blocks"])

    def test_only_monthly_expense_chart_gets_actions_including_zero_values(self):
        b = self.backend("2026-10-31T12:00:00+03:00")
        b.upload("a.ofx", spec.stmt("20260801", "20261030", [
            ("i", "20260803120000", 20, "Salary", "Зарплата")]))
        s = b.analytics("month", "2026-10")
        expense = self.block(s, "monthly_chart")[0]["data"]["points"]
        zero = next(p for p in expense if p["range"]["from"].startswith("2026-09"))
        self.assertEqual(0, zero["value"])
        self.assertEqual("2026-09", zero["action"]["date"])
        self.assertFalse(any("action" in p for p in self.block(s, "balance")[0]["data"]["points"]))

    def test_schema_and_coverage_choose_exact_tile_version(self):
        schema = SchemaCheck(os.path.join(ROOT, "openapi/openapi.yaml"))
        b = self.ledger()
        s = b.analytics("all_time", supports=SUPPORTS)
        self.assertEqual([], schema.errors("/v1/analytics", s))
        changed = copy.deepcopy(s)
        self.block(changed, "tiles")[0]["data"] = {"tiles": []}
        self.assertTrue(schema.errors("/v1/analytics", changed))
        self.block(changed, "tiles")[0]["v"] = 1
        self.assertEqual([], schema.errors("/v1/analytics", changed))
        self.assertIsNone(block_data_schema({"type": "tile_group", "v": 99}))
        cov = Coverage(os.path.join(ROOT, "openapi/openapi.yaml"))
        cov.observe("Analytics", s)
        cov.observe("Analytics", b.analytics("all_time"))
        cov.collect("Analytics")
        self.assertIn("TileGroupDataV2.groups", cov.observed)
        self.assertIn("TileGroupData.tiles", cov.observed)
        self.assertIn("ChartPoint.action", cov.observed)
        self.assertIn("BlockAction.period", cov.observed)
        self.assertIn("BlockAction.date", cov.observed)
