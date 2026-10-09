"""v5.345 — 눌림형 → 추세 눌림 / 하락 추세 분리(표시·필터 전용).

사용자 지시: "관찰의 '눌림형'은 ABC A 미통과(덜 빠진 종목) 전체라서 우상향 속 눌림과 하락 추세가 섞여 있음. … 평가 눌림형
체크리스트 ①(20개월선 상승 중)과 ④(직전 저점 위)를 재사용. 새 계산·상수 금지." 추세 눌림 = ①O·④O, 하락 추세 = ①X 또는 ④X,
①④ 중 None이 있으면 눌림형(판정 불가). ①X·④None처럼 X가 확정되면 하락 추세(lowpoint_eval.pullback_class 주석).

사보타주 확인(2026-10-09, FAIL 확인 후 원복): 조건을 "①④ 중 하나만 O"(a or b → 추세 눌림)로 → test_class_rule FAIL
"""
from __future__ import annotations

import gzip
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.join(ROOT, "scripts", "screens"), os.path.join(ROOT, "scripts", "measurements")):
    sys.path.insert(0, p)
import lowpoint_eval as ev  # noqa: E402
import lowpoint_rank as rk  # noqa: E402
import lowpoint_watch as w  # noqa: E402

KST = timezone(timedelta(hours=9))
FX = json.load(gzip.open(os.path.join(ROOT, "test_fixtures", "lowpoint_setup_20261009.json.gz"), "rt", encoding="utf-8"))["tickers"]


def _df(code):
    t = FX[code]
    return pd.DataFrame({k: [float("nan") if v is None else v for v in t[k]] for k in ("Open", "High", "Low", "Close", "Volume")},
                        index=pd.to_datetime(t["dates"]))


def _items(a, b):
    return [{"key": "ma20_rising", "result": a}, {"key": "above_prior_low", "result": b}, {"key": "above_ma20", "result": False}]


@pytest.mark.parametrize("a,b,want", [
    (True, True, "trend"), (True, False, "down"), (False, True, "down"), (False, False, "down"),
    (False, None, "down"), (None, False, "down"),                    # X가 확정되면 하락 추세
    (True, None, "pullback_unknown"), (None, True, "pullback_unknown"), (None, None, "pullback_unknown"),
])
def test_class_rule(a, b, want):
    assert ev.pullback_class(_items(a, b)) == want
    assert ev.PULLBACK_CLASS_LABEL[want] in ("추세 눌림", "하락 추세", "눌림형(판정 불가)")


def test_peg_is_downtrend():
    now = datetime(2026, 10, 9, 7, 0, tzinfo=KST).isoformat()
    s = w.setup_type(_df("PEG"), "2026-10-08", "US", now)
    assert (s["setup_type"], s["setup_pullback_class"], s["setup_class"]) == ("pullback", "down", "down")
    hi = w.setup_type(_df("365590.KQ"), "2026-10-08", "KR", now)
    assert (hi["setup_class"], hi["setup_pullback_class"]) == ("bottom", None)                 # 바닥형은 나누지 않는다
    assert w.setup_type(_df("PEG"), "2026-10-08")["setup_class"] == "pullback_unknown"          # 시장·시각 없으면 판정 안 함


def _trend_daily(depth=0.995):
    """5년 꾸준한 상승(종가 100 → 300) 뒤 마지막 30봉 얕은 눌림(−0.5%) — 20개월선 상승 중, 고점 직전 같은 봉 수(30봉) 최저 종가
    (선형 상승이라 고점보다 약 1.4% 낮음) 위. −5%처럼 깊게 빠지면 직전 저점 아래라 하락 추세가 된다(아래에서 확인)."""
    idx = pd.bdate_range("2021-06-01", "2026-10-08")
    n = len(idx)
    c = np.linspace(100, 300, n)
    c[-30:] = c[-31] * np.linspace(0.999, depth, 30)                 # 고점 뒤 얕은 눌림
    v = np.full(n, 1000.0)
    v[-30:] = 600.0
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": v}, index=idx)


def test_synthetic_both_O_is_trend_pullback():
    d = _trend_daily()
    months = ev.completed_months(ev.monthly_ohlc(d), "KR", datetime(2026, 10, 9, 7, 0, tzinfo=KST))
    items = {i["key"]: i for i in ev.pullback_checks(d, months)}
    assert items["ma20_rising"]["result"] is True and items["above_prior_low"]["result"] is True
    assert ev.pullback_class(list(items.values())) == "trend"
    # 관찰 경로(setup_type)도 같은 결과 — 합성 데이터는 A 미통과(하락 5%)라 눌림형
    s = w.setup_type(d, "2026-10-08", "KR", datetime(2026, 10, 9, 7, 0, tzinfo=KST).isoformat())
    assert (s["setup_type"], s["setup_class"]) == ("pullback", "trend")
    deep = w.setup_type(_trend_daily(0.95), "2026-10-08", "KR", datetime(2026, 10, 9, 7, 0, tzinfo=KST).isoformat())
    assert deep["setup_class"] == "down"                                                       # −5% 눌림 = 직전 저점 아래


def test_eval_returns_class_and_rank_review_classes(monkeypatch):
    d = _df("PEG")
    monkeypatch.setattr(ev, "fetch_ohlcv", lambda c, m: (d, ev.monthly_ohlc(d)))
    res = ev.evaluate("PEG", "US", datetime(2026, 10, 9, 7, 0, tzinfo=KST))
    assert res["pullback_class"] == "down" and res["pullback_class"] == ev.pullback_class(res["pullback_items"])
    # 순위 스냅샷·리뷰 — 4분류(판정 불가에 눌림형(판정 불가) 포함), 합 = 전체
    sn = rk.snapshot_item({"base_price": 1.0}, None, None, {"status": "active", "setup_type": "pullback", "setup_class": "trend"},
                          [], None, 60, 50)
    assert sn["setup_class"] == "trend"
    items = [{"watch_id": f"w{i}", "code": f"C{i}", "name": "n", "mkt": "KR", "base_date": "2026-10-08", "base_price": 1.0}
             for i in range(5)]
    recs = [{"id": "r", "tf": "week", "label": "2026-10-08", "items": items, "confirmed_at": None, "picks": {}, "snapshot": {},
             "results": {}}]
    watch = {"w0": {"setup_type": "bottom", "setup_class": "bottom"}, "w1": {"setup_type": "pullback", "setup_class": "trend"},
             "w2": {"setup_type": "pullback", "setup_class": "down"}, "w3": {"setup_type": "pullback", "setup_class": "pullback_unknown"},
             "w4": {}}
    rv = rk.review(recs, watch)
    assert {t: rv["by_type"][t]["total"]["n"] for t in rk.SETUP_CLASSES} == {"bottom": 1, "trend": 1, "down": 1, "unknown": 2}
    assert sum(rv["by_type"][t]["total"]["n"] for t in rk.SETUP_CLASSES) == rv["total"]["n"]


def test_judgement_untouched():
    import inspect
    for f in (w.stage_info, w.judge_kr_regular, w.reach_info, w.post_restart):
        src = inspect.getsource(f)
        assert "setup_class" not in src and "pullback_class" not in src
