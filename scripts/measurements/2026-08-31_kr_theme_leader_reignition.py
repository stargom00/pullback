"""
KR 테마 리더 재점화 가설 측정 (2026-08-31, 사용자 지시, 사전 등록).

가설: "KR 테마 전(前) 사이클 리더는 일정 기간 후 재점화한다" — 과거
사이클의 점화 리더(D0 leader)가 이후 30~180거래일 창에서 다시 급등하는
비율이, 같은 테마 비리더 및 유동성매칭 무작위 대조군보다 높은가.

방법론:
  - theme_map.json 6개 테마(사용자가 "8개"라 했으나 실제로는 6개만
    존재 — 8개는 사용자의 착오로 판단, 그대로 진행) × 가용 최대 기간
    (KR 1900일 fetch, ≈1273봉 — jongga/support_breach_1900d 관행과
    동일, scripts/measurements/2026-08-31_kr_pullback_support_breach_1900d.py
    의 fetch_kr_long_universe()를 그대로 import해서 재사용, 새로 구현
    안 함).
  - D0/리더 추출: theme_lifecycle.compute_theme_series(window=max) +
    find_cycles() 그대로 재사용(사이클 리셋 로직 재구현 안 함). window을
    "시장 데이터 전체 - BASELINE_WINDOW"로 최대화해서 60일 창이 아니라
    가용 전체 기간에서 사이클을 추출한다.
  - 재점화 판정: D0 이후 30~180거래일 내 (단일일 +15%↑ OR 5거래일
    누적 +25%↑) AND 거래량 ≥ 트레일링20일평균×2. (거래량 창은
    theme_lifecycle.TURNOVER_RATIO_WINDOW=20과 동일하게 20일로 통일 —
    별도 이유 없이 다르게 잴 이유가 없어서.)
  - 규칙6(대조군 유동성매칭): Control B는 harness.KR_LIQUIDITY_FLOOR
    (3억원/일) 통과 종목에서만 무작위 추출.
  - 규칙7(z검정): harness.ev_gap_zscore 재사용(EV 비교), 재점화율
    비교는 표준 2-표본 비율 z검정(별도 구현, 아래 two_prop_z()).
  - 규칙8: KR 전용(theme_lifecycle.py 자체가 KR 전용이라 해당 없음,
    US 비교 대상 아님 — 명시).
"""
import sys
import os
import json
import time
import math
import random
import importlib.util

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.chdir(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import harness
import theme_lifecycle as tl
from universe import get_universe

random.seed(20260831)

# ── fetch_kr_long_universe() 재사용(재구현 안 함) ──────────────────────
_SB_PATH = os.path.join("scripts", "measurements",
                         "2026-08-31_kr_pullback_support_breach_1900d.py")
_spec = importlib.util.spec_from_file_location("sb1900d", _SB_PATH)
sb1900d = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sb1900d)

REIGNITE_WINDOW_START = 30
REIGNITE_WINDOW_END = 180
REIGNITE_SINGLE_RET = 0.15
REIGNITE_5D_RET = 0.25
REIGNITE_VOL_MULT = 2.0
VOL_AVG_WINDOW = 20
COMPRESSION_LOOKBACK = 20
CONFIRM_VOL_MULT = 1.5
CONFIRM_BARS = 3
CONFIRM_VOL_AVG_WINDOW = 50


def two_prop_z(x1, n1, x2, n2):
    """표준 2표본 비율 z검정. 반환 (z, significant)."""
    if n1 == 0 or n2 == 0:
        return None, False
    p1, p2 = x1 / n1, x2 / n2
    p_pool = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
    if se == 0:
        return None, False
    z = (p1 - p2) / se
    return z, abs(z) >= 1.96


