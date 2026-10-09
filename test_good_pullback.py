"""좋은 되돌림 측정(docs/ma600_good_pullback.md, docs/ma99_good_pullback.md) — 확인봉 판정·매물대·진입 고정.

사용자 지시(사보타주): 아래 중 하나라도 진입으로 잡히면 테스트 결함으로 보고 멈춘다.
  ① 매물대에 닿지 않은 눌림 ② 10~50일선 구간에 닿지 않은 눌림 ③ 50일선 아래로 종가 마감 후 회복 ④ ②·③ 충족 전에 나온 확인봉
  ⑤ 음봉 확인봉 ⑥ 21거래일째 확인봉. 반대로 모든 조건을 갖춘 가짜 경우는 진입으로 잡혀야 한다.
합성 데이터는 99일선 모듈로 돈다(로직은 600과 같은 모듈 — 선 길이만 다름).
"""
from __future__ import annotations

import importlib.util
import os
from collections import Counter

import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
MS = os.path.join(ROOT, "scripts", "measurements")


def _load(name, f):
    spec = importlib.util.spec_from_file_location(name, os.path.join(MS, f))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


g99 = _load("gp99", "2026-10-09_ma99_good_pullback.py").m
g600 = _load("gp600", "2026-10-09_ma600_good_pullback.py")

ZONE = {"lo": 104.0, "hi": 108.0}


def _hist():
    """기준봉 전 300봉: 종가 100(거래량 1), 60번 80·61번 120(250봉 범위 80~120 → 4원 폭 10구간), 200~204번 106(거래량 100 — 매물대),
    마지막(전일) 95. 기대 매물대 = 전일 종가 95 위 최대 거래량 구간 = [104, 108)."""
    rows = [(100.0, 100.5, 99.5, 100.0, 1.0)] * 300
    rows[60] = (80.0, 80.5, 79.5, 80.0, 1.0)
    rows[61] = (120.0, 120.5, 119.5, 120.0, 1.0)
    for i in range(200, 205):
        rows[i] = (106.0, 106.5, 105.5, 106.0, 100.0)
    rows[299] = (96.0, 96.5, 94.5, 95.0, 1.0)
    return rows


BULL = (96.0, 111.0, 95.5, 110.0, 5.0)                 # +15.8% 양봉, 종가 110 > MA99(≈100) → A
FLAT = [(107.5, 108.0, 107.0, 107.5, 1.0)] * 80


def _df(tail):
    rows = _hist() + [BULL] + [tuple(float(x) for x in r) + ((1.0,) if len(r) == 4 else ()) for r in tail]
    idx = pd.bdate_range("2023-01-02", periods=len(rows))
    o, h, lo, c, v = zip(*rows)
    return pd.DataFrame({"Open": o, "High": h, "Low": lo, "Close": c, "Volume": v}, index=idx)


K_BULL = 300
D1 = (109, 109.5, 104.5, 107)          # 눌림(107 < 110), 저가 104.5 ≤ 108 → ② 닿음, MA 구간 아님
D2 = (106, 106.5, 101, 105)            # 저가 101 ∈ [MA50≈100.3, MA10≈101.7] → ③ 닿음, 종가 105 ≥ 매물대 하단 104·MA50
D3 = (105.5, 108, 105, 107.5)          # 양봉, 종가 107.5 > 전일 고가 106.5 → 확인봉


def _fe(tail, zone=ZONE):
    df = _df(tail)
    r = g99.find_entry(g99.harness.CleanView(df), K_BULL, zone)
    return r["reason"], (r.get("k_confirm") - K_BULL if r.get("k_confirm") else None), r


def test_all_conditions_is_entry():
    reason, d, r = _fe([D1, D2, D3] + FLAT)
    assert (reason, d, r["day"], r["stop"]) == ("confirmed", 3, 3, 101.0)


def test_supply_zone_is_abc_profile_above_prev_close():
    df = _df([D1, D2, D3] + FLAT)
    p = g99.harness.CleanView(df).prefix(K_BULL)
    z = g99.supply_zone(p.iloc[:-1])
    assert (z["lo"], z["hi"]) == (104.0, 108.0)
    assert z == g99.abc_screener.supply_profile(p["Close"].iloc[:-1], p["Volume"].iloc[:-1], 95.0)["zone"]


def test_1_zone_not_touched_is_no_entry():
    """① 매물대 [90, 100]에 저가가 안 닿음(최저 101) → 미진입(③만 충족)."""
    reason, d, _ = _fe([D1, D2, D3] + FLAT, zone={"lo": 90.0, "hi": 100.0})
    assert reason == "fail_zone_only" and d is None


def test_2_ma_band_not_touched_is_no_entry():
    """② 저가 102.5(구간 ≈[100.3, 101.7] 밖) → 미진입(②만 충족)."""
    rising = [(108 + 0.6 * i, 108.5 + 0.6 * i, 107.8 + 0.6 * i, 108.3 + 0.6 * i) for i in range(80)]   # 저가가 늘 10일선 위
    reason, d, _ = _fe([D1, (106, 106.5, 102.5, 105), D3] + rising)
    assert reason == "fail_ma_only" and d is None


