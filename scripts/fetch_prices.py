#!/usr/bin/env python3
"""KAMIS(농산물유통정보) 부류별 일별 시세를 받아 public/data/*.json 으로 저장한다.

dailyPriceByCategoryList 는 조회일 기준 당일·1일전·1주일전·2주일전 가격을
실제 날짜와 함께 돌려주므로, 날짜가 정확한 일별 이력을 쌓을 수 있다.

표준 라이브러리만 사용한다. 인증키는 환경변수 KAMIS_CERT_KEY / KAMIS_CERT_ID 로
지정하며, 없으면 KAMIS 문서에 공개된 테스트 키(111/222)를 쓴다.

  python3 scripts/fetch_prices.py               # 오늘 시세 갱신
  python3 scripts/fetch_prices.py --backfill 90 # 지난 90일 이력 채우기
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

API_URL = "https://www.kamis.or.kr/service/price/xml.do"
KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "public" / "data"
HISTORY_DAYS = 400
STALE_DAYS = 14  # 마지막 조사일이 기준일보다 이만큼 오래되면 목록에서 뺀다

CLASSES = {"01": "소매", "02": "도매"}
CATEGORIES = {"100": "식량작물", "200": "채소류", "300": "특용작물", "400": "과일류", "500": "축산물", "600": "수산물"}
SKIP = {("02", "500")}  # 도매 축산물은 소매와 같은 값을 돌려준다
# 응답의 dayN 라벨 → 필드. 날짜가 붙은 라벨은 이력으로, 나머지는 비교값으로 쓴다.
DATED_LABELS = ("당일", "1일전", "1주일전", "2주일전")
REF_LABELS = {"1개월전": "month_ago", "1년전": "year_ago", "일평년": "normal"}


def kamis_get(params: dict, cert: tuple[str, str], retries: int = 3) -> dict | None:
    query = urllib.parse.urlencode({
        **params, "p_cert_key": cert[0], "p_cert_id": cert[1], "p_returntype": "json",
    })
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(f"{API_URL}?{query}", headers={"User-Agent": "food-price-bot/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as err:  # 네트워크/파싱 오류 모두 재시도
            last_err = err
            time.sleep(2 ** attempt * 3)
    raise RuntimeError(f"KAMIS 요청 실패 {params}: {last_err}")


def fetch_category(day: date, cls: str, cat: str, cert: tuple[str, str]) -> list[dict]:
    payload = kamis_get({
        "action": "dailyPriceByCategoryList",
        "p_product_cls_code": cls,
        "p_item_category_code": cat,
        "p_country_code": "",
        "p_regday": day.isoformat(),
        "p_convert_kg_yn": "N",
    }, cert)
    data = payload.get("data") if payload else None
    if not isinstance(data, dict) or str(data.get("error_code")) != "000":
        return []  # "001" = 해당 날짜 자료 없음 (주말·공휴일)
    items = data.get("item", [])
    return [items] if isinstance(items, dict) else items


def fetch_day(day: date, cert: tuple[str, str]) -> dict[tuple[str, str], list[dict]]:
    combos = [(c, k) for c in CLASSES for k in CATEGORIES if (c, k) not in SKIP]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = pool.map(lambda ck: fetch_category(day, *ck, cert), combos)
    return {ck: rows for ck, rows in zip(combos, results) if rows}


def parse_price(value) -> int | None:
    """'63,289' → 63289. '-', '', [] 등은 None."""
    if not isinstance(value, str):
        return None
    value = value.replace(",", "").strip()
    try:
        return int(float(value))
    except ValueError:
        return None


def parse_label_date(label: str, regday: date) -> str | None:
    """'1일전 (11/05)' → '2026-11-05'. 조회일보다 뒤의 월이면 전년도로 본다."""
    m = re.search(r"\((\d{2})/(\d{2})\)", label or "")
    if not m:
        return None
    month, dd = int(m.group(1)), int(m.group(2))
    year = regday.year - (1 if month > regday.month else 0)
    return date(year, month, dd).isoformat()


def clean(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def item_meta(cls: str, cat: str, row: dict) -> dict:
    unit = clean(row.get("unit"))
    kind = clean(row.get("kind_name"))
    if unit and kind.endswith(f"({unit})"):
        kind = kind[: -len(unit) - 2]
    rank = clean(row.get("rank"))
    return {
        "id": f"{cls}-{row['item_code']}-{row['kind_code']}-{row['rank_code']}",
        "cls": CLASSES[cls],
        "category_code": cat,
        "category": CATEGORIES[cat],
        "item": clean(row.get("item_name")),
        "kind": kind,
        "name": f"{clean(row.get('item_name'))} {kind}".strip(),
        "rank": "" if rank == "-" else rank,
        "unit": unit,
    }


def parse_rows(day: date, by_combo: dict) -> tuple[dict, dict]:
    """조회 결과 → (품목 메타 + 비교값, 날짜별 가격 점)."""
    meta, points = {}, {}
    for (cls, cat), rows in by_combo.items():
        for row in rows:
            m = item_meta(cls, cat, row)
            for n in range(1, 8):
                label, price = clean(row.get(f"day{n}")), parse_price(row.get(f"dpr{n}"))
                if label.split(" ")[0] in DATED_LABELS:
                    d = parse_label_date(label, day)
                    if d and price:
                        points.setdefault(m["id"], {})[d] = price
                elif label in REF_LABELS:
                    m[REF_LABELS[label]] = price
            meta[m["id"]] = m
    return meta, points


def merge_history(history: dict, points: dict, today: date) -> dict:
    cutoff = (today - timedelta(days=HISTORY_DAYS)).isoformat()
    for item_id, by_day in points.items():
        series = {d: v for d, v in history.get(item_id, []) if d >= cutoff}
        series.update(by_day)
        history[item_id] = sorted([d, v] for d, v in series.items())
    return history


def pct(now: int | None, before: int | None) -> float | None:
    if not now or not before:
        return None
    return round((now - before) / before * 100, 1)


def next_weekday(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def linear_next(values: list[int]) -> float:
    """최소제곱 직선으로 다음 시점 값을 추정한다."""
    n = len(values)
    mx, my = (n - 1) / 2, sum(values) / n
    sxx = sum((x - mx) ** 2 for x in range(n))
    slope = sum((x - mx) * (y - my) for x, y in enumerate(values)) / sxx
    return my + slope * (n - mx)


def forecast(item: dict, series: list[list]) -> dict:
    """다음 조사일 가격을 단순 추세로 예측한다 (참고용).

    - 이력이 7일 이상이면 최근 14개 조사일 선형회귀 (±15% 제한)
    - 그 전에는 1개월 전 대비 추세를 일 단위로 환산 (±10% 제한)
    """
    today = item["price"]
    values = [v for _, v in series][-14:]
    if len(values) >= 7:
        guess, method, limit = linear_next(values), "최근 14개 조사일 선형추세", 0.15
    else:
        drift = (today / item["month_ago"]) ** (1 / 20) - 1 if item.get("month_ago") else 0.0
        guess, method, limit = today * (1 + 0.6 * drift), "1개월 추세", 0.10
    guess = min(max(guess, today * (1 - limit)), today * (1 + limit))
    change = round((guess - today) / today * 100, 1)
    direction = "up" if change >= 0.5 else "down" if change <= -0.5 else "flat"
    return {
        "date": next_weekday(date.fromisoformat(item["latest_day"])).isoformat(),
        "next": round(guess), "change_pct": change, "direction": direction, "method": method,
    }


def build_latest(meta: dict, history: dict) -> dict:
    data_date = max(history[i][-1][0] for i in meta if history.get(i))
    stale = (date.fromisoformat(data_date) - timedelta(days=STALE_DAYS)).isoformat()
    items = []
    for item_id, m in meta.items():
        series = history.get(item_id, [])
        if not series or series[-1][0] < stale:
            continue
        item = {**m, "latest_day": series[-1][0], "price": series[-1][1]}
        if len(series) > 1:
            item["prev_day_date"], item["prev_day"] = series[-2]
        item["change_day_pct"] = pct(item["price"], item.get("prev_day"))
        item["change_month_pct"] = pct(item["price"], item.get("month_ago"))
        item["change_year_pct"] = pct(item["price"], item.get("year_ago"))
        item["forecast"] = forecast(item, series)
        items.append(item)
    items.sort(key=lambda i: (i["cls"], i["category_code"], i["name"], i["rank"]))
    return {
        "source": "KAMIS 농산물유통정보 (aT 한국농수산식품유통공사)",
        "source_url": "https://www.kamis.or.kr",
        "updated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "data_date": data_date,
        "count": len(items),
        "items": items,
    }


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


def build(day: date, by_combo: dict, data_dir: Path, extra_points: dict | None = None) -> dict:
    meta, points = parse_rows(day, by_combo)
    if not meta:
        raise RuntimeError("파싱된 품목이 없습니다")
    history = load_json(data_dir / "history.json", {})
    history = merge_history(history, extra_points or {}, day)
    history = merge_history(history, points, day)
    latest = build_latest(meta, history)
    write_json(data_dir / "latest.json", latest)
    write_json(data_dir / "history.json", history)
    return latest


def latest_available(cert, start: date) -> tuple[date, dict]:
    """부류마다 가장 최근 조사일의 자료를 모은다.

    주말에는 축산물처럼 일부 부류만 조사되므로, 아직 자료가 없는 부류는
    하루씩 거슬러 올라가며 채운다.
    """
    wanted = {(c, k) for c in CLASSES for k in CATEGORIES if (c, k) not in SKIP}
    merged: dict = {}
    newest = None
    for back in range(7):
        day = start - timedelta(days=back)
        for combo, rows in fetch_day(day, cert).items():
            if combo not in merged:
                merged[combo] = rows
                newest = newest or day
        if merged.keys() >= wanted:
            break
    if not merged:
        raise RuntimeError("최근 7일간 조회된 자료가 없습니다")
    return newest, merged


def backfill(days: int, cert, end: date) -> dict:
    """하루 걸러 조회해도 응답의 '1일전'이 빈 날을 채운다."""
    weekdays = [end - timedelta(days=n) for n in range(1, days + 1)]
    weekdays = [d for d in weekdays if d.weekday() < 5][::2]
    points: dict = {}
    for k, day in enumerate(weekdays, 1):
        _, pts = parse_rows(day, fetch_day(day, cert))
        for item_id, by_day in pts.items():
            points.setdefault(item_id, {}).update(by_day)
        print(f"  backfill {k}/{len(weekdays)} {day}", file=sys.stderr)
    return points


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fixture", type=Path, help="API 대신 저장된 응답 JSON({regday, data:{'cls-cat': rows}})을 사용")
    parser.add_argument("--backfill", type=int, metavar="DAYS", help="지난 DAYS일 이력을 함께 채운다")
    parser.add_argument("--out", type=Path, default=DATA_DIR)
    args = parser.parse_args()

    cert = (os.environ.get("KAMIS_CERT_KEY") or "111", os.environ.get("KAMIS_CERT_ID") or "222")
    extra = None
    if args.fixture:
        fx = json.loads(args.fixture.read_text(encoding="utf-8"))
        day = date.fromisoformat(fx["regday"])
        by_combo = {tuple(k.split("-")): v for k, v in fx["data"].items()}
    else:
        day, by_combo = latest_available(cert, datetime.now(KST).date())
        if args.backfill:
            extra = backfill(args.backfill, cert, day)
    latest = build(day, by_combo, args.out, extra)
    print(f"[{latest['updated_at']}] {latest['count']}개 품목 저장 (기준일 {latest['data_date']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
