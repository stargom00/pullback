"""바닥 다지기(Stage 1→2 전환) 셋업의 20거래일 수익률.

사전등록: docs/stage1to2_base_setup.md (커밋 07c4825로 고정, 실행 전).
이 스크립트는 그 문서의 §1을 그대로 구현한다 — 조건·문턱·판정식을 여기서
바꾸지 않는다.

측정 대상(T): 사전등록 1.2의 6조건 AND. 숫자는 HIMX 1건에서 눈으로 잡은
임의값(문서 1.2 출처 고지).
대조군: C1=눌림목 scanner.analyze() 히트(판정 기준선), C2=유니버스 전체(서술용).
판정: 중앙값 차이 >= +1.0%p AND Mann-Whitney U z >= 1.96 AND 시기반분 둘 다
      중앙값 차이 >= +1.0%p AND 각 시장 T n >= 100. (전부 KR+US 통합, n만 시장별)

자기검증 게이트(R0 대체, 사전등록 3-1): 눌림목 히트 수를 직전 90cp 측정과
±5% 대조. ⚠️ 비교 대상인 2026-09-12 stall_exit R0의 눌림목 10,199건은
**KR 시총 필터가 없는** 수치라, 같은 조건끼리 비교하려고 이 스크립트는
눌림목을 시총 무필터로도 한 번 더 스캔해 그 수치로 게이트를 건다
(측정 본체 C1은 시총 필터 적용분을 쓴다).

실행: 주말(양 시장 휴장). run_stamp에 KST/NZST 둘 다 기록.
"""
import inspect
import json
import os
import pickle
import sys
import time

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import harness
import naver_kr
import app
from scanner import (analyze, analyze_turnaround, atr, volume_info,
                     CONFIG, TURN_CONFIG)

# 09-11 측정에서 만든 부품 재사용(재구현 금지) — 시총 추정 필터
_m0911 = __import__("2026-09-11_imminent_score_rank_vs_return")
fetch_mcap_now = _m0911.fetch_mcap_now

OFFSETS = harness.checkpoints(60, 950, 10)      # 90개 — 규칙9 표준
HALF = len(OFFSETS) // 2
RECENT_OFFSETS = set(OFFSETS[:HALF])            # off 60~500 (최근 반기)
OLDER_OFFSETS = set(OFFSETS[HALF:])             # off 510~950 (이전 반기)

PROD_WINDOW_DAYS = inspect.signature(naver_kr.fetch_history).parameters["days"].default
MCAP_MIN_EOK = app._MCAP_MIN_EOK
MIN_BARS = 210                                   # 전 탭 공통(200MA·52주)

T_PLUS = 20                                      # 사전등록 1.4 — 20거래일

# ── 사전등록 1.2의 6조건 (임의값, 격자탐색 금지) ──────────────────────
MA200_BAND = 0.10          # 200MA 대비 ±10%
OFF_HIGH_MIN = 0.30        # 52주 고점 대비 -30% 이하
RANGE20_MAX = 0.15         # 최근 20봉 고저 범위 <= 15%
ATR_CONTRACT_MAX = 0.80    # ATR14(t)/ATR14(t-20) <= 0.8
RS_LO, RS_HI = 40, 80      # 40 <= rs < 80 (80은 눌림목 영역이라 제외)

# ── 사전등록 1.6 판정 문턱 ─────────────────────────────────────────────
MEDIAN_GAP_MIN = 1.0       # %p
Z_MIN = 1.96
N_MIN_PER_MARKET = 100

# ── 사전등록 1.4 자기검증 게이트 ───────────────────────────────────────
GATE_REF_PULLBACK_HITS = 10199   # 2026-09-12 stall_exit R0 (시총 무필터)
GATE_REF_SOURCE = "2026-09-12_stall_exit_bench_revalidation.results.json R0 눌림목 n_hits_total"
GATE_TOL = 0.05

# ── 사전등록 3-4 후속 분기 ─────────────────────────────────────────────
OVERLAP_SPLIT = 0.50       # 임의값(문서 3-4에 명시)

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "2026-09-13_stage1to2_setup.results.json")


