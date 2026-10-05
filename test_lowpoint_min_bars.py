"""v5.329 — 저점 스크린 최소 봉 수 = 이 스크린이 계산하는 지표(RSI14)가 실제로 요구하는 최소치.

사용자 지시: "저점 스크린의 최소 봉 수를 그 탭이 계산하는 지표가 실제로 요구하는 최소치로 바꾼다 … 매직넘버 금지, 산출 근거
주석" · 기준 선택 "RSI14만 → 17봉", "월봉도 같은 기준으로". 배경: 삼진식품(0013V0.KQ, 2025-12 상장)이 키움 조건검색엔
잡히는데 저점 스크린에선 "주봉 41개 < 52"로 탈락. 5탭 추세 게이트(min_bars 210 등)는 그대로.

사보타주 확인(2026-10-06, FAIL 확인 후 원복):
① MIN_BARS_RSI = RSI_PERIOD + 2(한 봉 모자람) → test_min_bars_is_smallest_computable·test_boundary FAIL
② 주봉을 예전 52로 되돌림 → test_week_and_month_use_rsi_minimum·test_short_history_stock_now_evaluated FAIL
③ 평가 페이지 가드를 lp.MIN_BARS["month"] 재사용으로 되돌림 → test_eval_warmup_stays_36 FAIL
④ evaluate의 최소치 게이트 제거(len < 3만 검사) → test_boundary[week·month] FAIL(사용자 추가 조건 "새 최소치 게이트 제거 시
   경계 테스트 FAIL 확인")
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.join(ROOT, "scripts", "screens"), os.path.join(ROOT, "scripts", "measurements")):
    sys.path.insert(0, p)
import lowpoint as lp  # noqa: E402
import lowpoint_eval as ev  # noqa: E402

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=KST)


def test_min_bars_is_smallest_computable():
    """상수를 믿지 않고 직접 찾는다 — RSI[2]가 처음 계산되는 봉 수 = MIN_BARS_RSI."""
    rng = np.random.default_rng(5)
    s = pd.Series(1000 * np.cumprod(1 + rng.normal(0, 0.05, 60)))
    first = next(n for n in range(3, 60) if np.isfinite(lp.rsi_wilder_sma(s.iloc[:n], lp.RSI_PERIOD).iloc[-3]))
    assert first == lp.MIN_BARS_RSI == lp.RSI_PERIOD + 3 == 17


def test_week_and_month_use_rsi_minimum():
    assert lp.MIN_BARS == {"week": lp.MIN_BARS_RSI, "month": lp.MIN_BARS_RSI}
    src = open(lp.__file__, encoding="utf-8").read()
    assert 'MIN_BARS = {"week": MIN_BARS_RSI, "month": MIN_BARS_RSI}' in src and "MIN_BARS_RSI = RSI_PERIOD + 3" in src


def _weekly(n, seed=11):
    rng = np.random.default_rng(seed)
    w = 1000 * np.cumprod(1 + rng.normal(0, 0.05, n))
    return pd.Series(w, index=pd.date_range(end="2026-10-02", periods=n, freq="W-FRI"))


@pytest.mark.parametrize("tf", ["week", "month"])
def test_boundary(tf):
    """(최소치 − 1)봉 → 탈락(short), 최소치 → 판정(RSI 3개 모두 유한값)."""
    freq = "W-FRI" if tf == "week" else "ME"
    end = "2026-10-02" if tf == "week" else "2026-09-30"
    for n, short in ((lp.MIN_BARS_RSI - 1, True), (lp.MIN_BARS_RSI, False)):
        rng = np.random.default_rng(3)
        s = pd.Series(1000 * np.cumprod(1 + rng.normal(0, 0.05, n)), index=pd.date_range(end=end, periods=n, freq=freq))
        e = lp.evaluate(s, tf, "kr", NOW)
        if short:
            assert e["status"] == "short" and e["n_bars"] == n
        else:
            assert e["status"] in ("hit", "no") and all(np.isfinite([e["r2"], e["r1"], e["r0"]]))


def test_short_history_stock_now_evaluated():
    """삼진식품 모양: 주봉 41개(예전 52 미만으로 탈락) — 이제 판정된다."""
    e = lp.evaluate(_weekly(41), "week", "kr", NOW)
    assert e["status"] != "short"


def test_eval_warmup_stays_36():
    assert ev.SIGNAL_WARMUP_MONTHS == 36
    line = re.search(r"^SIGNAL_WARMUP_MONTHS = (.+)$", open(ev.__file__, encoding="utf-8").read(), re.M).group(1)
    assert line.strip() == "36", line


def test_trend_tab_gates_unchanged():
    """눌림목·추세전환·돌파·돌파임박 등 5탭의 min_bars(200일선·RS용)는 그대로."""
    import scanner
    src = open(scanner.__file__, encoding="utf-8").read()
    assert src.count('"min_bars": 210') >= 6 and '"min_bars": 260' in src
    assert "MIN_BARS_RSI" not in src
