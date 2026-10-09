"""99일선 돌파 장대양봉 측정(docs/ma99_bull_candle_breakout.md) — 600일선 모듈을 선 길이 99로 쓰는지·A/C 분류 고정.

사용자 지시(사보타주): 아래 중 하나라도 A로 잡히면 테스트 결함으로 보고 멈춘다.
  ① 고가로만 99일선을 넘은 장대양봉 ② 전일 이미 99일선 위였던 장대양봉 ③ +14.99% 양봉의 돌파 ④ 유효봉 99개인 종목.

사보타주 확인(2026-10-09, 공용 로직 600일선 모듈에 각각 주입 → FAIL 확인 후 원복): ① A 판정 고가 기준 → test_1 FAIL
② 전일 선 아래 조건 제거 → test_2 FAIL ③ 문턱 1.149 → test_3 FAIL ④ 이력 가드 제거 + 부분 창 평균 → test_4·수기값 FAIL.
"""
from __future__ import annotations

import importlib.util
import os

import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("ma99bc", os.path.join(ROOT, "scripts", "measurements", "2026-10-09_ma99_bull_candle_breakout.py"))
s99 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(s99)
m = s99.m                                                        # 선 길이를 99로 바꾼 600일선 모듈 인스턴스


def _df(n_flat: int, tail: list):
    rows = [(100.0, 100.5, 99.5, 100.0)] * n_flat + [tuple(float(x) for x in r) for r in tail]
    idx = pd.bdate_range("2020-01-01", periods=len(rows))
    o, h, lo, c = zip(*rows)
    return pd.DataFrame({"Open": o, "High": h, "Low": lo, "Close": c, "Volume": [1.0] * len(rows)}, index=idx)


def _cls(df):
    v = m.harness.CleanView(df)
    return m.classify(v.prefix(len(df) - 1))


PREV_BELOW = (96, 96.5, 94.5, 95)                                # 전일 종가 95 < MA99(≈99.95)


def test_breakout_close_above_is_A():
    """전일 95 < 선, 당일 +15.8% 양봉 종가 110 > 선(≈100.15) → A."""
    assert _cls(_df(120, [PREV_BELOW, (96, 111, 95, 110)])) == "A"


def test_1_high_only_cross_is_not_A():
    """① 80 → 92(+15% 양봉), 고가 105로 선(≈99.7)을 찔렀지만 종가는 아래 → C."""
    assert _cls(_df(120, [(81, 81, 79, 80), (81, 105, 80.5, 92)])) == "C"


def test_2_prev_already_above_is_not_A():
    """② 전일 종가 101 > 전일 선(≈100.01) → 둘 다 아님."""
    assert _cls(_df(120, [(100, 101.5, 99.5, 101), (101.5, 117, 101, 116.2)])) is None


def test_3_plus_14_99_is_not_A():
    """③ 95 → 95×1.1499(+14.99%) 양봉이 선을 넘어도 장대양봉이 아니다."""
    assert _cls(_df(120, [PREV_BELOW, (96, 110, 95, 95 * 1.1499)])) is None


def test_4_99_valid_bars_is_not_A():
    """④ 유효봉 99개 → 판정 안 함. 100개(전일·당일 MA99)부터 A."""
    assert _cls(_df(97, [PREV_BELOW, (96, 111, 95, 110)])) is None       # 99
    assert _cls(_df(98, [PREV_BELOW, (96, 111, 95, 110)])) == "A"        # 100


def test_ma99_hand_value_and_line_switch():
    """MA99 수기 계산(합성) + 같은 df를 600 기준으로 보면 이력 부족(None) — 선 길이만 바뀌었는지."""
    df = _df(120, [PREV_BELOW, (96, 111, 95, 110)])
    p = m.harness.CleanView(df).prefix(len(df) - 1)
    assert abs(m.ma_at(p, 0) - (97 * 100 + 95 + 110) / 99) < 1e-9 and abs(m.ma_at(p, 1) - (98 * 100 + 95) / 99) < 1e-9
    assert m.classify(p, 600) is None


def test_only_line_length_changed():
    assert m.MA_N == 99 and m.MIN_BARS == 100 and m.OTHER_N == 600
    assert m.Z_MIN == 2.58 and m.EV_MIN == 0.15 and m.N_MIN == 100 and m.MAX_BARS == 60 and m.DATA_DAYS == 3000
    assert m.bc.BULL_CLOSE_RATIO == 1.15 and m.RUN_MARKETS == ["KR"] and m.D_FWD == 63
    assert m.OUT.endswith("2026-10-09_ma99_bull_candle_breakout.results.json")


def test_ma600_module_untouched_by_ma99_import():
    """99 스크립트가 바꾼 값이 600일선 스크립트의 별도 로드에는 새지 않는다."""
    spec = importlib.util.spec_from_file_location("ma600_fresh", os.path.join(ROOT, "scripts", "measurements", "2026-10-09_ma600_bull_candle_breakout.py"))
    m600 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m600)
    assert m600.MA_N == 600 and m600.MIN_BARS == 601 and m600.OTHER_N == 99 and m600.OUT.endswith("ma600_bull_candle_breakout.results.json")
