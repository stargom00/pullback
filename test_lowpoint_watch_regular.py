"""v5.333 — 저점 관찰 KR 도달 판정 = 정규장(09:00~15:30) 고가(naver 분봉). 장외 체결 제외, 결측일은 판정 보류.

경위: 꿈비(407400.KQ) 10-02 주봉 코호트(기준가 1,969)가 10-06 "도달"로 분류됐는데 그 고가 2,115는 넥스트레이드 애프터마켓
16:04의 1주 체결이었다(정규장 최고 2,010 = +2.1%). 사용자 결정: "정규장 장중 고가는 인정하고, 장외(프리·애프터) 체결은
판정에서 뺀다 … naver 분봉 09:00~15:30 최고가 … yfinance 사용 금지 … 분봉을 못 받은 날은 판정하지 않고 다음 실행 때 다시
시도. 통합 일봉 고가로 대체하는 폴백은 금지." 꿈비 실제 분봉 203개는 test_fixtures/kumbi_20261006_minutes.json.gz
(2026-10-07 수집 — naver 분봉은 최근 6거래일만 남아 지금 저장해 둠).

사보타주 확인(2026-10-07, FAIL 확인 후 원복):
① 정규장 고가 대신 통합 일봉 고가(daily High)로 판정 → test_kumbi_reproduction·test_after_hours_single_share_not_counted FAIL
② 정규장 구간을 15:30 → 20:00으로 늘림 → test_kumbi_reproduction·test_after_hours_single_share_not_counted FAIL
③ 분봉 결측 시 통합 일봉 고가로 폴백 → test_missing_minutes_withhold_judgement FAIL
"""
from __future__ import annotations

import asyncio
import gzip
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.join(ROOT, "scripts", "screens"), os.path.join(ROOT, "scripts", "measurements")):
    sys.path.insert(0, p)
import lowpoint_watch as w  # noqa: E402

import app  # noqa: E402

KST = timezone(timedelta(hours=9))
FX = json.load(gzip.open(os.path.join(ROOT, "test_fixtures", "kumbi_20261006_minutes.json.gz"), "rt", encoding="utf-8"))


def _daily(rows):
    return pd.DataFrame({k: [r[k] for r in rows.values()] for k in ("Open", "High", "Low", "Close", "Volume")},
                        index=pd.to_datetime(list(rows)))


def _rec(base_price=1969.0, base_date="2026-10-02", code="407400.KQ", **k):
    return {"id": "w_week_2026-10-02_" + code, "code": code, "mkt": "KR", "base_price": base_price, "base_date": base_date,
            "status": "active", "label": "2026-10-02", "tf": "week", **k}


def _bar(day, hhmmss, high):
    return {"localDateTime": day.replace("-", "") + hhmmss, "highPrice": high, "openPrice": high, "lowPrice": high,
            "currentPrice": high, "accumulatedTradingVolume": 1}


def test_kumbi_reproduction():
    """실제 10-06 분봉: 정규장 최고 2,010 < 1,969×1.05 = 2,067.45 → 미도달(애프터 16:04 2,115·18:36 2,075는 제외)."""
    assert w.regular_high(FX["minutes"]["2026-10-06"]) == 2010.0
    assert max(b["highPrice"] for b in FX["minutes"]["2026-10-06"]) == 2115.0          # 전제: 통합으로는 넘었다
    daily = _daily(FX["daily"])
    assert float(daily.loc["2026-10-06", "High"]) == 2115.0
    got = w.judge_kr_regular(_rec(), daily, date(2026, 10, 7), False, fetch_min=lambda c, d: FX["minutes"][str(d)])
    assert got["reached"] is False and got["regular_checked_through"] == "2026-10-06" and got["pending_day"] is None


def test_intraday_over_then_close_below_counts():
    rows = {"2026-10-02": dict(Open=1940, High=1987, Low=1940, Close=1969, Volume=1),
            "2026-10-06": dict(Open=1970, High=2080, Low=1969, Close=2000, Volume=1)}
    bars = [_bar("2026-10-06", "090100", 1975), _bar("2026-10-06", "110000", 2070), _bar("2026-10-06", "153000", 2000)]
    got = w.judge_kr_regular(_rec(), _daily(rows), date(2026, 10, 7), False, fetch_min=lambda c, d: bars)
    assert got["reached"] and got["reached_date"] == "2026-10-06" and got["reached_high"] == 2070
    assert got["reached_pct"] == round((2070 / 1969 - 1) * 100, 2) and got["reached_days"] == 4


