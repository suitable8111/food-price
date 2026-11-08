import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fetch_prices as fp  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "category_2026-11-06.json"
DAY = date(2026, 11, 6)


def load_fixture():
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return {tuple(k.split("-")): v for k, v in fx["data"].items()}


class ParseTest(unittest.TestCase):
    def test_parse_price(self):
        self.assertEqual(fp.parse_price("63,289"), 63289)
        self.assertIsNone(fp.parse_price("-"))
        self.assertIsNone(fp.parse_price([]))

    def test_label_date_handles_year_boundary(self):
        self.assertEqual(fp.parse_label_date("1일전 (11/05)", DAY), "2026-11-05")
        self.assertEqual(fp.parse_label_date("2주일전 (12/24)", date(2027, 1, 7)), "2026-12-24")
        self.assertIsNone(fp.parse_label_date("1개월전", DAY))

    def test_parse_rows(self):
        meta, points = fp.parse_rows(DAY, load_fixture())
        self.assertGreater(len(meta), 100)
        cabbage = next(m for m in meta.values() if m["item"] == "배추" and m["kind"] == "가을" and m["cls"] == "소매")
        self.assertEqual(cabbage["unit"], "1포기")
        self.assertIn("2026-11-06", points[cabbage["id"]])
        beef = next(m for m in meta.values() if m["item"] == "소")
        self.assertEqual(beef["category"], "축산물")
        self.assertTrue(beef["rank"].endswith("등급"))


class ForecastTest(unittest.TestCase):
    def test_linear_trend_used_with_history(self):
        series = [[f"2026-10-{d:02d}", 1000 + d * 10] for d in range(1, 11)]
        item = {"price": 1100, "month_ago": 1000, "latest_day": "2026-11-06"}
        result = fp.forecast(item, series)
        self.assertEqual(result["next"], 1110)
        self.assertEqual(result["direction"], "up")
        self.assertEqual(result["date"], "2026-11-09")  # 금요일 다음은 월요일

    def test_forecast_is_clamped(self):
        result = fp.forecast({"price": 1000, "month_ago": 10, "latest_day": "2026-11-06"}, [])
        self.assertLessEqual(result["next"], 1100)


class BuildTest(unittest.TestCase):
    def test_build_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            fp.build(DAY, load_fixture(), out)
            latest = fp.build(DAY, load_fixture(), out)
            history = json.loads((out / "history.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["data_date"], "2026-11-06")
            self.assertEqual(latest["count"], len(latest["items"]))
            self.assertEqual(len({i["id"] for i in latest["items"]}), latest["count"])
            for series in history.values():
                days = [d for d, _ in series]
                self.assertEqual(days, sorted(set(days)))


class LatestAvailableTest(unittest.TestCase):
    def test_weekend_partial_day_is_filled_from_earlier_days(self):
        rows = load_fixture()
        sunday, friday = date(2026, 11, 8), date(2026, 11, 6)
        by_day = {sunday: {("01", "500"): rows[("01", "500")]}, friday: rows}
        original = fp.fetch_day
        fp.fetch_day = lambda day, cert: by_day.get(day, {})
        try:
            day, merged = fp.latest_available(("k", "i"), sunday)
        finally:
            fp.fetch_day = original
        self.assertEqual(day, sunday)
        self.assertEqual(set(merged), set(rows))


if __name__ == "__main__":
    unittest.main()
