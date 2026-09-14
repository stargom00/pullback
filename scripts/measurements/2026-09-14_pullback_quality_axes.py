"""측정 B — 눌림목 score 구성 항목별 판별력.
사전등록: docs/pullback_quality_axes.md (k=17, Bonferroni z >= 2.974).

항목 17개를 **각각 독립**으로만 본다(조합·총점 재합산 금지).
  연속값  → 체크포인트별 상위 30% vs 하위 30% (중간 40% 제외)
  불리언  → True vs False
  범주    → 최상 vs 최하 등급만

판정(항목별): |중앙값 차| >= 1.0%p & |MWU z| >= 2.974(보정) & 시기반분 같은 부호
             (각 군 n>=30) & 각 군 n >= 100. 문턱은 여기서 바꾸지 않는다.
"""
import inspect
import json
import math
import os
import pickle
import sys
import time
from collections import defaultdict

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import harness
import naver_kr
import app
from scanner import analyze, CONFIG

_m0911 = __import__("2026-09-11_imminent_score_rank_vs_return")
fetch_mcap_now = _m0911.fetch_mcap_now

OFFSETS = harness.checkpoints(60, 950, 10)
HALF = len(OFFSETS) // 2
RECENT, OLDER = set(OFFSETS[:HALF]), set(OFFSETS[HALF:])
PROD_WINDOW_DAYS = inspect.signature(naver_kr.fetch_history).parameters["days"].default
MCAP_MIN_EOK = app._MCAP_MIN_EOK
MIN_BARS = CONFIG["min_bars"]
T_PLUS = 20

TOP_PCT = 0.30          # 사전등록 1.3b — 임의값
QA_TOP_PCT = 0.20       # (c) 축만 기존 정의 유지(상위 20%)
MIN_HITS_FOR_SPLIT = 5  # 임의값
RANGE10_LO, RANGE10_HI = 7.0, 12.0
VOL50_CUT = 80.0

MEDIAN_GAP_MIN, HALF_N_MIN, GROUP_N_MIN = 1.0, 30, 100
Z_RAW = 1.96

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "2026-09-14_pullback_quality_axes.results.json")


def bonferroni_z(k: int) -> float:
    """양측 5%를 k개로 나눈 문턱. scipy 없이 이분법으로 Φ⁻¹."""
    p = 1 - 0.025 / k
    lo, hi = 0.0, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if 0.5 * (1 + math.erf(mid / math.sqrt(2))) < p:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 3)


# ══════════════════════════════════════════════════════════════════════
# 항목 정의 — (키, 타입, 그룹, 비고)
#   cont : 체크포인트별 상위/하위 분위
#   bool : True / False
#   cat  : (최상, 최하) 값 고정
#   abs  : 절대 경계(기존 (a)(b) 축 — 사전등록 1.3b 예외)
# ══════════════════════════════════════════════════════════════════════
ITEMS = [
    # A군 — score에 실제로 들어가고 히트 필드로 노출 (7)
    ("pullback_pct",   "cont", "A", "20×ideal(7.5% 최적)"),
    ("vol_ratio",      "cont", "A", "20×(1.1−x)/0.5"),
    ("rsi",            "cont", "A", "15×(45 중심)"),
    ("rs",             "cont", "A", "15×가산 + 곱셈계수 / 게이트 80+ 위"),
    ("rs_mom",         "cont", "A", "+3 (≥10)"),
    ("tightening",     "bool", "A", "score_adj +2 (vcp)"),
    ("vol_dry",        "bool", "A", "score_adj +2"),
    # B군 — 카드엔 있으나 score 미포함 (8)
    ("qa_score",       "qa",   "B", "기존 (c) 축 — 상위 20% 유지"),
    ("rs_3m",          "cont", "B", ""),
    ("rs_delta",       "cont", "B", "게이트 무관 — 온전한 축"),
    ("atr_pct",        "cont", "B", ""),
    ("ext200_pct",     "cont", "B", ""),
    ("late_level",     "cat",  "B", "none(최상) vs caution(최하) — danger는 게이트 제외"),
    ("grade",          "cat",  "B", "A(최상) vs D(최하)"),
    ("tt_pass",        "bool", "B", ""),
    # C군 — 기존 (a)(b) 축 (2)
    ("range10",        "abs",  "C", "<7% vs 12%+ (중간 40%는 서술)"),
    ("vol_rel",        "abs",  "C", "<80% vs 80%+"),
]
CAT_LEVELS = {"late_level": ("none", "caution"), "grade": ("A", "D")}
K = len(ITEMS)
Z_BONF = bonferroni_z(K)


