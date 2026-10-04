"""v5.323 — 저점 스크린·평가의 RSI = 고전 Wilder(lowpoint.rsi_wilder_sma, 첫 14개 변화량 SMA 시드).

사용자 지시: "저점·평가의 RSI를 고전 Wilder(첫 14봉 SMA 시드)로 교체 — 키움·트레이딩뷰 표준과 일치하고 시딩 왜곡
(NKE RSI 0.0류)도 줄어든다. 저점은 측정된 전략이 아니므로 기준 변경 가능."
5탭 스캐너(scanner.rsi — 첫 변화량부터 ewm)는 측정 기반이라 그대로다(이 파일이 고정).

기준 예제: StockCharts "Relative Strength Index (RSI)" ChartSchool 계산표(종가 33개, 첫 RSI = 15번째 종가).
그 표는 평균을 소수 둘째 자리로 반올림해 이어 계산해 우리 값(반올림 없음)과 최대 0.07 차이 난다 — 허용 0.1.

사보타주 확인(2026-10-04, FAIL 확인 후 원복):
① lowpoint.evaluate가 scanner.rsi로 되돌아감 → test_screen_uses_classic_wilder FAIL
② 시드를 첫 변화량 하나로(ag, al = g[0], l[0]) → test_matches_stockcharts_reference·test_seed_is_sma_of_first_14 FAIL
③ 평가 페이지 stoch_rsi_k가 scanner.rsi로 → test_eval_page_uses_classic_wilder FAIL
"""
from __future__ import annotations

import inspect
import os
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

import scanner  # noqa: E402

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 3, 9, 0, tzinfo=KST)

SC_CLOSE = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28,
            46.00, 46.03, 46.41, 46.22, 45.64, 46.21, 46.25, 45.71, 46.45, 45.78, 45.35, 44.03, 44.18, 44.22, 44.57,
            43.42, 42.66, 43.13]
SC_RSI = [70.53, 66.32, 66.55, 69.41, 66.36, 57.97, 62.93, 63.26, 56.06, 62.38, 54.71, 50.42, 39.99, 41.46, 41.87,
          45.46, 37.30, 33.08, 37.77]


def test_matches_stockcharts_reference():
    r = lp.rsi_wilder_sma(pd.Series(SC_CLOSE))
    assert r.iloc[:14].isna().all() and r.iloc[14:].notna().all()
    assert np.allclose(r.iloc[14:].to_numpy(), SC_RSI, atol=0.1)


def test_seed_is_sma_of_first_14_then_wilder_smoothing():
    c = pd.Series(SC_CLOSE)
    d = c.diff().iloc[1:].to_numpy()
    g, l = np.clip(d, 0, None), np.clip(-d, 0, None)
    ag, al = g[:14].mean(), l[:14].mean()
    exp = [100 - 100 / (1 + ag / al)]
    for i in range(14, len(d)):
        ag, al = (ag * 13 + g[i]) / 14, (al * 13 + l[i]) / 14
        exp.append(100 - 100 / (1 + ag / al))
    assert np.allclose(lp.rsi_wilder_sma(c).iloc[14:].to_numpy(), exp, atol=1e-9)


def test_edge_cases():
    up = pd.Series(np.arange(1.0, 31.0))
    assert (lp.rsi_wilder_sma(up).iloc[14:] == 100).all()                     # 하락 0 → 100
    flat = pd.Series([5.0] * 30)
    assert lp.rsi_wilder_sma(flat).isna().all()                              # 변화 0 → 판정 불가(NaN)
    assert lp.rsi_wilder_sma(pd.Series(SC_CLOSE[:14])).isna().all()          # 14봉 이하 → 전부 NaN
    idx = pd.date_range("2026-01-02", periods=33, freq="W-FRI")
    assert lp.rsi_wilder_sma(pd.Series(SC_CLOSE, index=idx)).index.equals(idx)


def test_converges_to_scanner_rsi_on_long_history():
    """시딩 차이는 앞부분에만 — 긴 이력의 끝에서는 두 방식이 같아진다(이력이 긴 종목의 판정은 그대로)."""
    rng = np.random.default_rng(1)
    s = pd.Series(1000 * np.cumprod(1 + rng.normal(0, 0.03, 600)))
    a, b = scanner.rsi(s, 14), lp.rsi_wilder_sma(s, 14)
    assert abs(a.iloc[20] - b.iloc[20]) > 0.5                                 # 앞: 다르다
    assert (a - b).iloc[-100:].abs().max() < 1e-6                             # 끝: 같다


def _short_weekly():
    """주봉 56개(최소 52 통과). 고전 Wilder: RSI[2] 30.60 / RSI[1] 29.64 → 히트, scanner.rsi: 32.51 / 31.47 → 아님."""
    rng = np.random.default_rng(334)
    w = 1000 * np.cumprod(1 + rng.normal(0, 0.06, 56))
    return pd.Series(w, index=pd.date_range(end="2026-10-02", periods=56, freq="W-FRI"))


def test_screen_uses_classic_wilder():
    s = _short_weekly()
    a, b = scanner.rsi(s, 14), lp.rsi_wilder_sma(s, 14)
    assert not (a.iloc[-2] < 30 <= a.iloc[-3]) and (b.iloc[-2] < 30 <= b.iloc[-3])   # 전제: 두 방식이 갈리는 표본
    res = lp.evaluate(s, "week", "kr", NOW)
    assert res["status"] == "hit"
    assert res["r1"] == pytest.approx(b.iloc[-2]) and res["r2"] == pytest.approx(b.iloc[-3])


def test_eval_page_uses_classic_wilder():
    s = _short_weekly()
    k = ev.stoch_rsi_k(s)
    r = lp.rsi_wilder_sma(s, 14)
    lo, hi = r.rolling(14).min(), r.rolling(14).max()
    exp = ((r - lo) / (hi - lo).replace(0, np.nan) * 100).rolling(3).mean()
    pd.testing.assert_series_equal(k, exp, check_names=False)
    for fn in (ev.long_checks, ev.manual_auto):
        src = inspect.getsource(fn)
        assert "lp.rsi_wilder_sma(months[\"Close\"], 14)" in src and "scanner.rsi" not in src


def test_five_tab_scanner_rsi_unchanged():
    """5탭(측정 기반)은 scanner.rsi 그대로 — 저점 교체가 번지지 않았다."""
    src = inspect.getsource(scanner.rsi)
    assert "ewm(alpha=1 / period, adjust=False)" in src
    import ast
    for path in ("scanner.py", "app.py"):   # 코드 참조만(주석·changelog 문장은 제외)
        tree = ast.parse(open(os.path.join(ROOT, path), encoding="utf-8").read())
        refs = [n for n in ast.walk(tree) if (isinstance(n, ast.Name) and n.id == "rsi_wilder_sma")
                or (isinstance(n, ast.Attribute) and n.attr == "rsi_wilder_sma")]
        assert refs == [], f"{path}가 저점 RSI를 쓴다"
