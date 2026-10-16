"""Examples of docs/calc.md, section 9, on the reference calculation."""
import datetime as dt
import os
import unittest
from zoneinfo import ZoneInfo

from refcalc.api import ApiError, Backend
from refcalc.compare import IGNORED, compare, redact
from refcalc.registry import Registry
from refcalc.util import round_half_up, tdiv

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..", "fixtures", "calc")
MSK = ZoneInfo("Europe/Moscow")
CUR = "40817810000000001111"
SAV = "42301810000000002222"


def stmt(start: str, end: str, trns: list, account: str = CUR, acctype: str = "CHECKING") -> str:
    """A minimal OFX statement: trns are (fitid, 'YYYYMMDDHHMMSS', amount, name, memo)."""
    rows = "".join(
        f"<STMTTRN>\n<TRNTYPE>{'DEBIT' if a < 0 else 'CREDIT'}</TRNTYPE>\n<DTPOSTED>{d}.000[+3:MSK]</DTPOSTED>\n"
        f"<TRNAMT>{a:.2f}</TRNAMT>\n<FITID>{f}</FITID>\n<NAME>{n}</NAME>\n<MEMO>{m}</MEMO>\n"
        f"<CURRENCY><CURSYM>RUB</CURSYM></CURRENCY>\n</STMTTRN>\n" for f, d, a, n, m in trns)
    return (f"<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKACCTFROM><BANKID>T-BANK</BANKID><ACCTID>{account}</ACCTID>"
            f"<ACCTTYPE>{acctype}</ACCTTYPE></BANKACCTFROM><BANKTRANLIST><DTSTART>{start}000000.000[+3:MSK]</DTSTART>"
            f"<DTEND>{end}235959.000[+3:MSK]</DTEND>\n{rows}</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>")