def test_3_close_below_ma50_then_recover_is_no_entry():
    """③ 1일째에 ②·③ 다 닿고, 2일째 종가 100 < MA50(≈100.3)(매물대 하단은 95로 두어 그쪽 이탈 아님) → 3일째 돌파해도 미진입."""
    reason, d, _ = _fe([(109, 109.5, 101, 107), (106, 106.5, 99.8, 100), (100.5, 108, 100, 107.5)] + FLAT,
                       zone={"lo": 95.0, "hi": 108.0})
    assert reason == "break_ma50" and d is None


def test_4_confirm_before_conditions_is_not_confirm():
    """④ 2일째 돌파 양봉(③ 미충족) → 확인봉 아님. 3일째 ③ 충족, 4일째 돌파 → 확인봉은 4일째."""
    reason, d, _ = _fe([D1, (107, 111, 106.5, 110), (109, 109.5, 101.5, 105), (105.5, 111, 105, 110)] + FLAT)
    assert (reason, d) == ("confirmed", 4)


def test_5_bearish_breakout_is_not_confirm():
    """⑤ 3일째 음봉(시가 108 → 종가 107.5)이 전일 고가 106.5를 넘어도 확인봉 아님."""
    reason, d, _ = _fe([D1, D2, (108, 108.5, 105, 107.5)] + [(107.5, 107.6, 107.0, 107.2)] * 80)
    assert reason == "no_confirm_bar" and d is None


def test_6_confirm_on_day_21_is_no_entry():
    """⑥ 조건은 2일째까지 충족, 3~20일 비돌파 음봉, 21일째 돌파 → 미진입. 20일째 돌파면 확인봉."""
    idle = (106, 106.5, 105.5, 105.8)
    brk = (105.9, 108, 105.5, 107.9)
    assert _fe([D1, D2] + [idle] * 18 + [brk] + FLAT)[0] == "no_confirm_bar"
    assert _fe([D1, D2] + [idle] * 17 + [brk] + FLAT)[:2] == ("confirmed", 20)


def test_no_zone_is_no_entry():
    assert _fe([D1, D2, D3] + FLAT, zone=None)[0] == "no_zone"


def test_scan_entry_next_open_stop_min_low(monkeypatch):
    """scan_ticker 전체 경로: 실제 매물대 계산 → 확인봉 → 다음 날 시가 진입, 손절 = 기준봉 다음 날~확인봉 최저 저가."""
    monkeypatch.setattr(g99.bcs.prev, "liquid", lambda p: True)      # race_open이 보는 저유동성 컷만 우회(합성 거래량)
    df = _df([D1, D2, D3] + FLAT)
    st = Counter()
    rows = g99.scan_ticker("T.KS", g99.harness.CleanView(df), st)
    assert len(rows) == 1 and rows[0]["group"] == "A"
    r = rows[0]
    assert r["entry"] == 107.5 and r["stop"] == 101.0 and r["date"] == str(df.index[K_BULL + 4].date())
    assert r["d63_incomplete"] is False and abs(r["d63_pct"] - (107.5 / 107.5 - 1) * 100) < 1e-9


def test_forward_from_entry_hand():
    df = _df([D1, D2, D3] + [(107.5 + i, 108 + i, 107 + i, 107.5 + i) for i in range(70)])
    f = g99.forward_from_entry(g99.harness.CleanView(df), K_BULL + 3, 107.5)
    assert abs(f["d63_pct"] - ((107.5 + 62) / 107.5 - 1) * 100) < 1e-9
    assert abs(f["d63_max_pct"] - ((108 + 62) / 107.5 - 1) * 100) < 1e-9 and abs(f["d63_min_pct"] - (107 / 107.5 - 1) * 100) < 1e-9


def test_params():
    assert g99.MA_N == 99 and g99.line.MA_N == 99 and g99.line.MIN_BARS == 100
    assert g600.MA_N == 600 and g600.line.MIN_BARS == 601
    for m in (g99, g600):
        assert m.Z_MIN == 2.69 and m.EV_MIN == 0.15 and m.N_MIN == 100 and m.WINDOW == 20 and (m.MA_FAST, m.MA_SLOW) == (10, 50)
        assert m.D_FWD == 63 and m.line.DATA_DAYS == 3000 and m.RUN_MARKETS == ["KR"] and m.prev.MAX_BARS == 60
    assert g99.abc_screener.ABC_CONFIG["a_lookback"] == 250 and g99.abc_screener.ABC_CONFIG["supply_profile_bins"] == 10
    assert g99.OUT.endswith("ma99_good_pullback.results.json") and g600.OUT.endswith("ma600_good_pullback.results.json")
