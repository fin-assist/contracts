"""FIN-408: actual expenses coexist with forecasts; explanation follows real coverage."""
import unittest

from tests import test_spec_examples as spec

stmt = spec.stmt


class ForecastFacts(unittest.TestCase):
    backend = spec.SpecExamples.backend
    screen = spec.SpecExamples.screen
    block = spec.SpecExamples.block
    tile = spec.SpecExamples.tile

    def test_current_fact_is_available_beside_locked_forecast(self):
        b = self.backend("2026-10-08T12:00:00+03:00")
        b.upload("a.ofx", stmt("20261001", "20261007", [("d", "20261003120000", -10, "Shop", "Продукты")]))
        for period, key in (("month", "2026-10"), ("quarter", "2026-Q4"), ("year", "2026")):
            s = self.screen(b, key, period=period)
            self.assertEqual(["balance", "expense", "daily_expense", "income", "forecast"],
                             [t["id"] for t in self.block(s, "tiles")[0]["data"]["tiles"]])
            self.assertEqual(1000, self.tile(s, "expense")["value"])
            self.assertEqual("locked", self.tile(s, "forecast")["status"])

    def test_ready_forecast_and_stale_or_past_period_keep_fact(self):
        for now, key, forecast in (("2026-10-20T12:00:00+03:00", "2026-10", True),
                                   ("2026-11-01T12:00:00+03:00", "2026-10", False),
                                   ("2026-10-30T12:00:00+03:00", "2026-10", False)):
            b = self.backend(now)
            b.upload("a.ofx", stmt("20261001", "20261010", [("d", "20261003120000", -10, "Shop", "Продукты")]))
            s = self.screen(b, key)
            self.assertEqual(1000, self.tile(s, "expense")["value"])
            self.assertEqual(forecast, any(t["id"] == "forecast" for t in self.block(s, "tiles")[0]["data"]["tiles"]))

    def test_no_coverage_is_not_zero_expense(self):
        b = self.backend("2026-10-20T12:00:00+03:00")
        b.upload("a.ofx", stmt("20261001", "20261010", [("d", "20261003120000", -10, "Shop", "Продукты")]))
        tiles = self.block(self.screen(b, "2026-09"), "tiles")[0]
        self.assertEqual("none", tiles["status"])
        self.assertNotIn("data", tiles)

    def test_rolling_year_has_eleven_full_months_without_edge_transactions(self):
        b = self.backend("2026-10-31T12:00:00+03:00")
        b.upload("a.ofx", stmt("20251030", "20261030", [("d", "20260415120000", -10, "Shop", "Продукты")]))
        locked = self.tile(self.screen(b, "2026", period="year"), "forecast")["lock"]
        self.assertEqual((12, 11), (locked["required"], locked["available"]))
        text = locked["text"]
        self.assertIn("независимо от выбранного года", text)
        self.assertIn("ноябрь 2025", text)
        self.assertIn("сентябрь 2026", text)
        self.assertIn("октябрь 2025", text)
        self.assertIn("октябрь 2026", text)
        self.assertIn("2025-10-30", text)
        self.assertIn("2026-10-30", text)

    def test_missing_months_and_internal_gaps_are_not_a_continuous_base(self):
        b = self.backend("2026-10-31T12:00:00+03:00")
        for i, (a, z) in enumerate((("20260101", "20260131"), ("20260301", "20260331"),
                                    ("20260401", "20260414"), ("20260416", "20260430"),
                                    ("20261001", "20261030"))):
            b.upload(str(i) + ".ofx", stmt(a, z, [("d" + str(i), a + "120000", -10, "Shop", "Продукты")]))
        locked = self.tile(self.screen(b, "2026", period="year"), "forecast")["lock"]
        self.assertEqual(2, locked["available"])
        self.assertIn("январь 2026, март 2026", locked["text"])
        self.assertIn("Месяцы без данных: февраль 2026", locked["text"])
        self.assertIn("2026-04-15–2026-04-15", locked["text"])

    def test_complementary_files_count_once_and_current_month_is_unfinished(self):
        b = self.backend("2026-10-31T12:00:00+03:00")
        b.upload("a.ofx", stmt("20260901", "20260915", [("a", "20260910120000", -10, "Shop", "Продукты")]))
        b.upload("b.ofx", stmt("20260916", "20261031", [("b", "20261010120000", -10, "Shop", "Продукты")]))
        locked = self.tile(self.screen(b, "2026", period="year"), "forecast")["lock"]
        self.assertEqual(1, locked["available"])
        self.assertIn("Учтены полные месяцы: сентябрь 2026.", locked["text"])
        self.assertIn("месяц ещё не завершён", locked["text"])