def load_data():
    cache = os.environ.get("MEAS_CACHE")
    if cache and os.path.exists(cache):
        print(f"[cache] load {cache}", flush=True)
        return pickle.load(open(cache, "rb"))
    data, kr_u, us_u = harness.fetch_universe_data(
        markets=("kr", "us"), kr_days=1900, us_period="5y", validate_offsets=OFFSETS)
    blob = {"data": data, "kr_u": dict(kr_u), "us_u": dict(us_u),
            "bench": harness.fetch_kr_benchmarks(days=1900), "mcap": fetch_mcap_now()}
    if cache:
        pickle.dump(blob, open(cache, "wb"))
    return blob


def market_calendar(data, tickers):
    from collections import Counter
    cnt, n = Counter(), 0
    for t in tickers:
        df = data.get(t)
        if df is None or len(df) < MIN_BARS:
            continue
        n += 1
        cnt.update(df.index)
    if not n:
        return pd.DatetimeIndex([])
    return pd.DatetimeIndex(sorted(d for d, c in cnt.items() if c >= 0.5 * n))


def scan_at(blob, cp, tickers, cal, is_kr, bench_kr, stats):
    data = blob["data"]
    lo_date = cp - pd.Timedelta(days=PROD_WINDOW_DAYS)
    pos = cal.get_loc(cp)
    t20 = cal[pos + T_PLUS] if pos + T_PLUS < len(cal) else None
    assert t20 is None or t20 > cp

    cache_all, cache_m = {}, {}
    for t in tickers:
        df = data.get(t)
        if df is None:
            continue
        tr = df.loc[(df.index >= lo_date) & (df.index <= cp)]
        if tr.empty or tr.index[-1] != cp:
            continue
        cl = harness.clean_at_checkpoint(tr)
        if cl is None or cl.empty or cl.index[-1] != cp or len(cl) < MIN_BARS:
            continue
        assert cl.index.max() == cp, f"lookahead {t}"
        cache_all[t] = cl
        if is_kr:
            m = blob["mcap"].get(t)
            if m is None:
                stats["mcap_unknown"] += 1
            else:
                if m[0] * float(cl["Close"].iloc[-1]) / m[1] < MCAP_MIN_EOK:
                    stats["mcap_dropped"] += 1
                    continue
        cache_m[t] = cl

    bk = bench_kr["kospi"] if is_kr else 0.0
    bq = bench_kr["kosdaq"] if is_kr else 0.0
    rs_m, mom_m = harness.compute_rs_at_checkpoint(cache_m, bk, bq)
    rs_a, mom_a = harness.compute_rs_at_checkpoint(cache_all, bk, bq)

    n_nofilter = 0
    for t, h in cache_all.items():
        r = analyze(h, rs_rank=rs_a.get(t), rs_mom=mom_a.get(t), cfg=CONFIG, is_kr=is_kr)
        if r and not r.get("price_frozen") and harness.passes_liquidity_filter(r, is_kr):
            n_nofilter += 1

    hits = []
    for t, h in cache_m.items():
        r = analyze(h, rs_rank=rs_m.get(t), rs_mom=mom_m.get(t), cfg=CONFIG, is_kr=is_kr)
        if r is None or r.get("price_frozen") or not harness.passes_liquidity_filter(r, is_kr):
            continue
        raw = data[t]
        c0 = float(raw.at[cp, "Close"])
        if t20 is None or t20 not in raw.index or c0 <= 0:
            stats["no_future"] += 1
            continue
        rec = {"ticker": t, "off": None,
               "ret20": (float(raw.at[t20, "Close"]) / c0 - 1) * 100}
        # 히트 필드를 **그대로** 옮긴다(재계산 금지)
        for key, kind, _grp, _note in ITEMS:
            if key in ("range10", "vol_rel"):
                continue
            rec[key] = r.get(key)
        # C군만 파생 계산 — 사전등록 1.2b 정의
        high, low, vol = h["High"], h["Low"], h["Volume"]
        lo10 = float(low.iloc[-10:].min())
        rec["range10"] = ((float(high.iloc[-10:].max()) - lo10) / lo10 * 100) if lo10 > 0 else None
        v50 = float(vol.iloc[-50:].mean())
        rec["vol_rel"] = (float(vol.iloc[-1]) / v50 * 100) if v50 > 0 else None
        hits.append(rec)
    return hits, n_nofilter