class SpecExamples(unittest.TestCase):
    def backend(self, now: str) -> Backend:
        b = Backend(Registry.load(os.path.join(ROOT, "categories.json")), MSK)
        b.now = dt.datetime.fromisoformat(now)
        return b

    def screen(self, b, date, mode="with", period="month"):
        return b.analytics(period, date, mode)

    def block(self, screen, bid):
        return [x for x in screen["blocks"] if x.get("id") == bid]

    def tile(self, screen, tid):
        return next(t for t in self.block(screen, "tiles")[0]["data"]["tiles"] if t["id"] == tid)

    def test_1_own_transfer_pair(self):
        b = self.backend("2026-09-26T12:00:00+03:00")
        b.upload("a.ofx", stmt("20260901", "20260925", [("d1", "20260918093000", -20000, "Между своими счетами", "Переводы")], SAV, "SAVINGS"))
        b.upload("b.ofx", stmt("20260901", "20260925", [("c1", "20260918093100", 20000, "Иван", "Переводы")]))
        items = b.feed()["items"]
        self.assertEqual([i["kind"] for i in items], ["own_transfer"])
        self.assertEqual(self.tile(self.screen(b, "2026-09"), "income")["value"], 0)

    def test_2_no_pair_after_four_days(self):
        b = self.backend("2026-09-26T12:00:00+03:00")
        b.upload("a.ofx", stmt("20260901", "20260925", [("d1", "20260918093000", -20000, "Между своими счетами", "Переводы")], SAV, "SAVINGS"))
        b.upload("b.ofx", stmt("20260901", "20260925", [("c1", "20260922093100", 20000, "Иван", "Переводы")]))
        kinds = sorted((i["kind"], i["category_id"]) for i in b.feed()["items"])
        self.assertEqual(kinds, [("expense", "cat_own_transfer"), ("income", "cat_transfers")])
        self.assertEqual(self.tile(self.screen(b, "2026-09", "without"), "income")["value"], 0)
        self.assertEqual(self.tile(self.screen(b, "2026-09"), "income")["value"], 2_000_000)

    def test_3_4_refund_by_description(self):
        b = self.backend("2026-10-02T12:00:00+03:00")
        b.upload("a.ofx", stmt("20260801", "20260930", [
            ("d1", "20260828120000", -3000, "Ozon", "Маркетплейсы"),
            ("c1", "20260903120000", 1200, "Ozon", "Маркетплейсы")]))
        s = self.screen(b, "2026-09")
        self.assertEqual(self.tile(s, "expense")["value"], -120_000)
        self.assertEqual(self.tile(s, "income")["value"], 0)
        self.assertEqual(self.tile(s, "balance")["value"], 120_000)
        row = self.block(s, "expense_categories")[0]["data"]["items"][0]
        self.assertEqual((row["id"], row["value"], "share" in row), ("cat_marketplaces", -120_000, False))

    def refund_case(self, credit):
        b = self.backend("2026-10-02T12:00:00+03:00")
        b.upload("a.ofx", stmt("20260801", "20260930", [("d1", "20260828120000", -3000, "Ozon", "Маркетплейсы"), credit]))
        return next(i for i in b.feed()["items"] if i["kind"] == "income")

    def test_refund_by_bank_category_only(self):
        r = self.refund_case(("c1", "20260903120000", 1200, "Другой магазин", "Маркетплейсы"))
        self.assertEqual((r["category_id"], r["note"]), ("cat_refund", "возврат · Маркетплейсы"))

    def test_refund_by_description_only(self):
        r = self.refund_case(("c1", "20260903120000", 1200, "Ozon", "Финансы"))
        self.assertEqual((r["category_id"], r["note"]), ("cat_refund", "возврат · Маркетплейсы"))

    def test_refund_by_word_only(self):
        r = self.refund_case(("c1", "20260903120000", 1700, "Возврат средств", "Финансы"))
        self.assertEqual((r["category_id"], r["note"]), ("cat_refund", "возврат"))

    def test_no_refund_sign_is_income(self):
        r = self.refund_case(("c1", "20260903120000", 1700, "Поступление", "Финансы"))
        self.assertEqual(r["category_id"], "cat_finance")

    def test_5_transfer_back_is_income(self):
        b = self.backend("2026-09-26T12:00:00+03:00")
        b.upload("a.ofx", stmt("20260901", "20260925", [
            ("d1", "20260902120000", -5000, "Иван И.", "Переводы"),
            ("c1", "20260910120000", 5000, "Иван И.", "Переводы")]))
        credit = next(i for i in b.feed()["items"] if i["kind"] == "income")
        self.assertEqual(credit["category_id"], "cat_transfers")

    def test_6_new_bank_category_both_directions(self):
        b = self.backend("2026-09-26T12:00:00+03:00")
        b.upload("a.ofx", stmt("20260901", "20260925", [("c1", "20260910120000", 5000, "Биржа", "Крипто")]))
        credit = b.feed()["items"][0]
        self.assertEqual((credit["kind"], credit["category_id"], credit["note"]), ("income", "cat_kripto", None))
        cat = next(c for c in b.categories()["categories"] if c["id"] == "cat_kripto")
        self.assertEqual(cat["kinds"], ["expense", "income"])

    def test_7_empty_memo_is_uncategorized(self):
        b = self.backend("2026-09-26T12:00:00+03:00")
        r = b.upload("a.ofx", stmt("20260901", "20260925", [("d1", "20260910120000", -700, "Ларёк", "")]))
        self.assertEqual(r["uncategorized_count"], 1)
        row = self.block(self.screen(b, "2026-09"), "expense_categories")[0]["data"]["items"][0]
        self.assertEqual((row["id"], row["value"]), ("cat_uncategorized", 70_000))

    def test_8_two_files_make_a_full_month(self):
        b = self.backend("2026-08-05T12:00:00+03:00")
        b.upload("a.ofx", stmt("20260701", "20260715", [("d1", "20260705120000", -100, "Кафе", "Рестораны")]))
        b.upload("b.ofx", stmt("20260716", "20260731", [("d2", "20260720120000", -100, "Кафе", "Рестораны")]))
        self.assertEqual(b.periods("month")["periods"][-1], {
            "key": "2026-07", "range": {"from": "2026-07-01T00:00:00+03:00", "to": "2026-08-01T00:00:00+03:00"},
            "coverage": "complete"})

    def test_9_gap(self):
        b = self.backend("2026-08-05T12:00:00+03:00")
        b.upload("a.ofx", stmt("20260701", "20260710", [("d1", "20260705120000", -100, "Кафе", "Рестораны")]))
        b.upload("b.ofx", stmt("20260720", "20260731", [("d2", "20260725120000", -100, "Кафе", "Рестораны")]))
        p = self.screen(b, "2026-07")["period"]
        self.assertEqual(p["coverage"], "partial")
        self.assertEqual(p["gaps"], [{"from": "2026-07-11T00:00:00+03:00", "to": "2026-07-20T00:00:00+03:00"}])

    def test_10_stale_by_period_end(self):
        b = self.backend("2026-10-16T12:00:00+03:00")
        b.upload("a.ofx", stmt("20260901", "20261015", [("d1", "20260920120000", -100, "Кафе", "Рестораны")]))
        self.assertFalse(b.feed()["state"]["stale"])

    def test_11_forecast_and_daily(self):
        b = self.backend("2026-09-26T12:00:00+03:00")
        b.upload("a.ofx", stmt("20260901", "20260925", [("d1", "20260910120000", -84320, "Магазин", "Супермаркеты")]))
        s = self.screen(b, "2026-09")
        self.assertEqual(self.tile(s, "forecast")["value"], 10_118_400)
        self.assertEqual(self.tile(s, "daily_expense")["value"], 337_280)

    def test_12_too_early(self):
        b = self.backend("2026-09-06T12:00:00+03:00")
        b.upload("a.ofx", stmt("20260901", "20260905", [("d1", "20260902120000", -100, "Кафе", "Рестораны")]))
        t = self.tile(self.screen(b, "2026-09"), "forecast")
        self.assertEqual(t["lock"]["reason"], "too_early_in_month")
        self.assertEqual(t["lock"]["available_from"], "2026-09-10T00:00:00+03:00")

    def test_14_no_income(self):
        b = self.backend("2026-09-26T12:00:00+03:00")
        b.upload("a.ofx", stmt("20260901", "20260925", [("d1", "20260910120000", -10000, "Магазин", "Супермаркеты")]))
        self.assertEqual(self.tile(self.screen(b, "2026-09"), "balance")["value"], -1_000_000)

    def test_15_notable_spending(self):
        b = self.backend("2026-09-26T12:00:00+03:00")
        b.upload("a.ofx", stmt("20260601", "20260925", [
            ("j", "20260610120000", -2000, "Такси", "Такси"), ("l", "20260710120000", -3000, "Такси", "Такси"),
            ("a", "20260810120000", -4000, "Такси", "Такси"), ("s", "20260910120000", -7200, "Такси", "Такси")]))
        cards = self.block(self.screen(b, "2026-09"), "notable_spending")
        self.assertEqual([(c["status"], c["data"]["value"]) for c in cards], [("ready", 720_000)])

    def test_16_small_frequent(self):
        b = self.backend("2026-10-02T12:00:00+03:00")
        nine = [(f"k{i}", f"202609{10 + i}120000", -280, "Кофейня", "Фастфуд") for i in range(9)]
        b.upload("a.ofx", stmt("20260901", "20260930", nine))
        card = self.block(self.screen(b, "2026-09"), "small_frequent")[0]
        self.assertEqual((card["status"], card["data"]["value"]), ("ready", 252_000))
        b2 = self.backend("2026-10-02T12:00:00+03:00")
        b2.upload("a.ofx", stmt("20260901", "20260930", nine[:8]))
        self.assertEqual(self.block(self.screen(b2, "2026-09"), "small_frequent")[0]["status"], "none")

    def test_17_regular_payment_tentative(self):
        b = self.backend("2026-09-26T12:00:00+03:00")
        b.upload("a.ofx", stmt("20260701", "20260925", [
            ("j", "20260715120000", -299, "Подписка", "Онлайн-кинотеатры"),
            ("a", "20260815120000", -299, "Подписка", "Онлайн-кинотеатры")]))
        card = self.block(self.screen(b, "2026-09"), "regular_payments")[0]
        self.assertEqual((card["status"], card["data"]["value"]), ("tentative", 29_900))

    def test_regular_payment_needs_one_charge_a_month(self):
        b = self.backend("2026-09-26T12:00:00+03:00")
        b.upload("a.ofx", stmt("20260701", "20260925", [
            ("j1", "20260705120000", -500, "Фитнес", "Тренировки"), ("j2", "20260720120000", -500, "Фитнес", "Тренировки"),
            ("a1", "20260805120000", -500, "Фитнес", "Тренировки"), ("a2", "20260820120000", -500, "Фитнес", "Тренировки")]))
        self.assertEqual(self.block(self.screen(b, "2026-09"), "regular_payments")[0]["status"], "none")


    def test_19_gap_crossing_the_period_is_whole(self):
        b = self.backend("2026-09-05T12:00:00+03:00")
        b.upload("a.ofx", stmt("20260701", "20260710", [("d1", "20260705120000", -100, "Кафе", "Рестораны")]))
        r = b.upload("b.ofx", stmt("20260804", "20260831", [("d2", "20260825120000", -100, "Кафе", "Рестораны")]))
        gap = [{"from": "2026-07-11T00:00:00+03:00", "to": "2026-08-04T00:00:00+03:00"}]
        self.assertEqual(self.screen(b, "2026-08")["period"]["gaps"], gap)
        months = {m["month"]: m["gaps"] for m in r["coverage"]["incomplete_months"]}
        self.assertEqual((months["2026-07"], months["2026-08"]), (gap, gap))

    def test_20_no_pair_across_currencies(self):
        b = self.backend("2026-09-26T12:00:00+03:00")
        b.upload("a.ofx", stmt("20260901", "20260925", [("d1", "20260918093000", -1000, "Между своими счетами", "Переводы")], SAV, "SAVINGS"))
        b.upload("b.ofx", stmt("20260901", "20260925", [("c1", "20260918093100", 1000, "Иван", "Переводы")]).replace(
            "<CURSYM>RUB</CURSYM>", "<CURSYM>USD</CURSYM>"))
        kinds = sorted((i["kind"], i["category_id"]) for i in b.feed()["items"])
        self.assertEqual(kinds, [("expense", "cat_own_transfer"), ("income", "cat_transfers")])

    def test_21_search_like_analytics_drops_single_transfer_to_self(self):
        b = self.backend("2026-09-26T12:00:00+03:00")
        b.upload("a.ofx", stmt("20260901", "20260925", [
            ("d1", "20260910120000", -5000, "Себе в другой банк", "Переводы"),
            ("d2", "20260911120000", -100, "Кафе", "Рестораны")]))
        self.assertEqual(b.search({})["total_count"], 2)
        self.assertEqual(b.search({"transfer_mode": "with"})["total_count"], 1)
    def test_22_manual_category_blocks_pairing(self):
        b = self.backend("2026-09-26T12:00:00+03:00")
        b.upload("a.ofx", stmt("20260901", "20260925", [("d1", "20260910120000", -5000, "Себе в другой банк", "Переводы")]))
        b.change_category(CUR, "d1", "cat_gifts")
        b.upload("b.ofx", stmt("20260901", "20260925", [("c1", "20260910120100", 5000, "Себе из другого банка", "Переводы")], SAV, "SAVINGS"))
        kinds = sorted((i["kind"], i["category_id"]) for i in b.feed()["items"])
        self.assertEqual(kinds, [("expense", "cat_gifts"), ("income", "cat_own_transfer")])

    def test_23_memo_refund(self):
        b = self.backend("2026-10-02T12:00:00+03:00")
        b.upload("a.ofx", stmt("20260801", "20260930", [
            ("d1", "20260828120000", -3000, "Ozon", "Маркетплейсы"),
            ("c1", "20260903120000", 1200, "Ozon", "Возврат"),
            ("d2", "20260904120000", -500, "Банк", "Возврат")]))
        got = {i["id"]: (i["kind"], i["category_id"], i["note"]) for i in b.feed()["items"]}
        self.assertEqual(sorted(got.values()), [
            ("expense", "cat_marketplaces", None), ("expense", "cat_uncategorized", None),
            ("income", "cat_refund", "возврат · Маркетплейсы")])
        self.assertNotIn("Возврат", [c["name"] for c in b.categories()["categories"] if c["id"] != "cat_refund"])

    def test_24_feed_before_mid_month_is_rejected(self):
        b = self.backend("2026-09-26T12:00:00+03:00")
        b.upload("a.ofx", stmt("20260601", "20260925", [("d1", "20260910120000", -100, "Кафе", "Рестораны")]))
        with self.assertRaises(ApiError) as e:
            b.feed("2026-07-15T00:00:00+03:00")
        self.assertEqual((e.exception.status, e.exception.code), (400, "VALIDATION_ERROR"))
        self.assertEqual(b.feed("2026-07-01T00:00:00+03:00")["range"]["from"], "2026-04-01T00:00:00+03:00")


