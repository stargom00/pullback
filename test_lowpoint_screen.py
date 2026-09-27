"""scripts/screens/lowpoint.py(저점종목 주간·월간 스크린) 테스트.

- 조건 A/B 경계: RSI 30.0 정확히, 종가 동일
- 진행 중 봉 제거: 주중 실행 결과 == 직전 주말 실행 결과(불변성)
- 주·월 리샘플 경계: 금요일 휴장, 월말 주말, 월 경계, 확정 시각 1분 전/후
- KR 확정 시각 사본이 app.py `KR_CLOSE_CONFIRMED_HM`과 같은지

사보타주 확인(2026-09-27): ① lowpoint_signal에서 `r2 >= RSI_LEVEL` 제거
② drop_in_progress가 bars를 그대로 반환 ③ A 부등호 반전(c1 > c0) —
세 경우 모두 이 파일에서 FAIL함을 확인하고 원복했다. ①이 처음엔 경계 단위 테스트
1건에만 걸려, 종목 흐름 전체를 도는 `test_rsi2_already_below_30_no_signal_end_to_end`를
추가했다(2건 FAIL로 재확인).
"""
import os
import re
import sys
from datetime import datetime

import pandas as pd
import pytest

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_ROOT, "scripts", "screens"))
sys.path.insert(0, _ROOT)

import lowpoint as lp  # noqa: E402
from scanner import rsi  # noqa: E402

KST, ET = lp.KST, lp.ET


# ── 조건 A/B 경계 ─────────────────────────────────────────────────────

def test_b_rsi2_exactly_30_passes():
    assert lp.lowpoint_signal(c0=101, c1=100, r1=29.99, r2=30.0) is True


def test_b_rsi2_below_30_fails():
    # RSI[2] >= 30 조건이 빠지면 이미 30 아래에 있던 종목이 잡힌다
    assert lp.lowpoint_signal(c0=101, c1=100, r1=25.0, r2=29.99) is False


def test_b_rsi1_exactly_30_fails():
    assert lp.lowpoint_signal(c0=101, c1=100, r1=30.0, r2=35.0) is False


def test_a_equal_close_fails():
    assert lp.lowpoint_signal(c0=100, c1=100, r1=29.0, r2=31.0) is False


def test_a_direction():
    assert lp.lowpoint_signal(c0=100.01, c1=100, r1=29.0, r2=31.0) is True
    assert lp.lowpoint_signal(c0=99.99, c1=100, r1=29.0, r2=31.0) is False


# ── 합성 시리즈 end-to-end (리샘플 → 진행 중 봉 제거 → scanner.rsi → 판정) ──

def _weekly_path(extra_drops=0):
    """주봉 종가: 60봉 진동 후 3씩 하락해 RSI 30 하향돌파, 마지막 봉 +0.5.
    extra_drops>0이면 돌파 후 그만큼 더 하락한 뒤 반등 — 1봉전 RSI가 30 아래지만
    2봉전도 이미 30 아래라 '하향돌파'가 아니다."""
    w = [100 + (2 if i % 2 else -2) + i * 0.1 for i in range(60)]
    last = w[-1]
    while True:
        last -= 3
        w.append(last)
        if rsi(pd.Series(w), 14).iloc[-1] < 30:
            break
    for _ in range(extra_drops):
        last -= 3
        w.append(last)
    w.append(last + 0.5)
    return w


def _daily_from_weekly(weekly, first_monday="2025-01-06"):
    idx, vals = [], []
    mondays = pd.date_range(first_monday, periods=len(weekly), freq="W-MON")
    for mon, c in zip(mondays, weekly):
        for d in range(5):
            idx.append(mon + pd.Timedelta(days=d))
            vals.append(c)
    return pd.Series(vals, index=pd.DatetimeIndex(idx))


def test_fixture_preconditions():
    r = rsi(pd.Series(_weekly_path()), 14)
    assert r.iloc[-3] >= 30 and r.iloc[-2] < 30  # B 성립하도록 만든 경로인지