# ══════════════════════════════════════════════════════════════════════
# 데이터
# ══════════════════════════════════════════════════════════════════════
def load_data():
    cache = os.environ.get("MEAS_CACHE")
    if cache and os.path.exists(cache):
        print(f"[cache] load {cache}", flush=True)
        return pickle.load(open(cache, "rb"))
    data, kr_u, us_u = harness.fetch_universe_data(
        markets=("kr", "us"), kr_days=1900, us_period="5y", validate_offsets=OFFSETS)
    bench = harness.fetch_kr_benchmarks(days=1900)
    mcap = fetch_mcap_now()
    blob = {"data": data, "kr_u": dict(kr_u), "us_u": dict(us_u), "bench": bench,
            "mcap": mcap, "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S %Z")}
    if cache:
        pickle.dump(blob, open(cache, "wb"))
    return blob


def market_calendar(data, tickers):
    """그 시장의 거래일 달력 — 봉이 가장 많은 종목의 인덱스를 기준으로,
    전 종목 날짜의 합집합 중 실제로 다수가 거래한 날만 취한다."""
    from collections import Counter
    cnt = Counter()
    n = 0
    for t in tickers:
        df = data.get(t)
        if df is None or len(df) < MIN_BARS:
            continue
        n += 1
        cnt.update(df.index)
    if not n:
        return pd.DatetimeIndex([])
    keep = [d for d, c in cnt.items() if c >= 0.5 * n]
    return pd.DatetimeIndex(sorted(keep))


def bench_scores(bench, cp):
    out = {}
    for k in ("kospi", "kosdaq"):
        s = bench[k]["Close"].dropna().loc[:cp]
        assert len(s) and s.index[-1] == cp, f"bench {k} last {s.index[-1] if len(s) else None} != {cp}"
        out[k] = harness.bench_score_at_date(bench[k]["Close"], cp)
    return out


# ══════════════════════════════════════════════════════════════════════
# 사전등록 1.2 조건 판정
# ══════════════════════════════════════════════════════════════════════
def stage1to2_conditions(h: pd.DataFrame, rs_rank):
    """체크포인트까지 잘린·정제된 df 하나에 6조건을 적용. 반환: (통과여부, 조건별 dict).

    조건 정의는 사전등록 1.2 표 그대로. 숫자는 임의값이라 여기서 조정 금지.
    """
    close = h["Close"]
    high, low = h["High"], h["Low"]
    c = float(close.iloc[-1])
    res = {}

    ma200 = float(close.iloc[-200:].mean())
    res["c1_ma200_band"] = (ma200 > 0 and abs(c / ma200 - 1) <= MA200_BAND)

    hi52 = float(high.iloc[-252:].max())
    res["c2_off_high"] = (hi52 > 0 and c <= hi52 * (1 - OFF_HIGH_MIN))

    hi20 = float(high.iloc[-20:].max())
    lo20 = float(low.iloc[-20:].min())
    res["c3_range20"] = (lo20 > 0 and (hi20 - lo20) / lo20 <= RANGE20_MAX)

    a_now = atr(high, low, close)
    a_then = atr(high.iloc[:-20], low.iloc[:-20], close.iloc[:-20])
    res["c4_atr_contract"] = (a_then > 0 and a_now / a_then <= ATR_CONTRACT_MAX)

    v20 = float(h["Volume"].iloc[-20:].mean())
    v60 = float(h["Volume"].iloc[-60:].mean())
    res["c5_vol_dry"] = (v60 > 0 and v20 <= v60)

    res["c6_rs_band"] = (rs_rank is not None and RS_LO <= rs_rank < RS_HI)

    return all(res.values()), res


