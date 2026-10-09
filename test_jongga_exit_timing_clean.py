"""종가베팅 매도 타이밍 정제 재측정(docs/jongga_exit_timing_clean.md) — 정제 적용·판정 상수 고정.

사용자 지시: "사보타주: OHLC=0 행을 1건 주입했을 때 정제가 그 행을 빼는지 확인한다."
조합 A를 통과하는 합성 종목의 T+1 봉을 OHLC=0(무거래일 이월 모양)으로 만들고, 정제판 (a)·(d)가 다음 유효봉에서 나오는지 본다.

사보타주 확인(2026-10-09, FAIL 확인 후 원복): collect()에서 정제 패치를 빼면(ctx = None) →
test_injected_zero_T1_removed_by_clean FAIL.
"""
from __future__ import annotations

import importlib.util
import os

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("jxc", os.path.join(ROOT, "scripts", "measurements",
                                                                    "2026-10-09_jongga_exit_timing_clean.py"))
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

OFF = 5


def _combo_df(n=400):
    """T(= iloc[-1-OFF])가 조합 A(+5% 양봉·고가 마감·거래량 3배·MA20 위·52주 고가 근처)를 통과하는 상승 종목."""
    idx = pd.bdate_range("2024-01-01", periods=n)
    c = 100.0 * (1 + 0.001 * np.arange(n))
    df = pd.DataFrame({"Open": c, "High": c * 1.005, "Low": c * 0.99, "Close": c, "Volume": np.full(n, 1e6)}, index=idx)
    t = n - 1 - OFF
    close_t = float(df["Close"].iloc[t - 1]) * 1.05
    df.iloc[t, :] = [close_t * 0.97, close_t, close_t * 0.96, close_t, 3e6]
    for k in range(t + 1, n):                                   # T 이후는 구분되는 값(어느 봉에서 팔았는지 보이게)
        o = close_t * (1 + 0.05 * (k - t))                     # 익일 갭 +5% — 게이트 기준(+0.796%)과 확실히 떨어지게
        df.iloc[k, :] = [o, o * 1.03, o * 0.99, close_t * (1 + 0.06 * (k - t)), 1e6]
    return df


def _zero(df, i):
    df = df.copy()
    df.iloc[i, df.columns.get_indexer(["Open", "High", "Low"])] = 0.0
    return df


def _collect(data, clean):
    return m.collect(data, [OFF, OFF + 10], clean=clean)


def test_fixture_passes_combo_a():
    recs = _collect({"OK.KS": _combo_df()}, clean=True)
    assert [r["ticker"] for r in recs if r["off"] == OFF] == ["OK.KS"]


def test_injected_zero_T1_removed_by_clean():
    """T+1(= iloc[-OFF])을 OHLC=0으로 주입 → 원 방법은 시가 0(−100%)을 쓰고, 정제판은 그 행을 빼고 다음 유효봉의 시가·종가로 판다."""
    df = _zero(_combo_df(), -OFF)
    data = {"BAD.KQ": df}
    raw = [r for r in _collect(data, clean=False) if r["off"] == OFF]
    cln = [r for r in _collect(data, clean=True) if r["off"] == OFF]
    assert len(raw) == 1 and raw[0]["open_t1"] == 0.0 and raw[0]["gap_open"] == -1.0
    assert len(cln) == 1
    r = cln[0]
    nxt = df.index[-OFF + 1]
    assert r["date_t1"] == str(nxt.date()) and r["date_t1"] != str(df.index[-OFF].date())
    close_t = float(df["Close"].iloc[-1 - OFF])
    assert abs(r["gap_open"] - (float(df["Open"].iloc[-OFF + 1]) / close_t - 1)) < 1e-6     # float32 캐스트 차이만
    assert abs(r["gap_close"] - (float(df["Close"].iloc[-OFF + 1]) / close_t - 1)) < 1e-6
    m.judge(cln * 1)                                            # 정제 레코드는 가격 ≤ 0 하드 실패에 안 걸린다


def test_judge_hard_fails_on_zero_price_record():
    df = _zero(_combo_df(), -OFF)
    raw = [r for r in _collect({"BAD.KQ": df}, clean=False) if r["off"] == OFF]
    with pytest.raises(SystemExit):
        m.judge(raw)


def test_gate_outside_tolerance_is_not_judged():
    recs = [r for r in _collect({"OK.KS": _combo_df()}, clean=True) if r["off"] == OFF]
    res = m.judge(recs)                                          # 합성 갭은 +0.796%와 멀다 → 판정 안 함
    assert res["validity_gate"]["pass"] is False and res["passed"] is None and res["checks"] is None


def test_params_are_registered_values():
    assert m.Z_MIN == 2.24 and m.GATE_TOL == 0.0015 and m.MIN_DIFF == 0.0030 and m.MIN_N == 100
    assert abs(m.GATE_REF_EV - 0.00796) < 1e-5 and m.xt.ROUND_TRIP_COST == 0.003
    assert m.RUN_MARKETS == ["KR"]
    assert m.xt.orig.OFFSETS == m.harness.checkpoints(60, 950, 10)