def test_hit_and_in_progress_week_dropped():
    weekly = _weekly_path()
    daily = _daily_from_weekly(weekly)
    last_fri = daily.index[-1]
    # 다음 주 월~수: 급락(진행 중 봉). 포함되면 A가 깨지고 RSI 창도 밀린다.
    nxt = pd.date_range(last_fri + pd.Timedelta(days=3), periods=3, freq="D")
    daily_mid = pd.concat([daily, pd.Series(50.0, index=nxt)])
    wed = nxt[-1]
    now_mid = datetime(wed.year, wed.month, wed.day, 23, 0, tzinfo=KST)
    sat = last_fri + pd.Timedelta(days=1)
    now_sat = datetime(sat.year, sat.month, sat.day, 9, 0, tzinfo=KST)

    mid = lp.evaluate(daily_mid, "week", "kr", now_mid)
    weekend = lp.evaluate(daily, "week", "kr", now_sat)
    assert mid["status"] == "hit"
    assert mid["bar_date"] == last_fri
    assert {k: mid[k] for k in ("label", "c0", "c1", "r2", "r1", "r0")} == \
           {k: weekend[k] for k in ("label", "c0", "c1", "r2", "r1", "r0")}


def test_rsi2_already_below_30_no_signal_end_to_end():
    # 종목 흐름 전체(리샘플→진행 중 봉 제거→scanner.rsi→판정)에서 B의 RSI[2]>=30 확인
    daily = _daily_from_weekly(_weekly_path(extra_drops=1))
    fri = daily.index[-1]
    now = datetime(fri.year, fri.month, fri.day, 21, 0, tzinfo=KST)
    res = lp.evaluate(daily, "week", "kr", now)
    assert res["bar_date"] == fri
    assert res["c1"] < res["c0"] and res["r1"] < 30 and res["r2"] < 30  # 픽스처 전제
    assert res["status"] == "no"


def test_friday_before_confirm_time_drops_that_week():
    daily = _daily_from_weekly(_weekly_path())
    fri = daily.index[-1]
    before = datetime(fri.year, fri.month, fri.day, 20, 9, tzinfo=KST)
    after = datetime(fri.year, fri.month, fri.day, 20, 10, tzinfo=KST)
    assert lp.evaluate(daily, "week", "kr", after)["bar_date"] == fri
    res_before = lp.evaluate(daily, "week", "kr", before)
    assert res_before["bar_date"] == fri - pd.Timedelta(days=7)
    assert res_before["status"] == "no"  # 한 주 당기면 신호 봉이 아니다


# ── 리샘플·마감 경계 ─────────────────────────────────────────────────

def test_week_friday_holiday_bar_date_is_thursday():
    idx = pd.to_datetime(["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17"])  # 금 휴장
    bars = lp.resample_bars(pd.Series([1.0, 2.0, 3.0, 4.0], index=idx), "week")
    assert list(bars.index) == [pd.Timestamp("2026-09-18")]
    assert bars["bar_date"].iloc[0] == pd.Timestamp("2026-09-17")
    assert bars["close"].iloc[0] == 4.0
    # 목요일 밤에는 아직 금요일 확정 전 → 진행 중으로 버린다
    thu = datetime(2026, 9, 17, 23, 0, tzinfo=KST)
    sat = datetime(2026, 9, 19, 9, 0, tzinfo=KST)
    assert len(lp.drop_in_progress(bars, "kr", thu)) == 0
    assert len(lp.drop_in_progress(bars, "kr", sat)) == 1


def test_week_boundary_monday_starts_new_bar():
    idx = pd.to_datetime(["2026-09-11", "2026-09-14"])  # 금, 다음 월
    bars = lp.resample_bars(pd.Series([1.0, 2.0], index=idx), "week")
    assert list(bars["close"]) == [1.0, 2.0]