# ══════════════════════════════════════════════════════════════════════
# 체크포인트 1회 스캔
# ══════════════════════════════════════════════════════════════════════
def scan_at(blob, cp, tickers, cal, is_kr, bench_kr, stats):
    """반환: dict(T히트, C1히트, C2수익률, TURN히트, 게이트용 무필터 C1수).

    시총 필터는 RS 계산 전에 건다(프로덕션 순서). 게이트용으로 무필터
    캐시도 같이 만들어 눌림목만 한 번 더 돌린다(사전등록 1.4).
    """
    data = blob["data"]
    lo_date = cp - pd.Timedelta(days=PROD_WINDOW_DAYS)
    pos = cal.get_loc(cp)
    t20 = cal[pos + T_PLUS] if pos + T_PLUS < len(cal) else None
    assert t20 is None or t20 > cp, f"future anchor {t20} <= {cp}"

    cache_all, cache_mcap = {}, {}
    for t in tickers:
        df = data.get(t)
        if df is None:
            stats["no_data"] += 1
            continue
        tr = df.loc[(df.index >= lo_date) & (df.index <= cp)]
        if tr.empty or tr.index[-1] != cp:
            stats["no_bar_on_cp"] += 1
            continue
        cl = harness.clean_at_checkpoint(tr)
        if cl is None or cl.empty or cl.index[-1] != cp or len(cl) < MIN_BARS:
            stats["too_short_or_invalid"] += 1
            continue
        # 룩어헤드 assert 1 — 입력이 체크포인트를 넘지 않는다
        assert cl.index.max() == cp, f"lookahead {t}: {cl.index.max()} > {cp}"
        cache_all[t] = cl
        if is_kr:
            m = blob["mcap"].get(t)
            if m is None:
                stats["mcap_unknown"] += 1        # fail-open (프로덕션과 같은 방향)
            else:
                est = m[0] * float(cl["Close"].iloc[-1]) / m[1]
                if est < MCAP_MIN_EOK:
                    stats["mcap_dropped"] += 1
                    continue
        cache_mcap[t] = cl

    bk = bench_kr["kospi"] if is_kr else 0.0
    bq = bench_kr["kosdaq"] if is_kr else 0.0
    rs_m, mom_m = harness.compute_rs_at_checkpoint(cache_mcap, bk, bq)
    rs_a, mom_a = harness.compute_rs_at_checkpoint(cache_all, bk, bq)

    def ret20(t, h):
        raw = data[t]
        c0 = float(raw.at[cp, "Close"])
        if t20 is None or t20 not in raw.index or c0 <= 0:
            return None
        return (float(raw.at[t20, "Close"]) / c0 - 1) * 100

    out = {"T": [], "C1": [], "C2": [], "TURN": set(), "gate_c1_nofilter": 0}

    # 게이트용: 시총 무필터 눌림목 (히트 수만 센다)
    for t, h in cache_all.items():
        r = analyze(h, rs_rank=rs_a.get(t), rs_mom=mom_a.get(t), cfg=CONFIG, is_kr=is_kr)
        if r is None or r.get("price_frozen") or not harness.passes_liquidity_filter(r, is_kr):
            continue
        out["gate_c1_nofilter"] += 1

    # 본 측정: 시총 필터 적용
    for t, h in cache_mcap.items():
        rr = ret20(t, h)
        vi = volume_info(float(h["Close"].iloc[-1]), h["Volume"])
        liquid = harness.passes_liquidity_filter({"avg_turnover": vi["avg_turnover"]}, is_kr)

        if liquid and rr is not None:
            out["C2"].append(rr)                       # 유니버스 기준선

        # C1 — 눌림목
        r = analyze(h, rs_rank=rs_m.get(t), rs_mom=mom_m.get(t), cfg=CONFIG, is_kr=is_kr)
        if r is not None and not r.get("price_frozen") and harness.passes_liquidity_filter(r, is_kr):
            # 룩어헤드 assert 2 — analyze가 cp 종가를 봤는가
            assert abs(r["close"] - round(float(h["Close"].iloc[-1]), 2)) < 1e-6, (t, r["close"])
            if rr is None:
                stats["c1_no_future"] += 1
            else:
                out["C1"].append({"ticker": t, "ret20": rr, "rs": rs_m.get(t)})

        # T — 바닥 다지기 6조건
        ok, detail = stage1to2_conditions(h, rs_m.get(t))
        for k, v in detail.items():
            stats["cond_pass"][k] += int(bool(v))
        if ok:
            if not liquid:
                stats["t_liq_dropped"] += 1
            elif rr is None:
                stats["t_no_future"] += 1
            else:
                out["T"].append({"ticker": t, "ret20": rr, "rs": rs_m.get(t)})

        # 추세전환 — 겹침률 계산에만 쓴다(대조군 아님)
        tr_ = analyze_turnaround(h, rs_rank=rs_m.get(t), rs_mom=mom_m.get(t),
                                 cfg=TURN_CONFIG, is_kr=is_kr)
        if tr_ is not None and not tr_.get("price_frozen") and harness.passes_liquidity_filter(tr_, is_kr):
            out["TURN"].add(t)

    if t20 is None:
        stats["cp_without_future"] += 1
    return out


