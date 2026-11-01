"""FIN-418: whole-history navigation, explicit help, and inclusive download dates."""
import copy
import datetime as dt
import unittest
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from refcalc.api import ApiError
from refcalc.schema import SchemaCheck
from tests import test_spec_examples as spec
from tests.test_analytics_increment import ROOT

SUPPORTS = "heading@1,text@1,rows@1,chart@1,tile_group@1,ranked_list@1,insight_card@1"


class ReviewFeedback(unittest.TestCase):
    backend = spec.SpecExamples.backend
    block = spec.SpecExamples.block
    tile = spec.SpecExamples.tile

    def history(self):
        b = self.backend("2026-10-31T12:00:00+03:00")
        b.upload("old", spec.stmt("20251230", "20251231", [
            ("old", "20251230120000", -100, "Shop", "Продукты")]))
        b.upload("zero", spec.stmt("20260101", "20260131", []))
        b.upload("new", spec.stmt("20261001", "20261030", [
            ("new", "20261003120000", -200, "Shop", "Продукты"),
            ("salary", "20261004120000", 500, "Salary", "Зарплата"),
            ("person", "20261005120000", -50, "Person", "Переводы")]))
        return b

    def test_every_tab_and_repeated_month_selection_keeps_whole_history(self):
        b = self.history()
        for mode in ("with", "without"):
            expected = self.block(b.analytics("all_time", mode=mode), "monthly_chart")[0]["data"]["points"]
            for period, date in (("month", "2026-10"), ("quarter", "2026-Q4"), ("year", "2026"),
                                 ("all_time", None), ("rolling_year", None), ("rolling_quarter", None),
                                 ("month", "2025-12"), ("month", "2026-10"), ("month", "2025-12")):
                with self.subTest(period=period, date=date, mode=mode):
                    plain = b.analytics(period, date, mode)
                    screen = b.analytics(period, date, mode, SUPPORTS)
                    chart = self.block(screen, "monthly_chart")[0]
                    self.assertEqual(1, chart["v"])
                    self.assertEqual(expected, chart["data"]["points"])
                    self.assertIn("всей загруженной истории", chart["help_text"])
                    self.assertEqual(plain["period"], screen["period"])
                    self.assertEqual(self.tile(plain, "expense")["value"], self.tile(screen, "expense")["value"])
                    self.assertEqual(self.block(plain, "balance"), self.block(screen, "balance"))
                    if period == "month":
                        point = chart["data"]["points"][chart["data"]["highlight_index"]]
                        self.assertEqual(date, point["action"]["date"])
                    else:
                        self.assertNotIn("highlight_index", chart["data"])
                    for note in chart["data"]["notes"]:
                        self.assertIn(screen["params"]["date"], note["text"])
                    self.assertEqual([], SchemaCheck(ROOT + "/openapi/openapi.yaml").errors("/v1/analytics", screen))
            points = expected
            self.assertEqual(0, points[1]["value"])
            self.assertEqual("2026-01", points[1]["action"]["date"])
            self.assertIsNone(points[2]["value"])
            self.assertNotIn("action", points[2])

    def test_chart_has_one_partial_month_without_gate(self):
        b = self.backend("2026-10-10T12:00:00+03:00")
        uploaded = b.upload("one", spec.stmt("20261001", "20261010", [("x", "20261003120000", -1, "Shop", "Продукты")]))
        feature = next(f for f in uploaded["coverage"]["features"] if f["feature"] == "monthly_chart")
        self.assertTrue(feature["open"])
        self.assertTrue(feature["opened_now"])
        self.assertEqual(1, len(self.block(b.analytics("month"), "monthly_chart")))
        for period in ("month", "quarter", "year", "all_time", "rolling_year", "rolling_quarter"):
            screen = b.analytics(period, supports=SUPPORTS)
            chart = self.block(screen, "monthly_chart")[0]
            self.assertEqual("ready", chart["status"])
            self.assertEqual(1, len(chart["data"]["points"]))
            self.assertEqual("partial", chart["data"]["points"][0]["coverage"])
            expected = [] if period == "month" else ["primary", "muted"] if period == "year" else ["primary"]
            self.assertEqual(expected, [n["tone"] for n in chart["data"]["notes"]])
            if period in ("month", "quarter"):
                balance = self.block(screen, "balance")[0]
                self.assertEqual([], balance["data"]["points"])
                self.assertEqual(-100, balance["data"]["summary"]["value"])
                self.assertIn("help_text", balance)
                self.assertNotIn("caption", balance["data"])
        b.delete_upload("one")
        self.assertEqual({"has_data": False, "calculation": b.recalculation.state()}, b.analytics("month", supports=SUPPORTS))

    def test_history_chart_does_not_change_selected_rolling_facts(self):
        b = self.history()
        screen = b.analytics("rolling_year")
        points = self.block(screen, "monthly_chart")[0]["data"]["points"]
        self.assertEqual("2025-10-31T00:00:00+03:00", screen["period"]["range"]["from"])
        self.assertEqual("2025-12-01T00:00:00+03:00", points[0]["range"]["from"])
        self.assertEqual(35000, self.tile(screen, "expense")["value"])
        self.assertEqual(points, self.block(b.analytics("month", "2025-12"), "monthly_chart")[0]["data"]["points"])

    def test_forecast_keeps_main_content_and_moves_only_method_and_coverage(self):
        b = self.history()
        screen = b.analytics("year", "2026", supports=SUPPORTS)
        forecast = self.tile(screen, "forecast")
        self.assertEqual("Прогноз расходов на год", forecast["title"])
        self.assertEqual("К концу 2026 года", forecast["caption"])
        self.assertEqual("Нужно 12 полных месяцев, есть 1", forecast["lock"]["text"])
        self.assertIn("последние 12 полных месяцев", forecast["help_text"])
        self.assertIn("Учтены полные месяцы", forecast["help_text"])
        daily = self.tile(screen, "daily_expense")
        self.assertIn("value", daily)
        self.assertEqual("сравнение", daily["caption"])
        self.assertIn("пропуски", daily["help_text"])

    def test_optional_help_is_nonnullable_and_absence_remains_valid(self):
        check = SchemaCheck(ROOT + "/openapi/openapi.yaml")
        screen = self.history().analytics("year", "2026", supports=SUPPORTS)
        self.assertEqual([], check.errors("/v1/analytics", screen))
        invalid = copy.deepcopy(screen)
        self.block(invalid, "monthly_chart")[0]["help_text"] = None
        self.assertTrue(check.errors("/v1/analytics", invalid))
        invalid = copy.deepcopy(screen)
        self.tile(invalid, "forecast")["help_text"] = []
        self.assertTrue(check.errors("/v1/analytics", invalid))
        for block in screen["blocks"]:
            block.pop("help_text", None)
            for tile in block.get("data", {}).get("tiles", []):
                tile.pop("help_text", None)
        self.assertEqual([], check.errors("/v1/analytics", screen))

    def test_download_default_and_selected_range_are_independent_of_loaded(self):
        b = self.backend("2024-02-29T12:00:00+03:00")
        self.assertEqual({"from": "2023-02-28T00:00:00+03:00", "to": "2024-03-01T00:00:00+03:00"},
                         b.import_config()["suggested_period"])
        b = self.history()
        self.assertEqual("2026-10-05T00:00:00+03:00", b.import_config()["suggested_period"]["from"])
        for start, end, exclusive in (("2025-12-31", "2026-01-01", "2026-01-02"),
                                      ("2024-02-29", "2024-02-29", "2024-03-01"),
                                      ("2026-10-31", "2026-10-31", "2026-11-01")):
            response = b.import_config(start, end)
            self.assertEqual({"from": start + "T00:00:00+03:00", "to": exclusive + "T00:00:00+03:00"},
                             response["suggested_period"])
            query = parse_qs(urlsplit(response["download_url"]).query)
            for field, key in (("from", "start"), ("to", "end")):
                at = dt.datetime.fromisoformat(response["suggested_period"][field])
                self.assertEqual(int(at.timestamp() * 1000), int(query[key][0]))
            self.assertEqual([], SchemaCheck(ROOT + "/openapi/openapi.yaml").errors("/v1/statements/config", response))
        self.assertEqual(4, b.import_config()["loaded"]["operation_count"])

    def test_download_respects_profile_today_and_dst_boundary_offsets(self):
        b = self.backend("2026-03-09T12:00:00+00:00")
        b.zone = ZoneInfo("America/New_York")
        r = b.import_config("2026-03-08", "2026-03-08")["suggested_period"]
        self.assertEqual({"from": "2026-03-08T00:00:00-05:00", "to": "2026-03-09T00:00:00-04:00"}, r)
        self.assertEqual(dt.timedelta(hours=23), dt.datetime.fromisoformat(r["to"]) - dt.datetime.fromisoformat(r["from"]))
        b.now = dt.datetime.fromisoformat("2026-03-09T02:00:00+00:00")
        with self.assertRaises(ApiError):
            b.import_config("2026-03-09", "2026-03-09")

    def test_download_errors_are_canonical_and_field_specific(self):
        b = self.history()
        cases = [(None, "2026-01-01", "period_from", "REQUIRED"),
                 ("2026-01-01", None, "period_to", "REQUIRED"),
                 ("", "2026-01-01", "period_from", "INVALID_FORMAT"),
                 ("2026-01-01", "2026-02-30", "period_to", "INVALID_FORMAT"),
                 ("2026-1-01", "2026-01-01", "period_from", "INVALID_FORMAT"),
                 ("0000-01-01", "2026-01-01", "period_from", "INVALID_FORMAT"),
                 ("2026-01-02", "2026-01-01", "period_from", "OUT_OF_RANGE"),
                 ("2026-01-01", "2026-11-01", "period_to", "OUT_OF_RANGE"),
                 ("0001-01-01", "2026-01-01", "period_from", "OUT_OF_RANGE")]
        for start, end, field, code in cases:
            with self.subTest(start=start, end=end), self.assertRaises(ApiError) as raised:
                b.import_config(start, end)
            e = raised.exception
            self.assertEqual((400, "VALIDATION_ERROR"), (e.status, e.code))
            self.assertEqual([{"field": field, "code": code}], e.details)
        b.zone = ZoneInfo("Pacific/Apia")
        with self.assertRaises(ApiError) as raised:
            b.import_config("2011-12-30", "2011-12-30")
        self.assertEqual([{"field": "period_to", "code": "OUT_OF_RANGE"}], raised.exception.details)

    def test_download_query_preserves_empty_and_rejects_duplicate_dates(self):
        from refcalc.scenario import Runner
        runner = Runner.__new__(Runner)
        runner.backend = self.history()
        for query, field, code in (
            ("period_from=&period_to=2026-10-31", "period_from", "INVALID_FORMAT"),
            ("period_from=2026-10-01&period_from=2026-10-02&period_to=2026-10-31", "period_from", "INVALID_FORMAT"),
            ("period_from=2026-10-01&period_to=2026-10-30&period_to=2026-10-31", "period_to", "INVALID_FORMAT"),
            ("period_from=2026-10-01&period_from=2026-10-02", "period_from", "INVALID_FORMAT"),
        ):
            with self.subTest(query=query), self.assertRaises(ApiError) as caught:
                runner.get("/v1/statements/config?" + query)
            self.assertEqual([{"field": field, "code": code}], caught.exception.details)
        runner.backend.now = dt.datetime(9999, 12, 31, 12, tzinfo=dt.timezone.utc)
        with self.assertRaises(ApiError) as caught:
            runner.backend.import_config("9999-12-30", "9999-12-31")
        self.assertEqual([{"field": "period_to", "code": "OUT_OF_RANGE"}], caught.exception.details)

    def test_quarter_forecast_uses_common_auxiliary_role_without_formula_change(self):
        b = self.history()
        forecast = self.tile(b.analytics("quarter", "2026-Q4"), "forecast")
        self.assertEqual("Прогноз расходов", forecast["title"])
        self.assertEqual("locked", forecast["status"])
        self.assertEqual("Нужно 3 полных месяца, есть 1", forecast["lock"]["text"])
        self.assertEqual((3, 1), (forecast["lock"]["required"], forecast["lock"]["available"]))
        self.assertIn("Учтены полные месяцы", forecast["help_text"])
        self.assertEqual("к концу периода", forecast["caption"])

    def test_download_skipped_start_normalizes_but_keeps_nonempty_range(self):
        b = self.history()
        b.zone = ZoneInfo("Pacific/Apia")
        result = b.import_config("2011-12-30", "2011-12-31")
        self.assertEqual({"from": "2011-12-31T00:00:00+14:00", "to": "2012-01-01T00:00:00+14:00"},
                         result["suggested_period"])
        start = dt.datetime(2011, 12, 30, tzinfo=b.zone).astimezone(dt.timezone.utc)
        end = dt.datetime(2012, 1, 1, tzinfo=b.zone).astimezone(dt.timezone.utc)
        self.assertEqual(start, dt.datetime.fromisoformat(result["suggested_period"]["from"]))
        self.assertEqual(end, dt.datetime.fromisoformat(result["suggested_period"]["to"]))
        self.assertGreater(end, start)

    def test_download_partial_midnight_gap_uses_first_real_instant(self):
        b = self.history()
        b.zone = ZoneInfo('America/Toronto')
        result = b.import_config('1919-03-31', '1919-03-31')
        start = dt.datetime.fromisoformat(result['suggested_period']['from'])
        self.assertEqual('1919-03-31T00:30:00-04:00', start.isoformat())
        instant = start.astimezone(dt.timezone.utc)
        self.assertEqual(dt.date(1919, 3, 30), (instant - dt.timedelta(seconds=1)).astimezone(b.zone).date())
        self.assertEqual('1919-04-01T00:00:00-04:00', result['suggested_period']['to'])
        query = parse_qs(urlsplit(result['download_url']).query)
        self.assertEqual(int(instant.timestamp() * 1000), int(query['start'][0]))
