"""_filter_invalid_bars()/_downcast() 무효봉 배제 + 갈래B 트렁케이션 검증
(v5.242, 사용자 지시 — 데이터 소스 오염 방어).

핵심 검증 대상:
1. 경계값(off-by-one) — 4거래일 연속 무효 vs 5거래일(_GAP_TRUNCATE_MIN_RUN)
   연속 무효에서 처리(제거만 vs 트렁케이션)가 정확히 갈리는지.
2. 정상 데이터 오탐 없음(false-positive guard).
3. 필터링 후에도 rolling/ATR류 계산이 죽지 않는지.
4. 실제 007610.KS 프로덕션 캐시 데이터로 검증 — 합성 데이터만으로 끝내지
   말라는 사용자 지시.
"""
import os
import pickle

import numpy as np
import pandas as pd
import pytest

import app
import scanner


def _make_valid_df(n=30, start_price=1000.0):
    dates = pd.bdate_range("2026-01-02", periods=n)
    price = start_price + np.arange(n) * 1.0
    return pd.DataFrame({
        "Open": price, "High": price + 10, "Low": price - 10,
        "Close": price, "Volume": np.full(n, 100000.0),
    }, index=dates)


def _zero_out(df, idx_slice):
    df = df.copy()
    df.loc[df.index[idx_slice], ["Open", "High", "Low"]] = 0.0
    # Close는 naver 실측과 동일하게 전일가 이월(0이 아님) — Close만 보는
    # 구형 필터가 못 거르는 케이스를 그대로 재현.
    df.loc[df.index[idx_slice], "Close"] = df["Close"].iloc[idx_slice.start - 1] if idx_slice.start > 0 else df["Close"].iloc[0]
    return df


def test_false_positive_guard_all_valid_untouched():
    """정상 데이터는 한 행도 걸러지지 않아야 한다."""
    df = _make_valid_df(30)
    filtered, stats = app._filter_invalid_bars(df)
    assert len(filtered) == len(df)
    assert stats == {"n_dropped": 0, "gap_truncated": False}


def test_4_day_run_no_truncation_row_removal_only():
    """★ 경계 테스트. 4거래일(< _GAP_TRUNCATE_MIN_RUN=5) 연속 무효 →
    트렁케이션 없이 그 4행만 제거되어야 한다."""
    assert app._GAP_TRUNCATE_MIN_RUN == 5
    df = _make_valid_df(30)
    bad_slice = slice(10, 14)  # 정확히 4행
    df = _zero_out(df, bad_slice)

    filtered, stats = app._filter_invalid_bars(df)
    assert stats["gap_truncated"] is False, "4일 연속은 트렁케이션 대상이 아니어야 함"
    assert stats["n_dropped"] == 4
    assert len(filtered) == len(df) - 4
    # 무효 구간 이전 데이터가 살아있어야 함(트렁케이션되지 않았다는 직접 증거)
    assert filtered.index[0] == df.index[0]


def test_5_day_run_triggers_truncation():
    """★ 경계 테스트. 5거래일(== _GAP_TRUNCATE_MIN_RUN) 연속 무효 →
    그 구간 끝 이전 데이터는 전부 버려져야 한다(트렁케이션)."""
    df = _make_valid_df(30)
    bad_slice = slice(10, 15)  # 정확히 5행
    df = _zero_out(df, bad_slice)

    filtered, stats = app._filter_invalid_bars(df)
    assert stats["gap_truncated"] is True, "5일 연속은 트렁케이션 대상이어야 함"
    assert stats["n_dropped"] == 5
    # 무효 구간(인덱스 10~14) 이전 데이터는 전부 사라져야 함
    assert filtered.index[0] == df.index[15]
    assert len(filtered) == len(df) - 15


def test_6_day_run_also_truncates():
    """5일 초과(6일)도 트렁케이션 — 경계값이 >=인지 재확인(> 로 잘못
    구현했다면 이 케이스가 4일 테스트처럼 행 제거만 하고 끝나버림)."""
    df = _make_valid_df(30)
    bad_slice = slice(10, 16)  # 6행
    df = _zero_out(df, bad_slice)

    filtered, stats = app._filter_invalid_bars(df)
    assert stats["gap_truncated"] is True
    assert filtered.index[0] == df.index[16]


def test_most_recent_qualifying_gap_is_used_not_first():
    """장기 이력에 5일+ 무효 구간이 두 번 있으면, 가장 "최근" 구간
    이후만 남아야 한다(가장 오래된 구간 기준으로 자르면 여전히 중간에
    구멍이 남아 잘못된 결과)."""
    df = _make_valid_df(40)
    df = _zero_out(df, slice(5, 10))     # 첫 번째 5일 무효 구간
    df = _zero_out(df, slice(25, 31))    # 두 번째(더 긴) 6일 무효 구간

    filtered, stats = app._filter_invalid_bars(df)
    assert stats["gap_truncated"] is True
    assert filtered.index[0] == df.index[31], "가장 최근(마지막) 긴 구간 이후만 남아야 함"


