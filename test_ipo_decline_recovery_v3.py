"""상장 후 하락 회복 탐색 3차(docs/ipo_decline_recovery_exploration.md §4) — 분할·유지 확인·금액 가중 고정.

사보타주 확인(2026-10-09, 각각 FAIL 확인 후 원복): C를 첫 매수가 기준으로 · E 유지 19봉 · C 트리거에 다음 봉 종가 · E 유지 창에 미래 2봉 ·
수익률을 체결가 단순평균 기준으로.
"""
from __future__ import annotations

import importlib.util
import os

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("ipo3", os.path.join(ROOT, "scripts", "measurements", "2026-10-09_ipo_decline_recovery_exploration_v3.py"))
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)
ALL = lambda i: True  # noqa: E731 — 저유동성 우회(합성 데이터)


def _df(closes, opens=None):
    c = np.asarray(closes, float)
    o = np.asarray(opens if opens is not None else c, float)
    idx = pd.bdate_range("2019-01-02", periods=len(c))
    return pd.DataFrame({"Open": o, "High": np.maximum(o, c) * 1.01, "Low": np.minimum(o, c) * 0.99, "Close": c,
                         "Volume": np.full(len(c), 1e6)}, index=idx)


def _fixture():
    """하락 100 → 30(600봉, 시작점·가격 분할 트리거) → 진동 상승 30 → 90(500봉, 교차·유지 확인)."""
    j = np.arange(500)
    return list(np.linspace(100, 30, 600)) + list(30 + 0.12 * j + 2 * np.sin(j / 9))


def test_decisions_do_not_look_ahead():
    """미래 참조: 모든 봉 i에서 i 이후 OHLC를 극단값(0.01·10^6)으로 바꿔도, 결정일 ≤ i인 결정은 B·C·E·F 모두 같아야 한다."""
    c = _fixture()
    s = _df(c)
    s0 = m.v2.start_index(np.array(c))
    base = m.decisions(s, s0, ALL)
    for k in ("B", "C", "E", "F"):
        assert base[k], k
    assert len(base["C"]) >= 3
    for i in range(s0, len(c) - 1, 3):
        for fill in (0.01, 1e6):
            alt = m.decisions(_df(c[:i + 1] + [fill] * (len(c) - i - 1)), s0, ALL)
            for k in ("A", "B", "C", "D", "E", "F"):
                assert [d for d in alt[k] if d <= i] == [d for d in base[k] if d <= i], (k, i, fill)


def test_price_split_uses_previous_buy_price():
    """C의 −15%는 '직전 매수가' 기준: 2회차(0.84·P1) 뒤 0.80·P1에 머물면 3회차 없음(첫 매수가 기준이었다면 바로 3회차),
    0.70·P1(≤ 0.85 × 0.84·P1)까지 가야 3회차."""
    pre = [100.0] * 25 + list(np.linspace(100, 60, 40))       # 시작점: 60 ≤ 100(21번째 봉 이후 최고) × 0.6
    s0 = m.v2.start_index(np.array(pre + [60.0]))
    assert s0 == len(pre) - 1
    P1 = 60.0
    tail = [P1] * 5 + [0.86 * P1] * 5 + [0.84 * P1] * 2 + [0.80 * P1] * 30 + [0.70 * P1] * 3 + [0.70 * P1] * 20
    c = pre + tail
    d = m.decisions(_df(c), s0, ALL)["C"]
    i2 = len(pre) + 10                                          # 0.84·P1 첫날 → 2회차 결정
    i3 = len(pre) + 5 + 5 + 2 + 30                              # 0.70·P1 첫날 → 3회차 결정
    assert d[:3] == [s0, i2, i3], d


def test_e_19_bars_then_break_is_not_entry():
    """E: 교차 뒤 19봉만 유지하고 20봉째 깨지면 확인 아님. 20봉 유지면 결정일 = 교차 + 20(21봉째)."""
    n = 80
    cross = np.zeros(n, bool)
    cross[10] = True
    cond = np.zeros(n, bool)
    cond[10:30] = True                                           # 교차일 10 + 뒤 19봉(11~29), 30에서 깨짐
    assert m.hold_confirm_days(cross, cond, 0) == []
    cond[30] = True                                              # 11~30 = 20봉
    assert m.hold_confirm_days(cross, cond, 0) == [30]


def test_valuation_amount_weighted_hand():
    """금액 가중 수기 1건: 1/4씩 100·50에 체결, 평가일 종가 75 → (0.25/100 + 0.25/50)·75 ÷ 0.5 − 1 = +12.5%(단순 가격 평균 75 기준이면 0%).
    둘째 체결 전 평가일엔 첫 회차만 센다."""
    c = [100.0] * 3 + [50.0] * 3 + [75.0] * 600
    o = [100.0] * 3 + [50.0] * 3 + [75.0] * 600
    s = _df(c, opens=o)
    v = m.valuation(s, [0, 3], 0.25)                             # 체결 = 1봉째 시가 100, 4봉째 시가 50
    T = v["first_fill"] + 250 - 1
    hand = ((0.25 / 100 + 0.25 / 50) * c[T] / 0.5 - 1) * 100
    assert abs(v["r250"] - hand) < 1e-9 and abs(hand - 12.5) < 1e-9
    assert abs(v["avg_cost"] - 0.5 / (0.25 / 100 + 0.25 / 50)) < 1e-9
    early = m.valuation(s, [0, 400], 0.25)                       # 둘째 회차가 250봉 뒤 → 250봉 평가엔 첫 회차만
    assert abs(early["r250"] - (75 / 100 - 1) * 100) < 1e-9


def test_params():
    assert m.TIME_STEPS == (0, 63, 126, 189) and m.PRICE_DROP == 0.85 and m.PRICE_MAX_BUYS == 4 and m.PRICE_WINDOW == 1000
    assert m.HOLD_CONFIRM == 20 and m.HORIZONS == (250, 500) and m.v2.EXPECT_V1 == {"explore": 367, "explore_used": 312}