def assign_splits(hits_at_cp, stats):
    """이 체크포인트 히트에 항목별 그룹(top/bot/None)을 매긴다.

    **항목마다 독립으로 매긴다** — 한 항목의 분할이 다른 항목의 분할에
    영향을 주지 않는다(사보타주 테스트가 이걸 확인한다).
    """
    for key, kind, _g, _n in ITEMS:
        grp_key = f"_g_{key}"
        if kind in ("cont", "qa"):
            valid = [h for h in hits_at_cp if isinstance(h.get(key), (int, float))]
            stats[f"none_{key}"] += len(hits_at_cp) - len(valid)
            if len(valid) < MIN_HITS_FOR_SPLIT:
                stats[f"cp_skip_{key}"] += 1
                continue
            pct = QA_TOP_PCT if kind == "qa" else TOP_PCT
            k = max(1, int(len(valid) * pct))
            order = sorted(valid, key=lambda x: -x[key])
            top = {id(x) for x in order[:k]}
            bot = ({id(x) for x in order[-k:]} if kind == "cont"
                   else {id(x) for x in order[k:]})   # qa는 "나머지" 전부가 하위군
            for h in valid:
                h[grp_key] = ("top" if id(h) in top
                              else ("bot" if id(h) in bot else None))
        elif kind == "bool":
            for h in hits_at_cp:
                v = h.get(key)
                h[grp_key] = ("top" if v is True else ("bot" if v is False else None))
        elif kind == "cat":
            best, worst = CAT_LEVELS[key]
            for h in hits_at_cp:
                v = h.get(key)
                h[grp_key] = ("top" if v == best else ("bot" if v == worst else None))
        elif kind == "abs":
            for h in hits_at_cp:
                v = h.get(key)
                if v is None:
                    h[grp_key] = None
                elif key == "range10":
                    h[grp_key] = ("bot" if v < RANGE10_LO
                                  else ("top" if v >= RANGE10_HI else None))
                else:
                    h[grp_key] = ("bot" if v < VOL50_CUT else "top")


def compare(bot_rows, top_rows):
    """bot=기준군, top=비교군. z>0이면 top이 크다."""
    a = pd.Series([r["ret20"] for r in bot_rows], dtype=float).dropna()
    b = pd.Series([r["ret20"] for r in top_rows], dtype=float).dropna()
    if a.empty or b.empty:
        return {"median_gap": None, "mwu_z": None}
    z, _ = harness.mannwhitney_zscore(a, b)
    return {"median_gap": round(float(b.median() - a.median()), 3),
            "mwu_z": round(float(z), 3) if z is not None else None,
            "median_bot": round(float(a.median()), 3),
            "median_top": round(float(b.median()), 3)}


def judge(key, note, group, all_hits):
    g = f"_g_{key}"
    top = [h for h in all_hits if h.get(g) == "top"]
    bot = [h for h in all_hits if h.get(g) == "bot"]
    pooled = compare(bot, top)
    halves = {}
    for label, offs in (("recent", RECENT), ("older", OLDER)):
        t = [h for h in top if h["off"] in offs]
        b = [h for h in bot if h["off"] in offs]
        halves[label] = {**compare(b, t), "n_top": len(t), "n_bot": len(b)}
    signs = [halves[x]["median_gap"] for x in ("recent", "older")]
    same_sign = all(v is not None for v in signs) and (signs[0] > 0) == (signs[1] > 0)
    half_n_ok = all(halves[x]["n_top"] >= HALF_N_MIN and halves[x]["n_bot"] >= HALF_N_MIN
                    for x in halves)
    z = abs(pooled["mwu_z"] or 0)
    checks = {
        "effect_size": abs(pooled["median_gap"] or 0) >= MEDIAN_GAP_MIN,
        "significance_bonf": z >= Z_BONF,
        "half_same_sign": bool(same_sign),
        "half_n": bool(half_n_ok),
        "group_n": len(top) >= GROUP_N_MIN and len(bot) >= GROUP_N_MIN,
    }
    return {"item": key, "group": group, "note": note,
            "n_top": len(top), "n_bot": len(bot), **pooled,
            "sig_raw_1_96": z >= Z_RAW, "sig_bonf": z >= Z_BONF,
            "halves": halves, "checks": checks, "passed": all(checks.values())}


