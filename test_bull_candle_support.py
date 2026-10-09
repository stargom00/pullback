"""장대양봉 지지 확인 후 진입 측정(docs/bull_candle_support_entry.md) — 확인봉 판정 고정.

사용자 지시(사보타주): 아래 중 하나라도 확인봉으로 잡히면 테스트 결함으로 보고 멈춘다.
  ① 지지선을 1원 깨고 회복 → 미진입 ② 21거래일째 확인봉 → 미진입 ③ 음봉이면서 전일 고가 돌파 → 확인봉 아님
  ④ 장대양봉 다음 날 바로 전일 고가 돌파 양봉(눌림 0일) → 확인봉 아님.
  반대로 눌림 1일 뒤 양봉이 전일 고가를 돌파하면 확인봉으로 잡혀야 한다.

사보타주 확인(2026-10-09, 각각 FAIL 확인 후 원복): ① 지지선 판정 `c < support` → `c < support - 2` → test_1 FAIL
② 창 `day > 20` → `day > 21` → test_2 FAIL ③ 양봉 조건 제거 → test_3 FAIL ④ 눌림 조건 제거 → test_4 FAIL
"""
from __future__ import annotations

import importlib.util
import os
from collections import Counter

import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("bcs", os.path.join(ROOT, "scripts", "measurements", "2026-10-09_bull_candle_support_entry.py"))
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

# 장대양봉: 전일 종가 100 → 시가 100 · 종가 120(+20%) — 지지선 110, 손절 = 시가 100
PRE = [(100, 101, 99, 100)] * 3
BULL = (100, 120, 100, 120)
FLAT = (115.5, 116, 115, 115)       # 눌림(종가 115 < 120), 지지선 위, 음봉·전일 고가 116 미돌파


def _df(rows):
    """rows: [(시가, 고가, 저가, 종가)] — 평일 연속 날짜, 거래량 1."""
    idx = pd.bdate_range("2026-01-05", periods=len(rows))
    o, h, lo, c = zip(*[tuple(float(x) for x in r) for r in rows])
    return pd.DataFrame({"Open": o, "High": h, "Low": lo, "Close": c, "Volume": [1.0] * len(rows)}, index=idx)


K_BULL = len(PRE)


def _find(rows):
    df = _df(PRE + [BULL] + rows)
    reason, k, day = m.find_confirm(m.harness.CleanView(df), K_BULL)
    return reason, (k - K_BULL if k is not None else None), day


def test_pullback_then_breakout_is_confirm():
    """눌림 1일(종가 115) 뒤 양봉이 전일 고가(116)를 돌파 → 확인봉(장대양봉 다음 날부터 2번째 거래일)."""
    assert _find([FLAT, (115, 118, 114.5, 117)] + [FLAT] * 25) == ("confirmed", 2, 2)


def test_1_support_broken_by_one_won_then_recovered():
    """① 종가 109(지지선 110을 1원 아래) → 이후 회복·돌파해도 미진입. 종가 = 지지선(110)은 깨진 게 아니다."""
    assert _find([(115, 116, 108, 109), (110, 125, 110, 124)] + [FLAT] * 25)[0] == "support_broken"
    assert _find([(115, 116, 108, 110), (110, 125, 110, 124)] + [FLAT] * 25) == ("confirmed", 2, 2)


def test_2_confirm_on_day_21_is_no_entry():
    """② 20거래일 안(눌림만 계속) → 21번째 거래일 돌파 양봉은 미진입. 20번째면 확인봉."""
    brk = (115, 118, 114.5, 117)
    assert _find([FLAT] * 20 + [brk] + [FLAT] * 5)[0] == "no_confirm_in_window"
    assert _find([FLAT] * 19 + [brk] + [FLAT] * 5) == ("confirmed", 20, 20)


def test_3_bearish_bar_above_prev_high_is_not_confirm():
    """③ 음봉(시가 125 → 종가 118)이 전일 고가 116을 넘어도 확인봉 아님."""
    reason, k, _ = _find([FLAT, (125, 126, 117, 118)] + [FLAT] * 25)
    assert reason != "confirmed" and k is None


def test_4_breakout_without_pullback_is_not_confirm():
    """④ 장대양봉 다음 날 바로 전일 고가(120) 돌파 양봉(눌림 0일)은 확인봉 아님 — 그 뒤 눌림·돌파는 확인봉."""
    assert _find([(121, 126, 120.5, 125)] + [(124, 125, 123, 124)] * 25)[0] == "no_confirm_in_window"
    assert _find([(121, 126, 120.5, 125), FLAT, (115, 118, 114.5, 117)] + [FLAT] * 25) == ("confirmed", 3, 3)


def test_invalid_bar_skipped_and_not_counted():
    """CleanView: 확인 창 안의 OHLC=0 봉(무거래일 이월)은 건너뛰고 거래일로 세지 않는다. 전일 고가 = 직전 유효봉 고가."""
    zero = (0, 0, 0, 115)
    assert _find([FLAT, zero, (115, 118, 114.5, 117)] + [FLAT] * 25) == ("confirmed", 3, 2)


def test_window_incomplete_when_data_ends():
    assert _find([FLAT] * 5)[0] == "window_incomplete"


def test_entry_next_open_stop_bull_open(monkeypatch):
    """진입 = 확인봉 다음 유효봉 시가, 손절 = 장대양봉 시가. 진입가 ≤ 손절이면 제외."""
    df = _df(PRE + [BULL, FLAT, (115, 118, 114.5, 117), (118, 119, 117, 118)] + [FLAT] * 70)
    v = m.harness.CleanView(df)
    monkeypatch.setattr(m.prev, "liquid", lambda p: True)          # 합성 거래량 1이라 저유동성 컷만 우회(판정 로직 아님)
    st = Counter()
    r = m.race_open(v, K_BULL + 2, 100.0, st, "a")
    assert r["date"] == str(df.index[K_BULL + 3].date()) and r["entry"] == 118.0 and r["stop"] == 100.0
    df2 = _df(PRE + [BULL, FLAT, (115, 118, 114.5, 117), (99, 119, 98, 118)] + [FLAT] * 70)
    assert m.race_open(m.harness.CleanView(df2), K_BULL + 2, 100.0, st, "a") is None and st["a_entry_not_above_stop"] == 1


def test_params_are_registered_values():
    assert m.Z_MIN == 2.39 and m.EV_MIN == 0.15 and m.N_MIN == 100 and m.CONFIRM_WINDOW == 20
    assert m.bc.BULL_CLOSE_RATIO == 1.15 and m.RUN_MARKETS == ["KR"] and m.prev.MAX_BARS == 60
