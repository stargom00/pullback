"""상장 후 하락 회복 탐색 2차(docs/ipo_decline_recovery_exploration.md §3) — 시작점·(a) 미래 참조·발생마다 세기 고정.

사보타주 확인(2026-10-09, 각각 FAIL 확인 후 원복): (a) 창에 미래 2봉 · 시작점에 전체(미래 포함) 최고가 · 250봉 보유 무시 제거 · (a) 시작일만이
아니라 계속 참 · 시작점 기준에 첫 20봉 포함. 두 번째는 처음 통과했다(픽스처 최고가가 하락 전에 있었음 — 테스트 결함, 뒤에 더 높은 고점을 붙여 수정).
"""
from __future__ import annotations

import importlib.util
import os
from collections import Counter

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("ipo2", os.path.join(ROOT, "scripts", "measurements", "2026-10-09_ipo_decline_recovery_exploration_v2.py"))
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def _df(closes):
    c = np.asarray(closes, float)
    idx = pd.bdate_range("2019-01-02", periods=len(c))
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": np.full(len(c), 1e6)}, index=idx)


def _fixture():
    """하락 300봉(100 → 40) → 바닥 120봉(40 ± 1) → 진동 상승 400봉 — (a)~(e) 모두 한 번 이상 켜짐."""
    j = np.arange(400)
    return list(np.linspace(100, 40, 300)) + list(40 + np.sin(np.arange(120) / 5)) + list(40 + 0.25 * j + 4 * np.sin(j / 6))


def test_signals_v2_do_not_look_ahead_including_a():
    """수정된 미래 참조 검사를 2차 (a)에도: 모든 봉 i에서 i 이후를 극단값(0.01·10^6)으로 바꿔도 i번째 신호가 같아야 한다."""
    c = _fixture()
    base = m.signal_series_v2(_df(c))
    for k in m.SIGNALS:
        assert base[k].any(), k
    for i in range(m.v1.SKIP_FIRST, len(c) - 1):
        for fill in (0.01, 1e6):
            alt = m.signal_series_v2(_df(c[:i + 1] + [fill] * (len(c) - i - 1)))
            for k in m.SIGNALS:
                assert alt[k][i] == base[k][i], (k, i, fill)


def test_a_v2_is_onset_and_requires_deep_drop():
    """(a)는 조건이 처음 참이 된 날만(이어지는 날은 아님), 60봉 최저가 그때까지 최고의 50% 이하여야 한다."""
    c = _fixture()
    a = m.signal_series_v2(_df(c))["a_pre_base"]
    on = np.flatnonzero(a)
    assert len(on) >= 1 and not a[on[0] + 1]
    shallow = list(np.linspace(100, 60, 300)) + [60 + np.sin(k / 5) for k in range(200)]       # 최고 대비 −40%까지만
    assert not m.signal_series_v2(_df(shallow))["a_pre_base"].any()


def test_start_uses_only_running_max_bar_by_bar():
    """시작점은 그때까지(21번째 봉~그날) 최고 종가만 쓴다 — 모든 봉 i에서 i까지 잘라 다시 구해도 (시작 ≤ i면 같은 값, 아니면 None).
    끝에 300까지 오르는 구간을 붙여 '미래 최고가'를 쓰면 시작점이 앞당겨지게 했다(처음 픽스처는 최고가가 하락 전에 있어 그 사보타주가 통과했다)."""
    c = [200.0] * 20 + list(np.linspace(100, 130, 50)) + list(np.linspace(130, 70, 80)) + [70.0] * 50 + list(np.linspace(70, 300, 100))
    s0 = m.start_index(np.array(c))
    assert s0 is not None and c[s0] <= max(c[20:s0 + 1]) * 0.6 and c[s0 - 1] > max(c[20:s0]) * 0.6   # 첫 20봉(200)은 기준이 아니다
    for i in range(len(c)):
        got = m.start_index(np.array(c[:i + 1]))
        assert got == (s0 if s0 <= i else None), i


def test_c_counted_each_time_with_250_hold(monkeypatch):
    """(c) 20>50 교차가 하락 구간 안에서 여러 번 → 나올 때마다 센다, 단 같은 신호 250봉 보유 중이면 무시."""
    monkeypatch.setattr(m.prev, "liquid", lambda p: True)
    j = np.arange(1200)
    c = list(np.linspace(100, 50, 100)) + list(50 + 3 * np.sin(j / 15))                       # 하락 뒤 진동 — 교차 여러 번
    s = _df(c)
    sig = m.signal_series_v2(s)
    s0 = m.start_index(np.array(c, float))
    crosses = [int(i) for i in np.flatnonzero(sig["c_ma20_50_cross"]) if i >= s0]
    st = Counter()
    ev = m.events(s, sig, s0, st)["c_ma20_50_cross"]
    assert len(crosses) >= 6 and len(ev) >= 3                                                 # 여러 번 진입
    for a_, b_ in zip(ev, ev[1:]):
        assert b_["i"] + 1 > a_["i"] + 1 + m.HOLD - 1                                          # 다음 진입은 보유 끝난 뒤
    assert st["c_ma20_50_cross_ignored_holding"] == len([i for i in crosses if i >= ev[0]["i"]]) - len(ev) - \
        len([i for i in crosses if i >= ev[0]["i"] and i + 1 >= len(c)])


def test_params():
    assert m.START_DROP == 0.6 and m.HOLD == 250 and m.v1.PRE_A_WIN == 60 and m.v1.PRE_A_BAND == 1.15 and m.v1.PRE_A_DROP == 0.5
    assert m.EXPECT_V1 == {"explore": 367, "explore_used": 312}