def test_after_hours_single_share_not_counted():
    rows = {"2026-10-06": dict(Open=1970, High=2115, Low=1969, Close=2045, Volume=1)}
    bars = [_bar("2026-10-06", "090000", 1970), _bar("2026-10-06", "152900", 2010), _bar("2026-10-06", "153000", 2005),
            _bar("2026-10-06", "160400", 2115), _bar("2026-10-06", "083000", 2100)]          # 프리 08:30 · 애프터 16:04
    got = w.judge_kr_regular(_rec(), _daily(rows), date(2026, 10, 7), False, fetch_min=lambda c, d: bars)
    assert got["reached"] is False and got["regular_checked_through"] == "2026-10-06"


@pytest.mark.parametrize("hi,reached", [(1969 * 1.05, True), (2066.45, False)])
def test_boundary_exact_threshold(hi, reached):
    rows = {"2026-10-06": dict(Open=1, High=3000, Low=1, Close=1, Volume=1)}
    got = w.judge_kr_regular(_rec(), _daily(rows), date(2026, 10, 7), False,
                             fetch_min=lambda c, d: [_bar("2026-10-06", "100000", hi)])
    assert got["reached"] is reached


@pytest.mark.parametrize("missing", [None, []])
def test_missing_minutes_withhold_judgement(missing):
    """분봉을 못 받은 거래일에서 멈춘다 — 도달도 미도달 확정도 아니고, 뒷날은 보지 않는다(도달일이 틀어지지 않게)."""
    rows = {"2026-10-06": dict(Open=1, High=2115, Low=1, Close=2045, Volume=1),
            "2026-10-07": dict(Open=1, High=2200, Low=1, Close=2100, Volume=1)}
    asked = []

    def fm(code, day):
        asked.append(str(day))
        return missing if str(day) == "2026-10-06" else [_bar(str(day), "100000", 2200)]
    got = w.judge_kr_regular(_rec(regular_checked_through="2026-10-02"), _daily(rows), date(2026, 10, 8), False, fetch_min=fm)
    assert got["reached"] is False and got["pending_day"] == "2026-10-06"
    assert got["regular_checked_through"] == "2026-10-02" and asked == ["2026-10-06"]
    # 다음 실행에 분봉이 오면 그 날부터 이어서 판정
    got2 = w.judge_kr_regular(_rec(regular_checked_through=got["regular_checked_through"]), _daily(rows), date(2026, 10, 8),
                              False, fetch_min=lambda c, d: [_bar(str(d), "100000", 2010 if str(d) == "2026-10-06" else 2200)])
    assert got2["reached"] and got2["reached_date"] == "2026-10-07" and got2["pending_day"] is None


def test_base_day_and_checked_days_not_refetched():
    rows = {"2026-10-02": dict(Open=1, High=9999, Low=1, Close=1969, Volume=1),
            "2026-10-06": dict(Open=1, High=1, Low=1, Close=1, Volume=1),
            "2026-10-07": dict(Open=1, High=1, Low=1, Close=1, Volume=1)}
    asked = []
    w.judge_kr_regular(_rec(regular_checked_through="2026-10-06"), _daily(rows), date(2026, 10, 8), False,
                       fetch_min=lambda c, d: asked.append(str(d)) or [_bar(str(d), "100000", 1990)])
    assert asked == ["2026-10-07"]                         # 기준일(10-02)·이미 판정한 10-06은 안 본다


def test_today_counts_only_after_regular_close():
    rows = {"2026-10-07": dict(Open=1, High=1, Low=1, Close=1, Volume=1)}
    fm = lambda c, d: [_bar("2026-10-07", "100000", 2000)]
    during = w.judge_kr_regular(_rec(), _daily(rows), date(2026, 10, 7), False, fetch_min=fm)
    after = w.judge_kr_regular(_rec(), _daily(rows), date(2026, 10, 7), True, fetch_min=fm)
    assert during["regular_checked_through"] is None and after["regular_checked_through"] == "2026-10-07"
    hit = w.judge_kr_regular(_rec(), _daily(rows), date(2026, 10, 7), False, fetch_min=lambda c, d: [_bar("2026-10-07", "100000", 2100)])
    assert hit["reached"]                                    # 장중이라도 정규장에서 넘었으면 인정
    assert w._session_done_today("2026-10-07T15:31:00+09:00") and not w._session_done_today("2026-10-07T15:29:00+09:00")


