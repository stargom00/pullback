"""600일선 돌파 장대양봉 측정(docs/ma600_bull_candle_breakout.md) — A/C 분류·이력·겹침 고정.

사용자 지시(사보타주): 아래 중 하나라도 A로 잡히면 테스트 결함으로 보고 멈춘다.
  ① 600일선을 종가가 아니라 고가로만 넘은 장대양봉 ② 전일 이미 600일선 위였던 장대양봉 ③ +14.9% 양봉의 600일선 돌파
  ④ 유효봉이 599개뿐인 종목. 반대로 전일 종가 < 600선, 당일 +15% 양봉 종가 > 600선이면 A.

사보타주 확인(2026-10-09, 각각 FAIL 확인 후 원복): ① A 판정을 고가 기준으로 → test_1 FAIL ② 전일 선 아래 조건 제거 → test_2 FAIL
③ 장대양봉 문턱 1.149 → test_3 FAIL ④ 이력 가드 제거 + 부분 창 평균 → test_4·무효봉 테스트 FAIL ⑤ 보유 중 건너뛰기 제거 → 겹침 테스트 FAIL.
처음엔 ③·④ 사보타주가 통과했다(테스트 결함): ③은 테스트 값이 정확히 95×1.149라 부동소수 경계에서 빗나갔고(문턱 바로 아래 값 추가),
④는 가드만 빼면 음수 슬라이스가 우연히 None을 내서였다(현실적 버그 모양 = 부분 창 평균으로 사보타주 교체, ma_at에 이력 부족 하드 실패 추가).
"""
from __future__ import annotations

import importlib.util
import os
from collections import Counter

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("ma600", os.path.join(ROOT, "scripts", "measurements", "2026-10-09_ma600_bull_candle_breakout.py"))
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def _df(n_flat: int, tail: list):
    """종가 100 평탄 n_flat봉 + tail [(시가, 고가, 저가, 종가)]. 거래량 1."""
    rows = [(100.0, 100.5, 99.5, 100.0)] * n_flat + [tuple(float(x) for x in r) for r in tail]
    idx = pd.bdate_range("2020-01-01", periods=len(rows))
    o, h, lo, c = zip(*rows)
    return pd.DataFrame({"Open": o, "High": h, "Low": lo, "Close": c, "Volume": [1.0] * len(rows)}, index=idx)


def _cls(df):
    v = m.harness.CleanView(df)
    return m.classify(v.prefix(len(df) - 1))


PREV_BELOW = (96, 96.5, 94.5, 95)                       # 전일 종가 95 < MA600(≈99.99)


def test_breakout_close_above_is_A():
    """전일 종가 95 < 선, 당일 +15.8% 양봉(96 → 110) 종가 > 선(≈100.03) → A. 정확히 +15%(95 → 109.25)도 A."""
    assert _cls(_df(613, [PREV_BELOW, (96, 111, 95, 110)])) == "A"
    assert _cls(_df(613, [PREV_BELOW, (96, 110, 95, 109.25)])) == "A"


def test_1_high_only_cross_is_not_A():
    """① 전일 80 → 당일 92(+15% 양봉), 고가 105로 선(≈99.9) 위를 찔렀지만 종가는 선 아래 → C(하락 중 장대양봉), A 아님."""
    assert _cls(_df(613, [(81, 81, 79, 80), (81, 105, 80.5, 92)])) == "C"


def test_2_prev_already_above_is_not_A():
    """② 전일 종가 101 > 전일 선(≈100.0017) → 장대양봉이 선 위에서 나와도 A도 C도 아님."""
    assert _cls(_df(613, [(100, 101.5, 99.5, 101), (101.5, 117, 101, 116.2)])) is None


def test_3_plus_14_9_is_not_A():
    """③ 95 → 109.155(+14.9%) 양봉이 선을 넘어도 장대양봉이 아니다."""
    assert _cls(_df(613, [PREV_BELOW, (96, 110, 95, 109.155)])) is None
    assert _cls(_df(613, [PREV_BELOW, (96, 110, 95, 95 * 1.1499)])) is None   # 문턱 바로 아래(+14.99%)