def test_sporadic_single_day_gaps_never_truncate():
    """1일짜리 무효 행이 여러 번 흩어져 있어도(각각은 5일 미만이므로)
    트렁케이션은 발생하지 않고 개별 행만 제거되어야 한다."""
    df = _make_valid_df(30)
    for i in (5, 12, 20):
        df = _zero_out(df, slice(i, i + 1))

    filtered, stats = app._filter_invalid_bars(df)
    assert stats["gap_truncated"] is False
    assert stats["n_dropped"] == 3
    assert len(filtered) == len(df) - 3


def test_high_less_than_low_is_invalid_even_if_all_positive():
    """OHLC가 전부 양수여도 high<low면 무효 처리되어야 한다(0-이하
    체크만으로는 못 잡는 별도 케이스)."""
    df = _make_valid_df(20)
    df.loc[df.index[8], "High"] = df.loc[df.index[8], "Low"] - 1
    filtered, stats = app._filter_invalid_bars(df)
    assert stats["n_dropped"] == 1
    assert len(filtered) == len(df) - 1


def test_filtered_series_survives_rolling_indicator_calc():
    """필터링(행 제거 또는 트렁케이션) 후에도 흔히 쓰는 rolling 계산이
    죽지 않고 유한값을 내야 한다."""
    df = _make_valid_df(40)
    df = _zero_out(df, slice(15, 22))  # 7일 → 트렁케이션 발생
    filtered, stats = app._filter_invalid_bars(df)
    assert stats["gap_truncated"] is True
    assert len(filtered) >= 10

    ma5 = filtered["Close"].rolling(5).mean()
    atr_tr = (filtered["High"] - filtered["Low"]).rolling(5).mean()
    assert np.isfinite(ma5.dropna().to_numpy()).all()
    assert np.isfinite(atr_tr.dropna().to_numpy()).all()


def test_downcast_records_stats_only_when_something_filtered():
    """stats_sink는 실제로 뭔가 걸러졌을 때만 append되어야 한다(정상
    데이터에 대해 매 호출마다 append하면 통계가 항상 0-엔트리로 오염됨)."""
    sink = []
    clean = _make_valid_df(20)
    app._downcast(clean, stats_sink=sink)
    assert sink == []

    dirty = _zero_out(_make_valid_df(20), slice(3, 8))  # 5일 → 트렁케이션
    app._downcast(dirty, stats_sink=sink)
    assert len(sink) == 1
    assert sink[0]["gap_truncated"] is True


# ---------------------------------------------------------------------------
# 실제 프로덕션 데이터 검증 — 007610.KS (갈래 B의 실제 사례, 장기 거래정지
# 후 재개). 합성 데이터만으로 끝내지 말라는 사용자 지시.
# ---------------------------------------------------------------------------

_CACHE_PATH = os.path.join(
    os.path.dirname(__file__), "scripts", "measurements", ".stage1_data_cache.pkl"
)


@pytest.mark.skipif(not os.path.exists(_CACHE_PATH), reason="jongga stage1 캐시 없음(로컬 전용 산출물)")
def test_007610_real_data_truncates_and_gates_via_min_bars():
    with open(_CACHE_PATH, "rb") as f:
        cache = pickle.load(f)
    df = cache["data"].get("007610.KS")
    assert df is not None, "007610.KS가 캐시에 없음 — 캐시 파일이 바뀌었을 수 있음"

    orig_len = len(df)
    filtered, stats = app._filter_invalid_bars(df)

    assert stats["gap_truncated"] is True, "007610.KS는 4년치 무거래 구간이 있어 트렁케이션 대상이어야 함"
    assert stats["n_dropped"] > 500, f"실제 오염 구간(약 4년치)에 비해 배제 건수가 너무 적음: {stats['n_dropped']}"
    # 재개 후 실제 거래일 수만큼만 남아야 함 — 원본 길이보다 훨씬 짧되 0은 아님.
    assert 0 < len(filtered) < orig_len
    assert len(filtered) < 210, "재개 후 이력이 200일선 계산에 필요한 min_bars(210)보다 짧아야 min_bars 게이트가 정상 작동"

    # min_bars=210 게이트를 쓰는 대표 함수(analyze, 눌림목)가 데이터부족으로
    # None을 반환하는지 확인 — 별도 분기 없이 기존 게이트로 자동 처리되는지 검증.
    result = scanner.analyze(filtered, rs_rank=90, rs_mom=90, is_kr=True)
    assert result is None, "min_bars 게이트가 트렁케이션된 짧은 이력을 걸러내지 못함"
