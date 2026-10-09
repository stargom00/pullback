"""기준양봉 출발 측정(docs/bull_candle_departure_entry.md) — 출발 판정 고정.

사용자 지시: "전일 대비 +14.9% 양봉과 +15% 음봉을 가짜로 넣었을 때 둘 다 출발로 잡히지 않는지 확인한다. 잡히면 테스트 결함으로 보고
멈춘다." 정의(레포에 기준양봉 수치 정의 없음 → 사용자 지시 고정값): 종가 ≥ 전일 종가 × 1.15 그리고 종가 > 시가.

사보타주 확인(2026-10-09, FAIL 확인 후 원복): ① 양봉 조건(종가 > 시가) 제거 → test_fake_candles_not_departure · test_boundaries FAIL
② 배수 1.15 → 1.149 → test_fake_candles_not_departure FAIL
"""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("bull", os.path.join(ROOT, "scripts", "measurements", "2026-10-09_bull_candle_departure_entry.py"))
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def _df(rows):
    """rows: [(날짜, 시가, 종가)] — 고가·저가는 시가·종가 범위, 거래량 1."""
    idx = pd.to_datetime([r[0] for r in rows])
    o = [float(r[1]) for r in rows]
    c = [float(r[2]) for r in rows]
    return pd.DataFrame({"Open": o, "High": [max(a, b) for a, b in zip(o, c)], "Low": [min(a, b) for a, b in zip(o, c)],
                         "Close": c, "Volume": [1.0] * len(rows)}, index=idx)


BASE = [("2026-09-01", 100, 100), ("2026-09-02", 100, 100)]          # 기준일 09-02 종가 100


def test_fake_candles_not_departure():
    """+14.9% 양봉(100 → 114.9)과 +15% 음봉(시가 120 → 종가 115)은 출발이 아니다."""
    # 09-03 +14.9% 양봉 · 09-04 보합 · 09-07 전일 114.9 × 1.15 = 132.135에 0.0001 모자란 종가(음봉)
    df = _df(BASE + [("2026-09-03", 100, 114.9), ("2026-09-04", 114.9, 114.9), ("2026-09-07", 133.0, 132.1349)])
    v = m.harness.CleanView(df)
    assert m.bull_departure(v, "2026-09-02") is None
    df2 = _df(BASE + [("2026-09-03", 120, 115)])                                         # +15% 음봉
    assert m.bull_departure(m.harness.CleanView(df2), "2026-09-02") is None
    df3 = _df(BASE + [("2026-09-03", 100, 114.9)])                                       # +14.9% 양봉
    assert m.bull_departure(m.harness.CleanView(df3), "2026-09-02") is None


@pytest.mark.parametrize("prev,o,c,want", [
    (100, 100, 115, True),        # 정확히 +15% 양봉 — 출발
    (100, 100, 114.9, False),     # +14.9%
    (100, 116, 115, False),       # +15% 음봉
    (100, 115, 115, False),       # +15% 도지(종가 = 시가) — 양봉 아님
    (100, 90, 130, True),
    (0, 1, 2, False),
])
def test_boundaries(prev, o, c, want):
    assert m.is_bull_candle(prev, o, c) is want


def test_first_bull_after_base_only():
    """기준일 당일 +15% 양봉은 안 본다. 기준일 다음부터 첫 기준양봉의 인덱스."""
    df = _df([("2026-09-01", 100, 100), ("2026-09-02", 100, 120),                       # 기준일이 +20% 양봉
              ("2026-09-03", 120, 121), ("2026-09-04", 121, 140), ("2026-09-07", 140, 170)])
    k = m.bull_departure(m.harness.CleanView(df), "2026-09-02")
    assert str(df.index[k].date()) == "2026-09-04"                                      # 121 → 140(+15.7%) 첫 봉


def test_invalid_zero_bar_skipped_and_prev_close_from_clean_prefix():
    """무거래일 OHLC=0 봉(종가만 전일 이월)은 건너뛰고, 그 다음 봉의 전일 종가는 정제 df의 직전 봉 종가."""
    df = _df(BASE + [("2026-09-03", 100, 100), ("2026-09-04", 0, 100), ("2026-09-07", 100, 116)])
    df.loc[pd.Timestamp("2026-09-04"), ["High", "Low"]] = [0.0, 0.0]
    v = m.harness.CleanView(df)
    assert v.dirty and not v.valid(3)
    k = m.bull_departure(v, "2026-09-02")
    assert str(df.index[k].date()) == "2026-09-07"


def test_run_window_accepts_upper_kr_list_and_params():
    kst = timezone(timedelta(hours=9))
    import harness
    assert harness.run_window_ok(["KR"], datetime(2026, 10, 9, 12, 0, tzinfo=kst))[0] is True        # 휴장일
    assert harness.run_window_ok(["KR"], datetime(2026, 10, 8, 12, 0, tzinfo=kst))[0] is False       # 거래일 장중
    assert m.RUN_MARKETS == ["KR"] and m.BULL_CLOSE_RATIO == 1.15 and m.Z_MIN == 2.24 and m.EV_MIN == 0.15 and m.N_MIN == 100
    assert m.prev.MAX_BARS == 60 and m.prev.KR_DAYS == 1900                               # 직전 측정과 같은 레이스·깊이