def test_4_599_valid_bars_is_not_A():
    """④ 유효봉 599개(전일·당일 MA600 불가) → A 아님. 600개도 전일 MA600이 없어 아님, 601개부터 판정."""
    assert _cls(_df(597, [PREV_BELOW, (96, 111, 95, 110)])) is None      # 599
    assert _cls(_df(598, [PREV_BELOW, (96, 111, 95, 110)])) is None      # 600
    assert _cls(_df(599, [PREV_BELOW, (96, 111, 95, 110)])) == "A"       # 601


def test_invalid_bars_do_not_count_toward_600():
    """OHLC=0 봉(무거래일 이월)은 정제에서 빠져 600개에 들어가지 않는다 — 601봉 중 1봉이 무효면 판정 안 함."""
    df = _df(599, [PREV_BELOW, (96, 111, 95, 110)])
    df.iloc[10, df.columns.get_indexer(["Open", "High", "Low"])] = 0.0
    assert _cls(df) is None


def test_ma_hand_value():
    """MA600 수기 계산 대조(합성): 평탄 598봉(100) + 95 + 110 → (598×100 + 95 + 110) / 600."""
    df = _df(613, [PREV_BELOW, (96, 111, 95, 110)])
    p = m.harness.CleanView(df).prefix(len(df) - 1)
    assert abs(m.ma_at(p, 0) - (598 * 100 + 95 + 110) / 600) < 1e-9
    assert abs(m.ma_at(p, 1) - (599 * 100 + 95) / 600) < 1e-9


def test_scan_entry_stop_and_skip_in_position(monkeypatch):
    """진입 = 장대양봉 다음 날 시가, 손절 = 장대양봉 시가. 보유 중(진입일~청산일)에 나온 다음 장대양봉은 건너뛴다."""
    monkeypatch.setattr(m.prev, "liquid", lambda p: True)          # 합성 거래량 1이라 저유동성 컷만 우회(판정 로직 아님)
    hold = [(104, 105, 103, 104)] * 5                                # 진입 104, 손절 96 → 목표 120 · 손절 96 둘 다 안 닿음
    second = [(98, 98.5, 97, 97), (98, 113, 97.5, 112)]              # 보유 중 다시 선 아래(97) → +15.5% 장대양봉 → 건너뛰어야(손절 96·목표 120 안 닿음)
    df = _df(613, [PREV_BELOW, (96, 111, 95, 110)] + hold + second + [(104, 105, 103, 104)] * 70)
    st = Counter()
    rows = m.scan_ticker("T.KS", m.harness.CleanView(df), st)
    assert len(rows) == 1 and rows[0]["group"] == "A"
    assert rows[0]["entry"] == 104.0 and rows[0]["stop"] == 96.0 and rows[0]["date"] == str(df.index[615].date())
    assert rows[0]["outcome"] == "unresolved" and st["A_signal"] == 2 and st["A_skipped_in_position"] == 1


def test_exit_index_matches_race():
    fut = pd.DataFrame({"Open": [104.0, 104, 104], "High": [105.0, 121, 105], "Low": [103.0, 103, 103], "Close": [104.0, 104, 104]},
                       index=pd.bdate_range("2024-01-01", periods=3))
    out, r = m.harness.race(104.0, 96.0, fut, 60)
    assert out == "target" and m.exit_index(104.0, 96.0, fut, out) == 1


def test_params_are_registered_values():
    assert m.MA_N == 600 and m.MIN_BARS == 601 and m.Z_MIN == 2.50 and m.EV_MIN == 0.15 and m.N_MIN == 100
    assert m.MAX_BARS == 60 and m.bc.BULL_CLOSE_RATIO == 1.15 and m.RUN_MARKETS == ["KR"] and m.MA_CHECK_N == 3