# ══════════════════════════════════════════════════════════════════════
# 통계
# ══════════════════════════════════════════════════════════════════════
def describe(rets):
    s = pd.Series(rets, dtype=float).dropna()
    if s.empty:
        return {"n": 0}
    return {
        "n": int(len(s)),
        "median": round(float(s.median()), 3),
        "mean": round(float(s.mean()), 3),
        "win_rate": round(float((s > 0).mean()), 4),
        "up10_rate": round(float((s >= 10).mean()), 4),
        "dn10_rate": round(float((s <= -10).mean()), 4),
        "std": round(float(s.std(ddof=1)), 3) if len(s) > 1 else None,
    }


def compare(t_rets, c_rets):
    a = pd.Series(c_rets, dtype=float).dropna()   # 기준선
    b = pd.Series(t_rets, dtype=float).dropna()   # 대상
    if a.empty or b.empty:
        return {"median_gap": None, "mwu_z": None}
    mz, msig = harness.mannwhitney_zscore(a, b)
    wz, _ = harness.welch_zscore(a, b)
    return {
        "median_gap": round(float(b.median() - a.median()), 3),
        "mwu_z": round(float(mz), 3) if mz is not None else None,
        "mwu_sig": bool(msig),
        "mean_gap_ref_only": round(float(b.mean() - a.mean()), 3),
        "welch_z_ref_only": round(float(wz), 3) if wz is not None else None,
    }