def check_reignition(df, d0_pos):
    """d0_pos 이후 30~180거래일 창에서 재점화 이벤트 탐색.
    반환: {"reignited": bool, "lag": int|None, "insufficient": bool,
           "reignite_pos": int|None}"""
    closes = df["Close"]
    vols = df["Volume"]
    n = len(df)
    start = d0_pos + REIGNITE_WINDOW_START
    end = d0_pos + REIGNITE_WINDOW_END
    if start >= n:
        return {"reignited": False, "lag": None, "insufficient": True, "reignite_pos": None}
    end = min(end, n - 1)
    insufficient = (n - 1) < (d0_pos + REIGNITE_WINDOW_END)  # 180일까지 데이터 없으면 표시(단, 부분창은 그래도 탐색)
    for i in range(start, end + 1):
        if i < 1:
            continue
        avg20 = vols.iloc[max(0, i - VOL_AVG_WINDOW):i].mean()
        if not avg20 or (isinstance(avg20, float) and math.isnan(avg20)) or avg20 <= 0:
            continue
        ret1 = float(closes.iloc[i] / closes.iloc[i - 1] - 1)
        vol_ok_1d = float(vols.iloc[i]) >= REIGNITE_VOL_MULT * avg20
        if ret1 >= REIGNITE_SINGLE_RET and vol_ok_1d:
            return {"reignited": True, "lag": i - d0_pos, "insufficient": False, "reignite_pos": i}
        if i >= 4:
            ret5 = float(closes.iloc[i] / closes.iloc[i - 4] - 1)
            vol_ok_5d = float(vols.iloc[i - 4:i + 1].max()) >= REIGNITE_VOL_MULT * avg20
            if ret5 >= REIGNITE_5D_RET and vol_ok_5d:
                return {"reignited": True, "lag": i - d0_pos, "insufficient": False, "reignite_pos": i}
    return {"reignited": False, "lag": None, "insufficient": insufficient, "reignite_pos": None}


def check_compression(df, reignite_pos):
    """재점화일 D-20~D-1 응축 여부: ATR(14) D-1 < D-20, 그리고/또는
    거래량 5일평균 D-1 < D-20. 반환 dict(atr_contract, vol_decline, either, both)."""
    if reignite_pos < COMPRESSION_LOOKBACK + 14:
        return None
    high, low, close, vol = df["High"], df["Low"], df["Close"], df["Volume"]
    tr = (high - low).combine((close.shift(1) - low).abs(), max).combine(
        (high - close.shift(1)).abs(), max)
    atr14 = tr.rolling(14).mean()
    d_minus_1 = reignite_pos - 1
    d_minus_20 = reignite_pos - COMPRESSION_LOOKBACK
    atr_d1 = atr14.iloc[d_minus_1]
    atr_d20 = atr14.iloc[d_minus_20]
    if math.isnan(atr_d1) or math.isnan(atr_d20):
        atr_contract = None
    else:
        atr_contract = bool(atr_d1 < atr_d20)
    vol5_d1 = vol.iloc[max(0, d_minus_1 - 4):d_minus_1 + 1].mean()
    vol5_d20 = vol.iloc[max(0, d_minus_20 - 4):d_minus_20 + 1].mean()
    vol_decline = bool(vol5_d1 < vol5_d20) if (vol5_d1 == vol5_d1 and vol5_d20 == vol5_d20) else None
    either = (atr_contract or vol_decline) if (atr_contract is not None or vol_decline is not None) else None
    both = (atr_contract and vol_decline) if (atr_contract is not None and vol_decline is not None) else None
    return {"atr_contract": atr_contract, "vol_decline": vol_decline, "either": either, "both": both}


def confirm_entry_race(df, reignite_pos, max_confirm_bars=CONFIRM_BARS, max_race_bars=60):
    """안C형 확인진입: 재점화일 고가 돌파 + 거래량>=1.5x트레일링50일평균,
    reignite_pos 이후 max_confirm_bars봉 내 미발생시 제외."""
    n = len(df)
    sig_high = float(df["High"].iloc[reignite_pos])
    sig_low = float(df["Low"].iloc[reignite_pos])
    vols = df["Volume"]
    for k in range(1, max_confirm_bars + 1):
        j = reignite_pos + k
        if j >= n:
            break
        avg50 = vols.iloc[max(0, j - CONFIRM_VOL_AVG_WINDOW):j].mean()
        if not avg50 or avg50 <= 0:
            continue
        hi = float(df["High"].iloc[j])
        if hi >= sig_high and float(vols.iloc[j]) >= CONFIRM_VOL_MULT * avg50:
            entry = sig_high
            stop = sig_low
            future = df.iloc[j:j + max_race_bars] if j + max_race_bars <= n else df.iloc[j:n]
            # harness.race expects entry/stop/future_df with High/Low columns already sliced from entry bar
            outcome, r = harness.race(entry, stop, future, max_bars=max_race_bars)
            return outcome, r
    return None, None


def avg_turnover(df, as_of_pos, window=60):
    sub = df.iloc[max(0, as_of_pos - window):as_of_pos]
    if sub.empty:
        return 0.0
    return float((sub["Close"] * sub["Volume"]).mean())


