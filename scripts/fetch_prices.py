#!/usr/bin/env python3
"""KAMIS(농산물유통정보) 일별 시세를 받아 public/data/*.json 으로 저장한다.

표준 라이브러리만 사용한다. 인증키는 환경변수 KAMIS_CERT_KEY / KAMIS_CERT_ID 로
지정하며, 없으면 KAMIS 문서에 공개된 테스트 키(111/222)를 쓴다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API_URL = "https://www.kamis.or.kr/service/price/xml.do"
KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "public" / "data"
HISTORY_DAYS = 400

# dailySalesList 의 day 라벨 → 필드 이름
DAY_LABELS = {"당일": "price", "1일전": "prev_day", "1개월전": "month_ago", "1년전": "year_ago"}


def fetch_daily_sales(cert_key: str, cert_id: str, retries: int = 3) -> dict:
    query = urllib.parse.urlencode({
        "action": "dailySalesList",
        "p_cert_key": cert_key,
        "p_cert_id": cert_id,
        "p_returntype": "json",
    })
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(f"{API_URL}?{query}", headers={"User-Agent": "food-price-bot/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if str(payload.get("error_code")) != "000":
                raise RuntimeError(f"KAMIS error_code={payload.get('error_code')}")
            return payload
        except Exception as err:  # 네트워크/파싱 오류 모두 재시도
            last_err = err
            time.sleep(2 ** attempt * 3)
    raise RuntimeError(f"KAMIS 요청 실패: {last_err}")


def parse_price(value) -> int | None:
    """'63,289' → 63289. '-', '', [] 등은 None."""
    if not isinstance(value, str):
        return None
    value = value.replace(",", "").strip()
    try:
        return int(float(value))
    except ValueError:
        return None


def pct(now: int | None, before: int | None) -> float | None:
    if not now or not before:
        return None
    return round((now - before) / before * 100, 1)


def normalize(payload: dict) -> list[dict]:
    items = []
    for row in payload.get("price", []):
        item = {
            # 축산물은 productno 가 등급 코드라 겹치므로 품목명 해시를 덧붙인다
            "id": f"{row['product_cls_code']}-{row['productno']}-"
                  + hashlib.md5(row["productName"].encode()).hexdigest()[:6],
            "cls": row["product_cls_name"],          # 소매 / 도매
            "category_code": row["category_code"],
            "category": row["category_name"],
            "name": row["productName"],
            "unit": row["unit"] if isinstance(row["unit"], str) else "",
            "latest_day": row.get("lastest_day"),
        }
        for n in range(1, 5):
            field = DAY_LABELS.get(row.get(f"day{n}"))
            if field:
                item[field] = parse_price(row.get(f"dpr{n}"))
        if item.get("price") is None:
            continue
        item["change_day_pct"] = pct(item["price"], item.get("prev_day"))
        item["change_month_pct"] = pct(item["price"], item.get("month_ago"))
        item["change_year_pct"] = pct(item["price"], item.get("year_ago"))
        items.append(item)
    return items


def linear_next(values: list[int]) -> float:
    """최소제곱 직선으로 다음 시점 값을 추정한다."""
    n = len(values)
    xs = range(n)
    mx, my = (n - 1) / 2, sum(values) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, values)) / sxx
    return my + slope * (n - mx)


def forecast(item: dict, series: list[list]) -> dict:
    """다음 조사일 가격을 단순 추세로 예측한다 (참고용).

    - 누적 이력이 7일 이상이면 최근 14일 선형회귀
    - 그 전에는 1개월 추세(일 단위 환산)와 전일 대비 모멘텀을 섞는다
    """
    today = item["price"]
    values = [v for _, v in series][-14:]
    if len(values) >= 7:
        guess, method, limit = linear_next(values), "최근 14일 선형추세", 0.15
    else:
        drift = 0.0
        if item.get("month_ago"):
            drift = math.pow(today / item["month_ago"], 1 / 20) - 1  # 한 달 ≈ 20 조사일
        momentum = (today - item["prev_day"]) / item["prev_day"] if item.get("prev_day") else 0.0
        guess, method, limit = today * (1 + 0.6 * drift + 0.2 * momentum), "1개월 추세 + 전일 모멘텀", 0.10
    guess = min(max(guess, today * (1 - limit)), today * (1 + limit))
    change = round((guess - today) / today * 100, 1)
    direction = "up" if change >= 0.5 else "down" if change <= -0.5 else "flat"
    return {"next": round(guess), "change_pct": change, "direction": direction, "method": method}


def update_history(history: dict, items: list[dict]) -> dict:
    cutoff = (datetime.now(KST) - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")
    for item in items:
        day = item.get("latest_day")
        if not day:
            continue
        series = [p for p in history.get(item["id"], []) if p[0] != day and p[0] >= cutoff]
        series.append([day, item["price"]])
        series.sort(key=lambda p: p[0])
        history[item["id"]] = series
    return history


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def build(payload: dict, data_dir: Path) -> dict:
    items = normalize(payload)
    if not items:
        raise RuntimeError("파싱된 품목이 없습니다")
    history = update_history(load_json(data_dir / "history.json", {}), items)
    for item in items:
        item["forecast"] = forecast(item, history.get(item["id"], []))
    latest = {
        "source": "KAMIS 농산물유통정보 (aT 한국농수산식품유통공사)",
        "source_url": "https://www.kamis.or.kr",
        "updated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "data_date": max(i["latest_day"] for i in items if i.get("latest_day")),
        "count": len(items),
        "items": items,
    }
    write_json(data_dir / "latest.json", latest)
    write_json(data_dir / "history.json", history)
    return latest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, help="API 대신 저장된 JSON 응답을 사용")
    parser.add_argument("--out", type=Path, default=DATA_DIR)
    args = parser.parse_args()

    if args.fixture:
        payload = json.loads(args.fixture.read_text(encoding="utf-8"))
    else:
        payload = fetch_daily_sales(os.environ.get("KAMIS_CERT_KEY", "111"), os.environ.get("KAMIS_CERT_ID", "222"))
    latest = build(payload, args.out)
    print(f"[{latest['updated_at']}] {latest['count']}개 품목 저장 (기준일 {latest['data_date']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