def test_old_reached_records_rejudged_and_reverted():
    """옛 규칙(통합 고가)으로 도달된 레코드 → 다음 추적에서 정규장 고가로 다시 판정, 미도달이면 관찰로 복귀."""
    old = _rec(status="reached", reached_date="2026-10-06", reached_days=4)
    daily = _daily(FX["daily"])
    fetch = lambda recs, today: {r["code"]: daily for r in recs}
    up, s = w.track([old], date(2026, 10, 7), "2026-10-07T07:00:00+09:00", fetch=fetch,
                    fetch_min=lambda c, d: FX["minutes"][str(d)])
    u = up[old["id"]]
    assert u["status"] == "active" and u["reached_date"] is None and u["reach_rule"] == w.REACH_RULE
    assert s["reverted"] == ["407400.KQ(2026-10-02 week)"] and s["rejudged"] == 1
    again = {**old, **u}
    no_min = lambda c, d: (_ for _ in ()).throw(AssertionError("새 규칙 도달은 다시 안 본다"))
    up2, s2 = w.track([{**again, "status": "reached", "reach_rule": w.REACH_RULE, "stage": "invalid"}], date(2026, 10, 7),
                      "x", fetch=fetch, fetch_min=no_min)
    assert up2 == {} and s2["rejudged"] == 0                    # v5.337: 종료(재출발·무효)는 조회 자체를 안 한다
    up3, s3 = w.track([{**again, "status": "reached", "reach_rule": w.REACH_RULE, "stage": "resting",
                        "reached_date": "2026-10-06", "reached_high": 2070.0}], date(2026, 10, 7),
                      "2026-10-07T07:00:00+09:00", fetch=fetch, fetch_min=no_min)
    assert s3["rejudged"] == 0 and s3["staged"] == 1 and up3[old["id"]]["status"] == "reached"   # 단계만 — 분봉 없음


def test_app_job_reverts_and_logs(monkeypatch, tmp_path, capsys):
    for k, v in (("LOWPOINT_DATA_PATH", "d.json"), ("LOWPOINT_LATEST_PATH", "r.json"), ("LOWPOINT_STATE_PATH", "s.json")):
        monkeypatch.setattr(app, k, str(tmp_path / v))
    old = {**_rec(status="reached", reached_date="2026-10-06", reached_days=4), "rev": 1, "name": "꿈비", "market": "KOSDAQ"}
    app._rec_list_write(app.LP_WATCH_PATH, [old])
    daily = _daily(FX["daily"])
    monkeypatch.setattr(w.track, "__defaults__", (lambda recs, today: {r["code"]: daily for r in recs},
                                                  lambda c, d: FX["minutes"][str(d)]))
    app._lp_watch_job_blocking("watch", datetime(2026, 10, 7, 7, 0, tzinfo=KST))
    rec = app._rec_list_load(app.LP_WATCH_PATH)[0]
    assert rec["status"] == "active" and rec["rev"] == 2 and rec["regular_checked_through"] == "2026-10-06"
    out = capsys.readouterr().out
    assert "정규장 고가 재판정 → 관찰 복귀 1건: 407400.KQ(2026-10-02 week)" in out


def test_us_still_uses_daily_high():
    rec = {**_rec(code="AVA"), "mkt": "US", "base_price": 35.0}
    daily = _daily({"2026-10-06": dict(Open=35, High=36.75, Low=35, Close=35.5, Volume=1)})
    up, _ = w.track([rec], date(2026, 10, 7), "x", fetch=lambda recs, t: {"AVA": daily},
                    fetch_min=lambda c, d: (_ for _ in ()).throw(AssertionError("US는 분봉을 안 받는다")))
    u = up[rec["id"]]
    assert u["status"] == "reached" and u["reached_high"] == 36.75 and u["reached_pct"] == 5.0


def test_no_yfinance_or_daily_high_in_kr_judgement():
    import inspect
    src = inspect.getsource(w.judge_kr_regular).split('"""')[2]
    assert "High" not in src and "harness" not in src and "yf" not in src
    assert w.KR_REGULAR_HM == ("090000", "153000")
    assert "api.stock.naver.com/chart/domestic/item/" in w.MINUTE_URL


def test_ui_shows_rule_and_reach_evidence():
    html = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
    assert "정규장 고가 기준(장외 체결 제외)" in html and "기준일 다음 거래일부터 일봉 고가로 판정" not in html
    # v5.337: 도달 고가는 "출발 고가"(숨고르기·종료 표) — KR은 정규장 고가임을 툴팁으로 남긴다
    assert html.count('<th class="r">출발 고가</th>') == 2 and "'정규장 고가(장외 체결 제외)'" in html
    assert "r.reached_high" in html and "r.reached_pct" in html and html.count("lpwPendingHtml(r)") >= 2