def test_month_boundary_and_weekend_month_end():
    # 2026-01-30(금), 2026-01-31(토=말일), 2026-02-02(월)
    idx = pd.to_datetime(["2026-01-29", "2026-01-30", "2026-02-02"])
    bars = lp.resample_bars(pd.Series([1.0, 2.0, 3.0], index=idx), "month")
    assert list(bars.index) == [pd.Timestamp("2026-01-31"), pd.Timestamp("2026-02-28")]
    assert bars["bar_date"].iloc[0] == pd.Timestamp("2026-01-30")
    assert list(bars["close"]) == [2.0, 3.0]
    feb_mid = datetime(2026, 2, 15, 12, 0, tzinfo=KST)
    kept = lp.drop_in_progress(bars, "kr", feb_mid)
    assert list(kept.index) == [pd.Timestamp("2026-01-31")]
    # 말일(토) 20:10 KST 이전이면 1월 봉도 아직 진행 중
    assert len(lp.drop_in_progress(bars, "kr", datetime(2026, 1, 31, 20, 9, tzinfo=KST))) == 0
    assert len(lp.drop_in_progress(bars, "kr", datetime(2026, 1, 31, 20, 10, tzinfo=KST))) == 1


def test_us_confirm_time_follows_dst():
    lbl = pd.Timestamp("2026-07-31")  # EDT
    assert not lp.is_bar_closed(lbl, "us", datetime(2026, 7, 31, 16, 59, tzinfo=ET))
    assert lp.is_bar_closed(lbl, "us", datetime(2026, 7, 31, 17, 0, tzinfo=ET))
    lbl = pd.Timestamp("2026-01-30")  # EST — KST로는 다음날 07:00
    assert not lp.is_bar_closed(lbl, "us", datetime(2026, 1, 31, 6, 59, tzinfo=KST))
    assert lp.is_bar_closed(lbl, "us", datetime(2026, 1, 31, 7, 0, tzinfo=KST))


def test_month_in_progress_invariance():
    # 월봉도 월중 실행 == 직전 월말 이후 실행
    weekly = _weekly_path()
    daily = _daily_from_weekly(weekly)
    end = daily.index[-1]
    month_end = end + pd.offsets.MonthEnd(0)
    after_me = month_end + pd.Timedelta(days=1)
    now_a = datetime(after_me.year, after_me.month, after_me.day, 12, tzinfo=KST)
    extra = pd.date_range(month_end + pd.Timedelta(days=1), periods=10, freq="B")
    daily_mid = pd.concat([daily, pd.Series(10.0, index=extra)])
    x = extra[-1]
    now_b = datetime(x.year, x.month, x.day, 23, tzinfo=KST)
    lp_min = lp.MIN_BARS["month"]
    try:
        lp.MIN_BARS["month"] = 3  # 합성 경로가 16개월뿐이라 번인 게이트만 낮춤
        a = lp.evaluate(daily, "month", "kr", now_a)
        b = lp.evaluate(daily_mid, "month", "kr", now_b)
    finally:
        lp.MIN_BARS["month"] = lp_min
    assert a["label"] == b["label"] == month_end.normalize()
    assert (a["c0"], a["r1"]) == (b["c0"], b["r1"])


def test_short_history_reported_not_evaluated():
    daily = _daily_from_weekly(_weekly_path()[:30])
    fri = daily.index[-1] + pd.Timedelta(days=1)
    res = lp.evaluate(daily, "week", "kr", datetime(fri.year, fri.month, fri.day, tzinfo=KST))
    assert res["status"] == "short" and res["n_bars"] == 30


def test_kr_confirm_time_matches_app():
    src = open(os.path.join(_ROOT, "app.py"), encoding="utf-8").read()
    m = re.search(r"^KR_CLOSE_CONFIRMED_HM\s*=\s*(\d+)\s*\*\s*60\s*\+\s*(\d+)", src, re.M)
    assert m, "app.py KR_CLOSE_CONFIRMED_HM 정의를 못 찾음"
    assert lp.KR_CLOSE_CONFIRMED_HM == int(m.group(1)) * 60 + int(m.group(2))