class Robustness(unittest.TestCase):
    def test_non_finite_amount_is_unread(self):
        b = Backend(Registry.load(os.path.join(ROOT, "categories.json")), MSK)
        b.now = dt.datetime.fromisoformat("2026-09-26T12:00:00+03:00")
        text = stmt("20260901", "20260925", [("d1", "20260910120000", -100, "Кафе", "Рестораны")]).replace(
            "<TRNAMT>-100.00</TRNAMT>", "<TRNAMT>NaN</TRNAMT>")
        r = b.upload("a.ofx", text)
        self.assertEqual((r["operation_count"], r["unread_count"]), (0, 1))
        self.assertEqual(b.search({"q": "NaN"})["total_count"], 0)

class Rounding(unittest.TestCase):
    def test_tdiv_truncates_toward_zero(self):
        self.assertEqual((tdiv(7, 2), tdiv(-7, 2)), (3, -3))

    def test_half_up(self):
        from fractions import Fraction
        self.assertEqual((round_half_up(Fraction(5, 2)), round_half_up(Fraction(-5, 2))), (3, -2))


class Comparison(unittest.TestCase):
    def test_texts_and_ids_are_ignored(self):
        exp = redact({"items": [{"id": "x", "title": "Пятёрочка"}], "blocks": [{"id": "tiles", "title": "T", "alt_text": "a"}]})
        self.assertEqual(exp, {"items": [{"id": IGNORED, "title": "Пятёрочка"}],
                               "blocks": [{"id": "tiles", "title": IGNORED, "alt_text": IGNORED}]})
        self.assertEqual(compare(exp, {"items": [{"id": "y", "title": "Пятёрочка"}],
                                       "blocks": [{"id": "tiles", "title": "Плитки"}]}), [])

    def test_new_bank_category_id_is_opaque(self):
        known = frozenset({"cat_supermarkets"})
        exp = redact({"items": [{"id": "x", "category_id": "cat_kripto", "category_name": "Крипто"},
                                {"id": "y", "category_id": "cat_supermarkets", "category_name": "Супермаркеты"}]},
                     known=known)
        self.assertEqual([i["category_id"] for i in exp["items"]], [IGNORED, "cat_supermarkets"])

    def test_moments_absent_null_and_extra(self):
        exp = {"at": "2026-09-25T21:40:00+03:00", "note": None, "status": "ready"}
        self.assertEqual(compare(exp, {"at": "2026-09-25T18:40:00Z"}), [])
        self.assertTrue(compare({"a": 1}, {"a": 1, "share": 0.5}))
        self.assertTrue(compare({"amount": 100}, {"amount": 100.0000001}))
        self.assertEqual(compare({"value": 337280}, {"value": 337280.0}), [])
        self.assertEqual(compare({"type": "heading"}, {"type": "heading", "status": "ready"}), [])
        self.assertTrue(compare({"categories": [{"id": "c"}]}, {"categories": [{"id": "c", "note": "не в тратах"}]}))


if __name__ == "__main__":
    unittest.main()
