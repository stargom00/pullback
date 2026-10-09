"""종가베팅 채택 재현 검증(docs/jongga_adoption_clean_revalidation.md) — 정제 접근 계층 고정.

사용자 지시: "OHLC=0 행을 1건 인위로 넣은 데이터로 돌렸을 때 CleanView가 그 행을 빼는지 확인한다. 안 빼면 테스트 결함으로 보고 멈춘다."
원 판정 코드(2026-08-29 확장 스크립트의 evaluate·turnover_rank_at)는 그대로 두고 harness.truncate_at·future_after만 정제판으로 바꾼다.

사보타주 확인(2026-10-09, FAIL 확인 후 원복): CleanAccess.truncate_at·future_after가 정제 없이 원본을 돌려줌 →
test_invalid_T_bar_excluded · test_invalid_T1_bar_skipped_to_next_valid FAIL
"""
from __future__ import annotations

import importlib.util
import os

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("jrv", os.path.join(ROOT, "scripts", "measurements",
                                                                    "2026-10-09_jongga_adoption_clean_revalidation.py"))
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def _df(n=400, start=100.0):
    idx = pd.bdate_range("2024-01-01", periods=n)
    c = start * (1 + 0.001 * np.arange(n))
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": np.full(n, 1e6)}, index=idx)


def _zero(df, i):
    df = df.copy()
    df.iloc[i, df.columns.get_indexer(["Open", "High", "Low"])] = 0.0          # 무거래일 이월 패턴(종가만 남음)
    return df


def _eval(data, off, clean):
    rank = {}
    if clean:
        with m._Patched(m.CleanAccess(data)):
            rank = m.orig.turnover_rank_at(data, off)
            return m.orig.evaluate(data, off, rank), rank
    rank = m.orig.turnover_rank_at(data, off)
    return m.orig.evaluate(data, off, rank), rank


def test_invalid_T_bar_excluded():
    """off=5의 T봉(=iloc[-6])이 OHLC=0 → 정제판은 그 종목을 순위·평가에서 뺀다. 원 방법은 그대로 쓴다."""
    data = {"BAD.KQ": _zero(_df(), -6), "OK.KS": _df()}
    raw, raw_rank = _eval(data, 5, clean=False)
    cln, cln_rank = _eval(data, 5, clean=True)
    assert {r["ticker"] for r in raw} == {"BAD.KQ", "OK.KS"} and "BAD.KQ" in raw_rank
    assert {r["ticker"] for r in cln} == {"OK.KS"} and "BAD.KQ" not in cln_rank
    acc = m.CleanAccess(data)
    assert acc.truncate_at(data["BAD.KQ"], 5).empty and acc.future_after(data["BAD.KQ"], 5).empty


def test_invalid_T1_bar_skipped_to_next_valid():
    """T+1(=iloc[-5])이 OHLC=0 → 원 방법은 시가 0으로 갭 −100%, 정제판은 다음 유효봉 시가로 판다."""
    df = _zero(_df(), -5)
    data = {"BAD.KQ": df}
    raw, _ = _eval(data, 5, clean=False)
    cln, _ = _eval(data, 5, clean=True)
    assert raw[0]["gap_open"] == -1.0                                              # 201490.KQ 2025-06-23과 같은 모양
    close_t, open_next_valid = float(df["Close"].iloc[-6]), float(df["Open"].iloc[-4])
    assert abs(cln[0]["gap_open"] - (open_next_valid / close_t - 1)) < 1e-6        # 정제 = float32 캐스트 포함(운영 _downcast)
    fut = m.CleanAccess(data).future_after(df, 5)
    assert fut.index[0] == df.index[-4] and len(fut) == 4


def test_no_invalid_rows_means_identical_to_raw():
    data = {"A.KS": _df(), "B.KQ": _df(start=50.0)}
    for off in (5, 20, 100):
        raw, rr = _eval(data, off, clean=False)
        cln, cr = _eval(data, off, clean=True)
        assert rr == cr and [r["ticker"] for r in raw] == [r["ticker"] for r in cln]
        assert all(abs(a["gap_open"] - b["gap_open"]) < 1e-6 for a, b in zip(raw, cln))  # float32 캐스트 차이만


def test_patch_is_scoped_and_judgment_params_are_original():
    import harness
    before = (harness.truncate_at, harness.future_after)
    with m._Patched(m.CleanAccess({"A.KS": _df()})):
        assert harness.truncate_at is not before[0]
    assert (harness.truncate_at, harness.future_after) == before                   # 블록 밖에선 원래 함수
    assert m.orig.OFFSETS == harness.checkpoints(60, 950, 10) and m.orig.ROUND_TRIP_COST == 0.003
    assert m.NET_MIN == 0.005 and m.Z_MIN == 1.96 and m.RUN_MARKETS == ["KR"]
