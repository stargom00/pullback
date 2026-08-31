"""
KR/US 시장 구조 비교 — "베이스를 만드는 종목이 US에 더 많다" 가설의 직접
측정 (2026-08-31, 사용자 지시). docs/kr_us_strategy_map.md("왜 KR=돌파계열
우위/US=계열무차이인가")의 "왜"에 해당하는 구조적 지도 문서 생성용.

측정 스크립트만 — scanner.py/app.py 미수정. 공통 하네스(harness.py) 재사용,
기존 collect_simple_tab 패턴(2026-08-29_breakout_vs_pullback_family_kr_us.py)
그대로 가져다 씀. 규칙6(유동성매칭)은 1절 베이스형성빈도 비교에 적용(대조군이
아니라 같은 시장 내 유동성 상위/하위 티어 비교 형태로 적용 — 아래 방법론 참고).
규칙7(z검정)은 이번 측정엔 사전등록 채택/기각 판정이 없어(사용자 지시: "판정이
아니라 지도") 미사용. 규칙8(KR/US 미혼합) — 전 절 KR/US 완전 분리 보고, 아래
결과 섹션 전부 market 컬럼으로 분리됨.

【이번 측정은 판정이 아니라 지도】 사전 등록 채택/기각 기준 없음. 결과는
docs/kr_us_market_structure.md에 표로만 정리.

실행: 리포 루트에서
  python3 scripts/measurements/2026-08-31_kr_us_market_structure.py
(전체 유니버스 fetch + RS 사전계산 + 3개 탭 x 20 체크포인트 analyze 호출 +
1년 주봉 베이스 스캔. breakout_vs_pullback_family 스크립트와 비슷한 비용,
30~45분 예상)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import time
import statistics as stats

import pandas as pd

import harness
from scanner import (
    to_rs_rank,
    analyze_turnaround, TURN_CONFIG,
    analyze_breakout, BREAKOUT_CONFIG,
    analyze_boxbreak, BOXBREAK_CONFIG,
)

OFFSETS = harness.checkpoints(60, 250, 10)  # 20개, 이 레포 표준 체크포인트
RS_DELTA_LOOKBACK = 20

# ── 조작적 정의 (전부 이 스크립트 안에서만 사용, scanner.py 게이트와 무관) ──
# 베이스 상태: 트레일링 4주+ 창에서 고저폭<=15% & 거래량 수축(최근 2주 평균이
# 창 시작 전 8주 평균보다 낮음). Minervini VCP 개념의 단순화(주봉 기준).
BASE_MIN_WEEKS = 4
BASE_RANGE_MAX = 0.15
CONTRACTION_LOOKBACK_WEEKS = 8
VERTICAL_RUN_DAYS = 20
VERTICAL_RUN_PCT = 0.50
PRE_BREAKOUT_LOOKBACK_WEEKS = 16   # 돌파 직전 몇 주를 "직전 구조"로 볼지
PERSIST_HORIZONS = (20, 40, 60)     # 거래일


def is_kr(t):
    return harness.is_kr_ticker(t)


# ── 주봉 리샘플 + 베이스 상태 시퀀스 ────────────────────────────────────
def weekly_bars(df: pd.DataFrame) -> pd.DataFrame:
    """일봉 df -> 주봉(W-FRI) OHLCV. 마지막(미완결) 주는 제외."""
    if df is None or len(df) < 30:
        return None
    w = pd.DataFrame({
        "High": df["High"].resample("W-FRI").max(),
        "Low": df["Low"].resample("W-FRI").min(),
        "Close": df["Close"].resample("W-FRI").last(),
        "Volume": df["Volume"].resample("W-FRI").sum(),
    }).dropna()
    if len(w) >= 2:
        w = w.iloc[:-1]  # 마지막 행은 미완결 주 가능성 있어 제외(보수적)
    return w


def base_state_sequence(w: pd.DataFrame):
    """주봉 df -> 각 주(index i)가 '베이스 상태'인지 bool 리스트. i는
    BASE_MIN_WEEKS + CONTRACTION_LOOKBACK_WEEKS - 1 부터 평가 가능(그 전은 None)."""
    n = len(w)
    need = BASE_MIN_WEEKS + CONTRACTION_LOOKBACK_WEEKS
    out = [None] * n
    hi = w["High"].values
    lo = w["Low"].values
    vol = w["Volume"].values
    for i in range(need - 1, n):
        win_hi = hi[i - BASE_MIN_WEEKS + 1: i + 1].max()
        win_lo = lo[i - BASE_MIN_WEEKS + 1: i + 1].min()
        if win_hi <= 0:
            out[i] = None
            continue
        range_pct = (win_hi - win_lo) / win_hi
        recent_vol = vol[max(0, i - 1): i + 1].mean()  # 최근 2주
        lookback_start = i - BASE_MIN_WEEKS + 1 - CONTRACTION_LOOKBACK_WEEKS
        lookback_end = i - BASE_MIN_WEEKS + 1
        if lookback_start < 0:
            out[i] = None
            continue
        prior_vol = vol[lookback_start:lookback_end].mean()
        contracting = (prior_vol > 0) and (recent_vol < 0.8 * prior_vol)
        out[i] = (range_pct <= BASE_RANGE_MAX) and contracting
    return out


def detect_base_runs(seq):
    """base_state_sequence 결과 -> 연속 True 구간(run) 리스트: [(start_i, end_i), ...]
    (end_i 포함, 길이>=BASE_MIN_WEEKS인 것만)."""
    runs = []
    start = None
    for i, v in enumerate(seq):
        if v:  # truthy check — v may be numpy.bool_, `is True` fails for that
            if start is None:
                start = i
        else:
            if start is not None and (i - start) >= BASE_MIN_WEEKS:
                runs.append((start, i - 1))
            start = None
    if start is not None and (len(seq) - start) >= BASE_MIN_WEEKS:
        runs.append((start, len(seq) - 1))
    return runs


def run_quality(w: pd.DataFrame, start_i, end_i):
    """base run 하나의 length(주)/depth(%)/vol_contraction_ratio/vcp_legs."""
    seg = w.iloc[start_i:end_i + 1]
    length = len(seg)
    hi = seg["High"].max()
    lo = seg["Low"].min()
    depth = (hi - lo) / hi if hi > 0 else None
    lookback_start = max(0, start_i - CONTRACTION_LOOKBACK_WEEKS)
    prior = w.iloc[lookback_start:start_i]
    vol_ratio = None
    if len(prior) > 0 and prior["Volume"].mean() > 0:
        vol_ratio = seg["Volume"].mean() / prior["Volume"].mean()
    legs = vcp_legs(seg)
    return {"length_weeks": length, "depth_pct": depth, "vol_contraction_ratio": vol_ratio,
            "n_legs": len(legs), "legs_monotone_tightening": is_monotone_tightening(legs)}


def vcp_legs(seg: pd.DataFrame):
    """세그먼트의 주봉 종가에서 local peak->다음 local trough 페어를 찾아
    각 leg의 낙폭%(peak 대비)을 리스트로. 단순 로컬극값 기준(1주 좌우 비교)."""
    c = seg["Close"].values
    n = len(c)
    if n < 3:
        return []
    peaks, troughs = [], []
    for i in range(1, n - 1):
        if c[i] >= c[i - 1] and c[i] >= c[i + 1]:
            peaks.append(i)
        if c[i] <= c[i - 1] and c[i] <= c[i + 1]:
            troughs.append(i)
    legs = []
    pi = 0
    for p in peaks:
        nxt = [t for t in troughs if t > p]
        if not nxt:
            continue
        t = nxt[0]
        if c[p] > 0:
            legs.append((c[p] - c[t]) / c[p])
    return legs


def is_monotone_tightening(legs, min_legs=3, tol=1e-9):
    if len(legs) < min_legs:
        return False
    for i in range(1, len(legs)):
        if legs[i] > legs[i - 1] + tol:
            return False
    return True


# ── 1/2절: 베이스형성빈도 + 품질 (유니버스 전체, 최근 1년 주봉) ──────────
def section_1_2(data):
    t0 = time.time()
    stockweek_rows = []   # (ticker, is_kr, avg_turnover, in_base:bool)
    run_rows = []          # (ticker, is_kr, **run_quality)
    n_done = 0
    for t, df in data.items():
        w = weekly_bars(df)
        if w is None or len(w) < (BASE_MIN_WEEKS + CONTRACTION_LOOKBACK_WEEKS + 52):
            n_done += 1
            continue
        w52 = w.iloc[-52:]
        avg_turn = float((df["Close"] * df["Volume"]).iloc[-252:].mean()) if len(df) >= 60 else None
        ikr = is_kr(t)
        seq_full = base_state_sequence(w)
        # 최근 52주 구간에 해당하는 seq만 사용(단, seq 계산엔 그 이전 lookback도 필요해 전체로 계산했음)
        offset = len(w) - 52
        seq_52 = seq_full[offset:]
        for v in seq_52:
            if v is None:
                continue
            stockweek_rows.append((t, ikr, avg_turn, v))
        runs = detect_base_runs(seq_full)
        for (s, e) in runs:
            if e < offset:  # 최근 52주 밖 run은 제외
                continue
            q = run_quality(w, s, e)
            run_rows.append({"ticker": t, "is_kr": ikr, **q})
        n_done += 1
        if n_done % 500 == 0:
            print(f"[section1/2] {n_done}/{len(data)} elapsed={time.time()-t0:.0f}s", flush=True)
    print(f"[section1/2] 완료 {n_done}종목, stockweeks={len(stockweek_rows)}, runs={len(run_rows)}, "
          f"elapsed={time.time()-t0:.0f}s", flush=True)
    return stockweek_rows, run_rows


def liquidity_tier(avg_turn, ikr):
    """production 유동성 하한 통과 여부 + 통과분 내 상/하위 분할.
    KR/US 절대 turnover가 다른 스케일이라(원화 vs 달러) '매칭'은 각 시장의
    production 유동성컷(harness.KR/US_LIQUIDITY_FLOOR, 이 레포 기존 상수 재사용)
    통과 여부로 먼저 필터하고, 그 통과분 내부에서만 상/하위 절반으로 나눠
    비교한다 — 절대 turnover값 자체를 KR/US 간 직접 매칭하지 않음(스케일이
    달라 의미 없음), '이미 스캔 대상이 될 만큼 유동적인 종목들 사이에서'
    비교하는 것이 이번 측정의 매칭 정의."""
    floor_ = harness.KR_LIQUIDITY_FLOOR if ikr else harness.US_LIQUIDITY_FLOOR
    if avg_turn is None or avg_turn < floor_:
        return "illiquid"
    return "liquid"  # 상하위 분할은 report 단계에서 median 기준으로 별도 처리


def report_section_1(stockweek_rows):
    def frac(rows):
        n = len(rows)
        if n == 0:
            return None, 0
        k = sum(1 for r in rows if r[3])
        return k / n, n

    kr_all = [r for r in stockweek_rows if r[1]]
    us_all = [r for r in stockweek_rows if not r[1]]
    headline_kr, n_kr = frac(kr_all)
    headline_us, n_us = frac(us_all)

    kr_liquid = [r for r in kr_all if r[2] is not None and r[2] >= harness.KR_LIQUIDITY_FLOOR]
    us_liquid = [r for r in us_all if r[2] is not None and r[2] >= harness.US_LIQUIDITY_FLOOR]
    liq_kr, n_liq_kr = frac(kr_liquid)
    liq_us, n_liq_us = frac(us_liquid)

    # 유동성통과분 내부에서 상/하위 절반(중앙값 기준)
    def tier_split(rows):
        turns = sorted(r[2] for r in rows if r[2] is not None)
        if not turns:
            return None, None
        med = turns[len(turns) // 2]
        top = [r for r in rows if r[2] is not None and r[2] >= med]
        bot = [r for r in rows if r[2] is not None and r[2] < med]
        return frac(top), frac(bot)

    (kr_top_frac, kr_top_n), (kr_bot_frac, kr_bot_n) = tier_split(kr_liquid)
    (us_top_frac, us_top_n), (us_bot_frac, us_bot_n) = tier_split(us_liquid)

    return {
        "headline": {"kr": (headline_kr, n_kr), "us": (headline_us, n_us)},
        "liquidity_filtered": {"kr": (liq_kr, n_liq_kr), "us": (liq_us, n_liq_us)},
        "liquid_top_half": {"kr": (kr_top_frac, kr_top_n), "us": (us_top_frac, us_top_n)},
        "liquid_bottom_half": {"kr": (kr_bot_frac, kr_bot_n), "us": (us_bot_frac, us_bot_n)},
    }


def median_iqr(vals):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None, None, None
    n = len(vals)
    med = stats.median(vals)
    q1 = vals[int(n * 0.25)]
    q3 = vals[min(n - 1, int(n * 0.75))]
    return med, q1, q3


def report_section_2(run_rows):
    out = {}
    for ikr, label in ((True, "kr"), (False, "us")):
        rows = [r for r in run_rows if r["is_kr"] == ikr]
        n = len(rows)
        lens = [r["length_weeks"] for r in rows]
        depths = [r["depth_pct"] for r in rows]
        vratios = [r["vol_contraction_ratio"] for r in rows]
        vcp_n = sum(1 for r in rows if r["legs_monotone_tightening"])
        out[label] = {
            "n_bases": n,
            "length_weeks": median_iqr(lens),
            "depth_pct": median_iqr(depths),
            "vol_contraction_ratio": median_iqr(vratios),
            "vcp_qualifying_frac": (vcp_n / n) if n else None,
            "vcp_qualifying_n": vcp_n,
        }
    return out


# ── 3/4절: 실제 돌파계열 히트의 직전 구조 + 이후 지속성 ──────────────────
def precompute_rs(data, kospi_close, kosdaq_close):
    t0 = time.time()
    tickers = list(data.keys())
    extra_offsets = sorted(set(OFFSETS) | {o + RS_DELTA_LOOKBACK for o in OFFSETS})
    rs_cache = {}
    for off in extra_offsets:
        b_kospi = harness.bench_score_at(kospi_close, off)
        b_kosdaq = harness.bench_score_at(kosdaq_close, off)
        trunc_cache = {}
        for t in tickers:
            df = data[t]
            if len(df) - off < 200:
                continue
            trunc_cache[t] = harness.truncate_at(df, off)
        rs_ranks, rs_moms = harness.compute_rs_at_checkpoint(trunc_cache, b_kospi, b_kosdaq)
        rs_cache[off] = (rs_ranks, rs_moms)
        print(f"[rs-precompute] offset {off} 완료 elapsed={time.time()-t0:.0f}s", flush=True)
    return rs_cache


def pre_breakout_structure(hist: pd.DataFrame):
    """hist = 돌파 시점까지 truncate된 일봉 df. 직전 PRE_BREAKOUT_LOOKBACK_WEEKS주
    구조 특징 반환: base_len_weeks(0=없음), vertical_runup(bool)."""
    w = weekly_bars(hist)
    base_len = 0
    if w is not None and len(w) >= (BASE_MIN_WEEKS + CONTRACTION_LOOKBACK_WEEKS + PRE_BREAKOUT_LOOKBACK_WEEKS):
        seq = base_state_sequence(w)
        runs = detect_base_runs(seq)
        last_i = len(w) - 1
        # 돌파 직전 주(마지막 주 또는 그 바로 전)에서 끝나는 run이 있으면 그 길이
        for (s, e) in runs:
            if e >= last_i - 1:
                base_len = e - s + 1
                break
    vertical = False
    c = hist["Close"].dropna()
    if len(c) >= VERTICAL_RUN_DAYS + 1:
        past = float(c.iloc[-VERTICAL_RUN_DAYS - 1])
        if past > 0:
            vertical = (float(c.iloc[-1]) / past - 1) >= VERTICAL_RUN_PCT
    return base_len, vertical


def forward_returns(entry, df: pd.DataFrame, off: int):
    future = harness.future_after(df, off)
    out = {}
    for h in PERSIST_HORIZONS:
        if len(future) >= h and entry:
            px = float(future["Close"].iloc[h - 1])
            out[h] = px / entry - 1
        else:
            out[h] = None
    return out


def collect_breakout_family(data, rs_cache):
    t0 = time.time()
    rows = []  # dict per hit: market, tab, base_len_weeks, vertical, ret20/40/60
    specs = [
        ("돌파", analyze_breakout, BREAKOUT_CONFIG),
        ("박스돌파", analyze_boxbreak, BOXBREAK_CONFIG),
        ("추세전환", analyze_turnaround, TURN_CONFIG),
    ]
    for oi, off in enumerate(OFFSETS):
        rs_ranks, rs_moms = rs_cache[off]
        for t, df in data.items():
            ikr = is_kr(t)
            for label, fn, cfg in specs:
                if len(df) - off < cfg["min_bars"]:
                    continue
                hist = harness.truncate_at(df, off)
                try:
                    hit = fn(hist, rs_rank=rs_ranks.get(t), rs_mom=rs_moms.get(t), cfg=cfg, is_kr=ikr)
                except Exception:
                    continue
                if hit is None or not harness.passes_liquidity_filter(hit, ikr):
                    continue
                base_len, vertical = pre_breakout_structure(hist)
                rets = forward_returns(hit["close"], df, off)
                rows.append({
                    "ticker": t, "is_kr": ikr, "tab": label, "off": off,
                    "base_len_weeks": base_len, "vertical_runup": vertical,
                    **{f"ret_{h}d": rets[h] for h in PERSIST_HORIZONS},
                })
        print(f"[collect] offset {off} 완료 ({oi+1}/{len(OFFSETS)}) n={len(rows)} "
              f"elapsed={time.time()-t0:.0f}s", flush=True)
    return rows


def report_section_3(rows):
    out = {}
    for ikr, label in ((True, "kr"), (False, "us")):
        sub = [r for r in rows if r["is_kr"] == ikr]
        n = len(sub)
        no_base = sum(1 for r in sub if r["base_len_weeks"] == 0)
        vertical_n = sum(1 for r in sub if r["vertical_runup"])
        out[label] = {
            "n_hits": n,
            "no_base_frac": (no_base / n) if n else None,
            "no_base_n": no_base,
            "vertical_runup_frac": (vertical_n / n) if n else None,
            "base_len_weeks_median_iqr": median_iqr([r["base_len_weeks"] for r in sub if r["base_len_weeks"] > 0]),
        }
    return out


def report_section_4(rows):
    out = {}
    for ikr, label in ((True, "kr"), (False, "us")):
        sub = [r for r in rows if r["is_kr"] == ikr]
        out[label] = {}
        for h in PERSIST_HORIZONS:
            vals = [r[f"ret_{h}d"] for r in sub if r[f"ret_{h}d"] is not None]
            out[label][h] = {"n": len(vals), "median_iqr": median_iqr(vals)}
    return out


if __name__ == "__main__":
    _t0 = time.time()
    data, kr_u, us_u = harness.fetch_universe_data()
    bench = harness.fetch_kr_benchmarks()
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None

    print("\n" + "=" * 70)
    print("1/2절: 베이스 형성 빈도 + 품질 (최근 1년 주봉)")
    print("=" * 70)
    stockweek_rows, run_rows = section_1_2(data)
    s1 = report_section_1(stockweek_rows)
    s2 = report_section_2(run_rows)
    print("\n[1절 결과]")
    for k, v in s1.items():
        print(f"  {k}: KR={v['kr']} US={v['us']}")
    print("\n[2절 결과]")
    for k, v in s2.items():
        print(f"  {k}: {v}")

    print("\n" + "=" * 70)
    print("3/4절: 돌파계열 히트 직전구조 + 돌파후 지속성")
    print("=" * 70)
    rs_cache = precompute_rs(data, kospi_close, kosdaq_close)
    hit_rows = collect_breakout_family(data, rs_cache)
    s3 = report_section_3(hit_rows)
    s4 = report_section_4(hit_rows)
    print("\n[3절 결과]")
    for k, v in s3.items():
        print(f"  {k}: {v}")
    print("\n[4절 결과]")
    for k, v in s4.items():
        print(f"  {k}: {v}")

    print(f"\n[main] 전체 완료, elapsed={time.time()-_t0:.0f}s", flush=True)

    import json
    with open("/tmp/kr_us_market_structure_result.json", "w") as f:
        json.dump({"s1": s1, "s2": s2, "s3": s3, "s4": s4}, f, default=str, indent=2)
    print("[main] 결과 JSON: /tmp/kr_us_market_structure_result.json (커밋 대상 아님, 참고용)")