# ══════════════════════════════════════════════════════════════════════
def main():
    t0 = time.time()
    blob = load_data()
    data = blob["data"]
    kr_t = [t for t in blob["kr_u"] if t in data]
    us_t = [t for t in blob["us_u"] if t in data]
    cal_kr = market_calendar(data, kr_t)
    cal_us = market_calendar(data, us_t)
    print(f"[data] kr={len(kr_t)} us={len(us_t)} cal_kr={len(cal_kr)} cal_us={len(cal_us)} "
          f"last_kr={cal_kr[-1].date()} last_us={cal_us[-1].date()}", flush=True)

    stamp = harness.run_stamp(data)
    print(f"[stamp] {stamp}", flush=True)

    from collections import defaultdict
    stats = defaultdict(int)
    stats["cond_pass"] = defaultdict(int)

    rows = {"T": [], "C1": [], "C2": []}          # (offset, market, ret20)
    overlap_hit, overlap_total = 0, 0
    gate_nofilter = 0

    for i, off in enumerate(OFFSETS):
        for market, tickers, cal in (("kr", kr_t, cal_kr), ("us", us_t, cal_us)):
            if off >= len(cal):
                stats["cp_out_of_calendar"] += 1
                continue
            cp = cal[-1 - off]
            bench_kr = bench_scores(blob["bench"], cp) if market == "kr" else None
            r = scan_at(blob, cp, tickers, cal, market == "kr", bench_kr, stats)
            gate_nofilter += r["gate_c1_nofilter"]
            for h in r["T"]:
                rows["T"].append((off, market, h["ret20"]))
                overlap_total += 1
                if h["ticker"] in r["TURN"]:
                    overlap_hit += 1
            for h in r["C1"]:
                rows["C1"].append((off, market, h["ret20"]))
            for v in r["C2"]:
                rows["C2"].append((off, market, v))
        if (i + 1) % 10 == 0:
            print(f"[cp] {i+1}/{len(OFFSETS)} off={off} T={len(rows['T'])} "
                  f"C1={len(rows['C1'])} elapsed={time.time()-t0:.0f}s", flush=True)

    # ── 자기검증 게이트 ────────────────────────────────────────────────
    diff = gate_nofilter / GATE_REF_PULLBACK_HITS - 1
    gate = {"ref": GATE_REF_PULLBACK_HITS, "ref_source": GATE_REF_SOURCE,
            "observed_nofilter": gate_nofilter, "diff_pct": round(diff * 100, 2),
            "tol_pct": GATE_TOL * 100, "passed": abs(diff) <= GATE_TOL,
            "c1_with_mcap_filter": len(rows["C1"])}
    print(f"[gate] 눌림목 무필터 {gate_nofilter} vs ref {GATE_REF_PULLBACK_HITS} "
          f"({diff*100:+.2f}%) -> {'PASS' if gate['passed'] else 'FAIL'}", flush=True)

    def sel(key, market=None, offs=None):
        return [v for off, m, v in rows[key]
                if (market is None or m == market) and (offs is None or off in offs)]

    result = {
        "meta": {
            "prereg": "docs/stage1to2_base_setup.md (commit 07c4825)",
            "offsets": OFFSETS, "t_plus": T_PLUS,
            "conditions": {"ma200_band": MA200_BAND, "off_high_min": OFF_HIGH_MIN,
                           "range20_max": RANGE20_MAX, "atr_contract_max": ATR_CONTRACT_MAX,
                           "rs_lo": RS_LO, "rs_hi": RS_HI},
            "thresholds": {"median_gap_min": MEDIAN_GAP_MIN, "z_min": Z_MIN,
                           "n_min_per_market": N_MIN_PER_MARKET},
            "run_stamp": stamp, "n_fetched": len(data),
            "last_bar": {"kr": str(cal_kr[-1].date()), "us": str(cal_us[-1].date())},
        },
        "gate": gate,
        "groups": {k: describe(sel(k)) for k in ("T", "C1", "C2")},
        "by_market": {m: {k: describe(sel(k, market=m)) for k in ("T", "C1", "C2")}
                      for m in ("kr", "us")},
        "verdict_inputs": {
            "pooled_vs_C1": compare(sel("T"), sel("C1")),
            "pooled_vs_C2_narrative": compare(sel("T"), sel("C2")),
            "half_recent": compare(sel("T", offs=RECENT_OFFSETS), sel("C1", offs=RECENT_OFFSETS)),
            "half_older": compare(sel("T", offs=OLDER_OFFSETS), sel("C1", offs=OLDER_OFFSETS)),
        },
        "overlap_with_turnaround": {
            "n_T": overlap_total, "n_overlap": overlap_hit,
            "rate": round(overlap_hit / overlap_total, 4) if overlap_total else None,
            "split_threshold_arbitrary": OVERLAP_SPLIT,
        },
        "diag": {k: (dict(v) if isinstance(v, defaultdict) else v) for k, v in stats.items()},
    }

    # ── 판정 (사전등록 1.6, 기계적) ────────────────────────────────────
    v = result["verdict_inputs"]
    n_kr = result["by_market"]["kr"]["T"]["n"]
    n_us = result["by_market"]["us"]["T"]["n"]
    checks = {
        "effect_size": (v["pooled_vs_C1"]["median_gap"] or -99) >= MEDIAN_GAP_MIN,
        "significance": (v["pooled_vs_C1"]["mwu_z"] or -99) >= Z_MIN,
        "half_recent": (v["half_recent"]["median_gap"] or -99) >= MEDIAN_GAP_MIN,
        "half_older": (v["half_older"]["median_gap"] or -99) >= MEDIAN_GAP_MIN,
        "n_kr": n_kr >= N_MIN_PER_MARKET,
        "n_us": n_us >= N_MIN_PER_MARKET,
    }
    result["verdict"] = {
        "checks": checks,
        "passed": all(checks.values()) and gate["passed"],
        "gate_passed": gate["passed"],
        "followup_if_passed": ("추세전환 조건 완화"
                               if (result["overlap_with_turnaround"]["rate"] or 0) >= OVERLAP_SPLIT
                               else "새 탭 신설"),
    }

    json.dump(result, open(OUT, "w"), ensure_ascii=False, indent=2, default=str)
    print(json.dumps({"gate": gate, "groups": result["groups"],
                      "verdict_inputs": v, "verdict": result["verdict"],
                      "overlap": result["overlap_with_turnaround"]},
                     ensure_ascii=False, indent=2), flush=True)
    print(f"[done] {time.time()-t0:.0f}s -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
