import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fetch_prices as fp  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "daily_sales.json"


class ParseTest(unittest.TestCase):
    def test_parse_price(self):
        self.assertEqual(fp.parse_price("63,289"), 63289)
        self.assertIsNone(fp.parse_price("-"))
        self.assertIsNone(fp.parse_price([]))

    def test_normalize_fixture(self):
        items = fp.normalize(json.loads(FIXTURE.read_text(encoding="utf-8")))
        self.assertGreater(len(items), 100)
        self.assertEqual({i["cls"] for i in items}, {"소매", "도매"})
        self.assertTrue(all(isinstance(i["price"], int) for i in items))
        self.assertEqual(len({i["id"] for i in items}), len(items))


class ForecastTest(unittest.TestCase):
    def test_linear_trend_used_with_history(self):
        series = [[f"2026-10-{d:02d}", 1000 + d * 10] for d in range(1, 11)]
        result = fp.forecast({"price": 1100, "prev_day": 1090, "month_ago": 1000}, series)
        self.assertEqual(result["method"], "최근 14일 선형추세")
        self.assertEqual(result["next"], 1110)
        self.assertEqual(result["direction"], "up")

    def test_forecast_is_clamped(self):
        result = fp.forecast({"price": 1000, "prev_day": 100, "month_ago": 10}, [])
        self.assertLessEqual(result["next"], 1100)


class BuildTest(unittest.TestCase):
    def test_build_writes_files_and_dedupes_history(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            fp.build(payload, out)
            fp.build(payload, out)  # 같은 날 두 번 실행해도 이력은 1건
            latest = json.loads((out / "latest.json").read_text(encoding="utf-8"))
            history = json.loads((out / "history.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["count"], len(latest["items"]))
            first = latest["items"][0]["id"]
            self.assertEqual(len(history[first]), 1)


if __name__ == "__main__":
    unittest.main()