def main():
    t0 = time.time()
    print("[main] KR 1900일 유니버스 fetch 시작 (fetch_kr_long_universe 재사용)...", flush=True)
    data = sb1900d.fetch_kr_long_universe(concurrency=10)
    print(f"[main] fetch 완료 {len(data)}종목, {time.time()-t0:.0f}s", flush=True)

    with open("theme_map.json") as f:
        theme_map = json.load(f)
    print(f"[main] theme_map.json 테마 수: {len(theme_map)} — {list(theme_map.keys())}", flush=True)

    market_turnover = tl.market_daily_turnover(data)
    print(f"[main] market_turnover 길이: {len(market_turnover)}", flush=True)
    max_window = len(market_turnover) - tl.BASELINE_WINDOW - 1

    # ── 1) 전체 테마에서 D0 리더 이벤트 추출 (find_cycles 재사용) ──────
    leader_events = []   # [{theme, ticker, d0_pos_in_df, d0_date}]
    theme_data_map = {}
    for name, entry in theme_map.items():
        stocks = entry.get("stocks", [])
        theme_data = tl.compute_theme_series(stocks, data, market_turnover, window=max_window)
        if theme_data is None:
            print(f"[main] {name}: theme_data 없음(구성종목 데이터 부족), 스킵", flush=True)
            continue
        theme_data_map[name] = theme_data
        cycles = tl.find_cycles(theme_data)
        print(f"[main] {name}: {len(cycles)}개 사이클 탐지", flush=True)
        for c in cycles:
            leader = c["d0"]["leader"]
            d0_date = c["d0"]["date"]
            df_t = data.get(leader)
            if df_t is None or d0_date not in df_t.index:
                continue
            d0_pos = df_t.index.get_loc(d0_date)
            leader_events.append({"theme": name, "ticker": leader, "d0_date": str(d0_date.date()),
                                   "d0_pos": d0_pos, "leader_ret_pct": c["d0"]["leader_ret_pct"],
                                   "z": c["d0"]["z"]})

    print(f"[main] 총 D0 리더 이벤트: {len(leader_events)}", flush=True)
    for e in leader_events:
        print(f"   {e['theme']} {e['ticker']} D0={e['d0_date']} z={e['z']} leader_ret={e['leader_ret_pct']}", flush=True)

    # ── 2) 리더 재점화율 + 소요일 ────────────────────────────────────
    leader_results = []
    for e in leader_events:
        df_t = data[e["ticker"]]
        res = check_reignition(df_t, e["d0_pos"])
        leader_results.append({**e, **res})

    leader_valid = [r for r in leader_results if not r["insufficient"] or r["reignited"]]
    n_leader = len(leader_valid)
    n_leader_reignite = sum(1 for r in leader_valid if r["reignited"])
    lag_dist = [r["lag"] for r in leader_valid if r["reignited"]]

    print(f"[main] 리더 재점화: {n_leader_reignite}/{n_leader}", flush=True)

    # ── 3) Control A: 같은 테마 비리더, 동일 D0-상대 창 ─────────────
    controlA_results = []
    for e in leader_events:
        td = theme_data_map[e["theme"]]
        all_tickers = td["tickers"]
        for t in all_tickers:
            if t == e["ticker"]:
                continue
            df_t = data.get(t)
            if df_t is None or e["d0_date"] not in [str(d.date()) for d in [df_t.index[0]]]:
                pass
            # d0_pos: 같은 날짜를 이 종목 df에서 찾음(없으면 스킵)
            import pandas as pd
            d0_dt = pd.Timestamp(e["d0_date"])
            if df_t is None or d0_dt not in df_t.index:
                continue
            d0_pos_t = df_t.index.get_loc(d0_dt)
            res = check_reignition(df_t, d0_pos_t)
            controlA_results.append({"theme": e["theme"], "ticker": t, "d0_date": e["d0_date"], **res})

    controlA_valid = [r for r in controlA_results if not r["insufficient"] or r["reignited"]]
    n_A = len(controlA_valid)
    n_A_reignite = sum(1 for r in controlA_valid if r["reignited"])
    print(f"[main] Control A(같은테마 비리더) 재점화: {n_A_reignite}/{n_A}", flush=True)

    # ── 4) Control B: 유동성매칭 무작위 KR, 무작위 시작일 ───────────
    kr_u = get_universe("kr")
    all_kr_tickers = [t for t in kr_u if t in data]
    controlB_results = []
    n_target_B = max(200, n_leader * 5)
    attempts = 0
    while len(controlB_results) < n_target_B and attempts < n_target_B * 8:
        attempts += 1
        t = random.choice(all_kr_tickers)
        df_t = data[t]
        n = len(df_t)
        if n < REIGNITE_WINDOW_END + 80:
            continue
        pos0 = random.randint(60, n - REIGNITE_WINDOW_END - 1)
        turn = avg_turnover(df_t, pos0, window=60)
        if turn < harness.KR_LIQUIDITY_FLOOR:
            continue
        res = check_reignition(df_t, pos0)
        controlB_results.append({"ticker": t, "pos0": pos0, **res})

    controlB_valid = [r for r in controlB_results if not r["insufficient"] or r["reignited"]]
    n_B = len(controlB_valid)
    n_B_reignite = sum(1 for r in controlB_valid if r["reignited"])
    print(f"[main] Control B(유동성매칭 무작위) 재점화: {n_B_reignite}/{n_B} (표본목표={n_target_B}, 시도={attempts})", flush=True)

    z_leader_vs_A, sig_A = two_prop_z(n_leader_reignite, n_leader, n_A_reignite, n_A)
    z_leader_vs_B, sig_B = two_prop_z(n_leader_reignite, n_leader, n_B_reignite, n_B)
    print(f"[main] 리더 vs A: z={z_leader_vs_A}, 유의={sig_A}", flush=True)
    print(f"[main] 리더 vs B: z={z_leader_vs_B}, 유의={sig_B}", flush=True)

    # ── 5) 응축 여부(재점화 사례만) ──────────────────────────────────
    reignited_leader = [r for r in leader_valid if r["reignited"]]
    compression_results = []
    for r in reignited_leader:
        df_t = data[r["ticker"]]
        comp = check_compression(df_t, r["reignite_pos"])
        if comp is not None:
            compression_results.append(comp)
    n_comp = len(compression_results)
    n_either = sum(1 for c in compression_results if c["either"])
    n_both = sum(1 for c in compression_results if c["both"])
    n_atr = sum(1 for c in compression_results if c["atr_contract"])
    n_vol = sum(1 for c in compression_results if c["vol_decline"])
    print(f"[main] 응축체크(n={n_comp}): ATR수축={n_atr}, 거래량감소={n_vol}, 둘중하나={n_either}, 둘다={n_both}", flush=True)

    # ── 6) 확인진입 EV (안C형) ───────────────────────────────────────
    outcomes = []
    for r in reignited_leader:
        df_t = data[r["ticker"]]
        outcome, rr = confirm_entry_race(df_t, r["reignite_pos"])
        if outcome is not None:
            outcomes.append((outcome, rr))
    ev_confirm = harness.ev_summary(outcomes)
    print(f"[main] 확인진입 EV: {ev_confirm}", flush=True)

    # ── 판정 ──────────────────────────────────────────────────────
    ev_r = ev_confirm.get("ev_R")
    adopted = bool(sig_A and sig_B and z_leader_vs_A and z_leader_vs_A > 0
                   and z_leader_vs_B and z_leader_vs_B > 0
                   and ev_r is not None and ev_r >= 0.30)
    print(f"[main] 판정: {'채택' if adopted else '기록(미채택)'}", flush=True)

    result = {
        "n_themes": len(theme_data_map), "themes": list(theme_data_map.keys()),
        "n_leader_events": len(leader_events), "leader_events": leader_events,
        "n_leader": n_leader, "n_leader_reignite": n_leader_reignite,
        "lag_dist": lag_dist,
        "n_A": n_A, "n_A_reignite": n_A_reignite,
        "n_B": n_B, "n_B_reignite": n_B_reignite,
        "z_leader_vs_A": z_leader_vs_A, "sig_A": sig_A,
        "z_leader_vs_B": z_leader_vs_B, "sig_B": sig_B,
        "n_comp": n_comp, "n_atr": n_atr, "n_vol": n_vol, "n_either": n_either, "n_both": n_both,
        "ev_confirm": ev_confirm,
        "adopted": adopted,
        "elapsed_s": time.time() - t0,
    }
    with open("/tmp/kr_theme_leader_reignition_result.json", "w") as f:
        json.dump(result, f, indent=2, default=str)
    print("[main] 결과 JSON: /tmp/kr_theme_leader_reignition_result.json (커밋 대상 아님, 참고용)", flush=True)
    print(f"[main] 총 소요시간 {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