def main():
    t0 = time.time()
    blob = load_data()
    data = blob["data"]
    kr_t = [t for t in blob["kr_u"] if t in data]
    us_t = [t for t in blob["us_u"] if t in data]
    cal_kr, cal_us = market_calendar(data, kr_t), market_calendar(data, us_t)
    stamp = harness.run_stamp(data)
    print(f"[data] kr={len(kr_t)} us={len(us_t)} last_kr={cal_kr[-1].date()} "
          f"last_us={cal_us[-1].date()}", flush=True)
    print(f"[stamp] {stamp['run_at_kst']} (local {stamp['run_at_local']})", flush=True)
    print(f"[bonf] k={K} -> z >= {Z_BONF}", flush=True)

    stats = defaultdict(int)
    all_hits, gate_n = [], 0
    for i, off in enumerate(OFFSETS):
        for mkt, tickers, cal in (("kr", kr_t, cal_kr), ("us", us_t, cal_us)):
            if off >= len(cal):
                continue
            cp = cal[-1 - off]
            bench = ({k: harness.bench_score_at_date(blob["bench"][k]["Close"], cp)
                      for k in ("kospi", "kosdaq")} if mkt == "kr" else None)
            hits, n_nf = scan_at(blob, cp, tickers, cal, mkt == "kr", bench, stats)
            gate_n += n_nf
            assign_splits(hits, stats)
            for h in hits:
                h["off"] = off
            all_hits.extend(hits)
        if (i + 1) % 20 == 0:
            print(f"[cp] {i+1}/{len(OFFSETS)} hits={len(all_hits)} "
                  f"elapsed={time.time()-t0:.0f}s", flush=True)

    diff = gate_n / 10199 - 1
    gate = {"ref": 10199, "observed_nofilter": gate_n, "diff_pct": round(diff * 100, 2),
            "passed": abs(diff) <= 0.05, "hits_with_mcap_filter": len(all_hits)}
    print(f"[gate] 눌림목 무필터 {gate_n} vs 10199 ({diff*100:+.2f}%) -> "
          f"{'PASS' if gate['passed'] else 'FAIL'}", flush=True)
    if not gate["passed"]:
        json.dump({"gate": gate, "meta": {"run_stamp": stamp}}, open(OUT, "w"),
                  ensure_ascii=False, indent=2, default=str)
        print("[stop] 재현 게이트 실패 — 판정하지 않고 종료", flush=True)
        return

    rows = [judge(k_, note, grp, all_hits) for k_, _kind, grp, note in ITEMS]
    mid = [h for h in all_hits if h.get("range10") is not None
           and RANGE10_LO <= h["range10"] < RANGE10_HI]
    mid_med = (round(float(pd.Series([h["ret20"] for h in mid]).median()), 3)
               if mid else None)

    print(f"\n{'항목':16s} {'군':2s} {'n_top':>6s} {'n_bot':>6s} {'중앙값차':>9s} "
          f"{'MWU z':>7s} {'1.96':>5s} {'보정':>5s} {'반분':>5s} {'판정':>5s}")
    for r in rows:
        print(f"{r['item']:16s} {r['group']:2s} {r['n_top']:6d} {r['n_bot']:6d} "
              f"{(r['median_gap'] if r['median_gap'] is not None else float('nan')):9.3f} "
              f"{(r['mwu_z'] if r['mwu_z'] is not None else float('nan')):7.3f} "
              f"{'O' if r['sig_raw_1_96'] else '-':>5s} "
              f"{'O' if r['sig_bonf'] else '-':>5s} "
              f"{'O' if r['checks']['half_same_sign'] else '-':>5s} "
              f"{'PASS' if r['passed'] else '-':>5s}", flush=True)

    passed = [r["item"] for r in rows if r["passed"]]
    raw_only = [r["item"] for r in rows if r["sig_raw_1_96"] and not r["sig_bonf"]]
    result = {"meta": {"prereg": "docs/pullback_quality_axes.md", "k": K,
                       "z_bonferroni": Z_BONF, "z_raw": Z_RAW, "run_stamp": stamp,
                       "cuts": {"top_pct": TOP_PCT, "qa_top_pct": QA_TOP_PCT,
                                "range10": [RANGE10_LO, RANGE10_HI], "vol50": VOL50_CUT},
                       "excluded_score_items": ["near_ma", "recent_high_ok",
                                                 "base_length_wk", "base_depth_pct"]},
              "gate": gate, "n_hits": len(all_hits), "items": rows,
              "range10_middle_narrative": {"n": len(mid), "median": mid_med},
              "diag": dict(stats),
              "verdict": {"passed_items": passed, "n_passed": len(passed),
                          "sig_raw_only_items": raw_only}}
    json.dump(result, open(OUT, "w"), ensure_ascii=False, indent=2, default=str)
    print(f"\n통과 항목: {passed or '없음'}")
    print(f"보정 전에만 유의: {raw_only or '없음'}")
    print(f"[done] {time.time()-t0:.0f}s -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
