"""
표시 전용 근거 2건(8·9번) 재검증 — 벤치마크 룩어헤드 수정 (2026-09-12).

사전등록: docs/display_only_bench_revalidation.md §1 (커밋 43439c5 — 실행 전 고정).
재검증 대상(둘 다 2026-09-07, 원본 무수정):
  8번 ..._kr_us_breakout_boxbreak_post_pivot_consolidation_ev.py  → 0~0.5ATR 즉시진입 금지
  9번 ..._kr_us_confirm_entry_stop_width_atr_multiple_ev.py       → 손절폭 ATR 배수 표시

두 원본의 분석 함수(run_q2 / run_stop_width_analysis / summarize / bucket 정의)를
importlib로 그대로 가져다 쓴다 — 재구현 없음. 새로 쓰는 건 히트 수집뿐이고,
한 번의 fetch로 8·9번 × R0~R3를 전부 돌린다(두 원본의 수집 요구 필드가 달라
한 루프에서 둘 다 채운다).

  R0 원본 복제 / R1 벤치마크만 수정 / R2 +시총필터 / R3 참고(프로덕션 데이터 경로)

원본 실행일(2026-09-07)에 날짜·fetch 시작일·유니버스·미래 구간을 맞춘다.

실행: `python3 scripts/measurements/2026-09-12_display_only_bench_revalidation.py`
환경변수 MEAS_CACHE=경로 → fetch 캐시.
"""
import sys
import os
import json
import time
import pickle
import importlib.util
import multiprocessing as mp
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MEAS = os.path.join(ROOT, "scripts", "measurements")
sys.path.insert(0, ROOT)
sys.path.insert(0, MEAS)

import pandas as pd

import harness
import universe as universe_mod
from scanner import nonzero_vol_mean


def _load(fname, modname):
    spec = importlib.util.spec_from_file_location(modname, os.path.join(MEAS, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


P8 = _load("2026-09-07_kr_us_breakout_boxbreak_post_pivot_consolidation_ev.py", "orig_q8")
P9 = _load("2026-09-07_kr_us_confirm_entry_stop_width_atr_multiple_ev.py", "orig_q9")
PREV5 = _load("2026-09-11_confirm_entry_close_bench_revalidation.py", "prev5_entry_close")
PREV_IMM = _load("2026-09-11_imminent_score_rank_vs_return.py", "prev_imminent_rank")

ORIG_DATE = pd.Timestamp("2026-09-07")
ORIG_UNIV_FILE = "kr_universe_v6_1500_20260907_eod.json"
OFFSETS = P8.OFFSETS
NEED_BARS = max(P8.NEED_BARS, P9.NEED_BARS)
RUNS = ("R0", "R1", "R2", "R3")
DECISIVE = ("R1", "R2")
OUT_PATH = os.path.join(MEAS, "2026-09-12_display_only_bench_revalidation.results.json")
HUG = "0.0~0.5ATR"          # 8번 판정 대상 버킷 라벨(P8.run_q2 결과 키)
BASE9 = P9.BASELINE_BUCKET_LABEL   # 9번 상한검정 기준 버킷("1.0~1.5ATR")

# 원본(2026-09-07 발표) — 비교표 + R0 재현 게이트(사전등록 1.4)
ORIGINAL_8 = {   # (탭, 시장): (0~0.5ATR EV, n, z)
    ("박스돌파", "KR"): (-0.334, 302, -4.66), ("박스돌파", "US"): (-0.221, 267, -2.75),
    ("돌파", "KR"): (-0.365, 397, -5.94), ("돌파", "US"): (-0.215, 517, -3.71),
}
ORIGINAL_9_BASE_NV = {"KR": 261, "US": 310}   # 돌파임박 1.0~1.5ATR 기준 버킷
R0_GATE_TOL = 0.02
MIN_N_TREND = 30    # 사전등록 1.2(9번): 추세 서술용 최소 표본, AI 판단 임의값


def orig_kr_universe():
    dyn = json.load(open(os.path.join(ROOT, ORIG_UNIV_FILE)))
    wl = {t: n for t, n in universe_mod.load_watchlist().items() if t.endswith((".KS", ".KQ"))}
    return {**universe_mod.KR_UNIVERSE, **dyn, **wl}


def prepare():
    cache = os.environ.get("MEAS_CACHE")
    if cache and os.path.exists(cache):
        print(f"[cache] load {cache}", flush=True)
        return pickle.load(open(cache, "rb"))
    t0 = time.time()
    dd = (datetime.now().date() - ORIG_DATE.date()).days
    kr_u, us_u = orig_kr_universe(), universe_mod.get_universe("us")
    data = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        for t, df in ex.map(lambda t: harness._fetch_kr_one(t, 1900 + dd), list(kr_u)):
            if df is not None:
                data[t] = df
    print(f"[fetch] kr {len(data)}/{len(kr_u)} {time.time()-t0:.0f}s", flush=True)
    us_tickers = list(us_u)
    for i in range(0, len(us_tickers), 100):
        data.update(harness._fetch_us_batch(us_tickers[i:i + 100], period="5y"))
    blob = {"data": data, "kr_u": dict(kr_u), "us_u": dict(us_u), "dd": dd,
            "bench900": harness.fetch_kr_benchmarks(days=900 + dd),
            "bench1900": harness.fetch_kr_benchmarks(days=1900 + dd),
            "mcap": PREV_IMM.fetch_mcap_now(),
            "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S %Z")}
    print(f"[fetch] total {len(data)} tickers, {time.time()-t0:.0f}s", flush=True)
    if cache:
        pickle.dump(blob, open(cache, "wb"))
    return blob


BLOB = None


def _delta_us(data):
    from collections import Counter
    c = Counter()
    for t, df in data.items():
        if harness.is_kr_ticker(t) or len(df) < 300:
            continue
        c[len(df.index[df.index > ORIG_DATE])] += 1
        if sum(c.values()) >= 200:
            break
    assert c, "US 데이터 없음"
    delta, n = c.most_common(1)[0]
    assert n / sum(c.values()) > 0.8, f"US Δ 불일치 — {c.most_common(5)}"
    return delta


def collect(run):
    """8번(P8.TABS=돌파/박스돌파)과 9번(P9.TABS=5탭) 히트를 한 루프에서 채운다.
    각 원본의 필드 요구를 그대로 만족시킨다(8번: pivot/close/atr14/future,
    9번: signal_high/trailing50_vol/atr14/stop_val/future)."""
    data, mcap = BLOB["data"], BLOB["mcap"]
    k900 = BLOB["bench900"]["kospi"]["Close"].dropna()
    q900 = BLOB["bench900"]["kosdaq"]["Close"].dropna()
    k19 = BLOB["bench1900"]["kospi"]["Close"].dropna()
    q19 = BLOB["bench1900"]["kosdaq"]["Close"].dropna()
    cal = k19.index
    assert ORIG_DATE in cal
    d_kr = len(cal) - 1 - cal.get_loc(ORIG_DATE)
    d_us = _delta_us(data)
    win = pd.Timedelta(days=PREV_IMM.PROD_WINDOW_DAYS)
    hits8 = {name: [] for name in P8.TABS}
    hits9 = {name: [] for name in P9.TABS}
    diag = {"delta_kr": int(d_kr), "delta_us": int(d_us), "mcap_dropped": 0,
            "mcap_unknown": 0, "no_bar_on_cp": 0}
    t0 = time.time()
    for oi, off in enumerate(OFFSETS):
        cp = cal[-(off + d_kr + 1)]
        if run == "R0":
            b_k = PREV5._legacy_bench_score_at_CONTAMINATED(k900, off + d_kr)
            b_q = PREV5._legacy_bench_score_at_CONTAMINATED(q900, off + d_kr)
        else:
            b_k = harness.bench_score_at_date(k19, cp)
            b_q = harness.bench_score_at_date(q19, cp)

        trunc_cache, cp_eff = {}, {}
        for t, df in data.items():
            ikr = harness.is_kr_ticker(t)
            offp = off + (d_kr if ikr else d_us)
            if run == "R3":
                if ikr:
                    cp_t = cp
                else:
                    if len(df) - offp < 1:
                        continue
                    cp_t = df.index[len(df) - offp - 1]
                tr = df.loc[(df.index >= cp_t - win) & (df.index <= cp_t)]
                if tr.empty or (ikr and tr.index[-1] != cp):
                    diag["no_bar_on_cp"] += 1
                    continue
                tr = harness.clean_at_checkpoint(tr)
                if tr.empty:
                    diag["no_bar_on_cp"] += 1
                    continue
                cp_eff[t] = cp_t
            else:
                if len(df) - offp < NEED_BARS:
                    continue
                tr = harness.truncate_at(df, offp)
            if run in ("R2", "R3") and ikr:
                m = mcap.get(t)
                if m is None:
                    diag["mcap_unknown"] += 1
                elif m[0] * float(tr["Close"].iloc[-1]) / m[1] < PREV_IMM.MCAP_MIN_EOK:
                    diag["mcap_dropped"] += 1
                    continue
            trunc_cache[t] = tr
        rs_ranks, rs_moms = harness.compute_rs_at_checkpoint(trunc_cache, b_k, b_q)

        for t, hist in trunc_cache.items():
            ikr = harness.is_kr_ticker(t)
            offp = off + (d_kr if ikr else d_us)
            if run == "R3":
                fut = data[t].loc[data[t].index > cp_eff[t]]
            else:
                fut = harness.future_after(data[t], offp)
            fut = fut.loc[fut.index <= ORIG_DATE]
            if len(fut):
                assert fut.index[0] > hist.index[-1], (t, fut.index[0], hist.index[-1])
            rr, rm = rs_ranks.get(t), rs_moms.get(t)
            atr14 = P8.calc_atr(hist["High"], hist["Low"], hist["Close"], 14)
            signal_high = float(hist["High"].iloc[-1])
            signal_low = float(hist["Low"].iloc[-1])
            trailing50_vol = float(nonzero_vol_mean(hist["Volume"].iloc[-50:]))
            for name, spec in P9.TABS.items():   # 5탭 = 8번 2탭의 상위집합
                try:
                    r = spec["fn"](hist, rs_rank=rr, rs_mom=rm, cfg=spec["cfg"], is_kr=ikr)
                except Exception:
                    r = None
                if r is None or not harness.passes_liquidity_filter(r, ikr):
                    continue
                # 9번 히트
                stop_val = signal_low if spec["stop_key"] == "signal_low" else r.get(spec["stop_key"])
                if stop_val is not None:
                    hits9[name].append({
                        "ticker": t, "off": off, "is_kr": ikr,
                        "signal_high": signal_high, "trailing50_vol": trailing50_vol,
                        "atr14": float(atr14) if atr14 else None,
                        "stop_val": float(stop_val), "future": fut,
                    })
                # 8번 히트(돌파/박스돌파만, 원본 가드 그대로)
                if name in P8.TABS:
                    pivot, close = r.get("pivot"), r.get("close")
                    if pivot is None or close is None or pivot <= 0 or close <= pivot:
                        continue
                    hits8[name].append({
                        "ticker": t, "off": off, "is_kr": ikr,
                        "close": float(close), "pivot": float(pivot),
                        "atr14": float(atr14) if atr14 else None, "future": fut,
                    })
        if (oi + 1) % 20 == 0:
            print(f"[{run}] cp {oi+1}/90 off={off} {cp.date()} "
                  f"8={ {k: len(v) for k, v in hits8.items()} } 9n={sum(len(v) for v in hits9.values())} "
                  f"{time.time()-t0:.0f}s", flush=True)
    return hits8, hits9, diag


def do_run(run):
    t0 = time.time()
    hits8, hits9, diag = collect(run)
    if run != "R0":
        assert PREV5.LEGACY_CALLS == 0, f"{run}이 오염 재현 사본을 호출함"
    else:
        diag["legacy_fallback"] = dict(PREV5.LEGACY_FALLBACK)

    # ── 8번: 탭×시장 ATR거리 버킷(원본 run_q2 그대로) ──
    out8 = {}
    for name in P8.TABS:
        for mkt in ("KR", "US"):
            sub = [h for h in hits8[name] if h["is_kr"] == (mkt == "KR")]
            out8[f"{name}|{mkt}"] = {"n_hits": len(sub), "buckets": P8.run_q2(sub) if sub else {}}
    # ── 9번: 탭×시장 손절폭 버킷 + 상한검정(원본 run_stop_width_analysis 그대로) ──
    out9 = {}
    for name in P9.TABS:
        for mkt in ("KR", "US"):
            sub = [h for h in hits9[name] if h["is_kr"] == (mkt == "KR")]
            out9[f"{name}|{mkt}"] = P9.run_stop_width_analysis(sub) if sub else {}
    diag["elapsed_s"] = round(time.time() - t0)
    print(f"[{run}] done {diag['elapsed_s']}s", flush=True)
    return run, {"q8": out8, "q9": out9, "diag": diag}


def main():
    global BLOB
    t0 = time.time()
    BLOB = prepare()
    ctx = mp.get_context("fork")
    with ctx.Pool(len(RUNS)) as pool:
        results = dict(pool.map(do_run, RUNS))

    # ── R0 재현 게이트 ──
    gate = {}
    for (tab, mkt), (_ev, n_orig, _z) in ORIGINAL_8.items():
        b = (results["R0"]["q8"].get(f"{tab}|{mkt}") or {}).get("buckets", {}).get(HUG, {})
        got = b.get("nv")
        ok = got is not None and abs(got - n_orig) / n_orig <= R0_GATE_TOL
        gate[f"8:{tab}|{mkt}"] = {"original_n": n_orig, "r0_n": got,
                                   "diff_pct": None if got is None else round((got - n_orig) / n_orig * 100, 2),
                                   "pass": bool(ok)}
    for mkt, n_orig in ORIGINAL_9_BASE_NV.items():
        b = (results["R0"]["q9"].get(f"돌파임박|{mkt}") or {}).get(BASE9, {})
        got = b.get("nv")
        ok = got is not None and abs(got - n_orig) / n_orig <= R0_GATE_TOL
        gate[f"9:돌파임박|{mkt}"] = {"original_n": n_orig, "r0_n": got,
                                     "diff_pct": None if got is None else round((got - n_orig) / n_orig * 100, 2),
                                     "pass": bool(ok)}
    gate_pass = all(v["pass"] for v in gate.values())

    out = {"meta": {"prereg": "docs/display_only_bench_revalidation.md §1 (43439c5)",
                    "fetched_at": BLOB["fetched_at"], "dd": BLOB["dd"], "n_fetched": len(BLOB["data"])},
           "original_8": {f"{k[0]}|{k[1]}": v for k, v in ORIGINAL_8.items()},
           "original_9_baseline_nv": ORIGINAL_9_BASE_NV,
           "runs": results, "r0_gate": gate, "r0_gate_pass": gate_pass}

    if not gate_pass:
        out["verdict_8"] = out["verdict_9"] = "판정 없음 — R0 재현 게이트 실패"
    else:
        # 8번 셀별 갈래(사전등록 1.2): R1·R2 중 더 보수적인(아래) 갈래
        def branch(z):
            if z is None:
                return "판정불가"
            if z <= -1.96:
                return "(a) 유지"
            if z < 0:
                return "(b) 근거 상실 — 비유의"
            return "(c) 근거 상실 — 부호 역전"
        order = {"(a) 유지": 0, "(b) 근거 상실 — 비유의": 1, "(c) 근거 상실 — 부호 역전": 2, "판정불가": 3}
        cells = {}
        for (tab, mkt) in ORIGINAL_8:
            key = f"{tab}|{mkt}"
            # summarize()의 z = harness.one_sample_zscore(= EV가 0과 다른지)
            zs = {r: ((results[r]["q8"].get(key) or {}).get("buckets", {}).get(HUG, {}) or {}).get("z")
                  for r in DECISIVE}
            cells[key] = {"z_by_run": zs, "branch": max((branch(z) for z in zs.values()), key=lambda b: order[b])}
        out["cells_8"] = cells
        all_a = all(c["branch"] == "(a) 유지" for c in cells.values())
        out["verdict_8"] = ("4셀 전부 (a) — '0~0.5ATR 즉시진입 금지' 문구 유지"
                            if all_a else
                            "부분 상실 — 문구 유지 근거 일부 상실(쪼갤지 여부는 별도 사전등록)")
        # 9번: 돌파임박 상한검정 유의성 + KR stop_rate 추세
        ceil = {}
        for mkt in ("KR", "US"):
            c = (results["R1"]["q9"].get(f"돌파임박|{mkt}") or {}).get("_상한검정(vs_1.0~1.5)", {})
            c2 = (results["R2"]["q9"].get(f"돌파임박|{mkt}") or {}).get("_상한검정(vs_1.0~1.5)", {})
            ceil[mkt] = {"R1": {k: v.get("z") for k, v in c.items()},
                          "R2": {k: v.get("z") for k, v in c2.items()},
                          "any_significant": any((v.get("significant") for v in list(c.values()) + list(c2.values())))}
        out["ceiling_test_9"] = ceil
        any_sig = any(v["any_significant"] for v in ceil.values())
        out["verdict_9"] = ("상한검정 여전히 비유의 — 현행 표시(참고 기준선) 유지"
                            if not any_sig else
                            "상한검정이 유의해짐 — 기록만, 문구·★ 조건 변경은 별도 사전등록")
    json.dump(out, open(OUT_PATH, "w"), ensure_ascii=False, indent=1, default=str)

    print("\n=== R0 재현 게이트 ===")
    for k, v in gate.items():
        print(f"  {k}: 원본 {v['original_n']} vs R0 {v['r0_n']} ({v['diff_pct']}%) {'OK' if v['pass'] else 'FAIL'}")
    print(f"  → {'PASS' if gate_pass else 'FAIL'}")
    print("\n=== 8번: 0~0.5ATR 버킷 ===")
    for (tab, mkt), (ev, n, z) in ORIGINAL_8.items():
        key = f"{tab}|{mkt}"
        print(f"  {key}: 원래 EV {ev:+.3f} (n {n}) z {z:+.2f}")
        for r in RUNS:
            b = ((results[r]["q8"].get(key) or {}).get("buckets", {}) or {}).get(HUG, {}) or {}
            print(f"    {r}: EV {b.get('ev_R')} (nv {b.get('nv')}) z {b.get('z')}")
    print("\n=== 9번: 돌파임박 상한검정 z(vs 1.0~1.5ATR) ===")
    for mkt in ("KR", "US"):
        for r in RUNS:
            c = (results[r]["q9"].get(f"돌파임박|{mkt}") or {}).get("_상한검정(vs_1.0~1.5)", {})
            print(f"  {mkt} {r}: " + ", ".join(f"{k}: z={v.get('z')}" for k, v in c.items()))
    print("\n=== 9번: KR stop_rate 추세(n>=30 버킷만) ===")
    for name in P9.TABS:
        for r in ("R0", "R1", "R2"):
            d = results[r]["q9"].get(f"{name}|KR") or {}
            row = [(lbl, v.get("stop_rate"), v.get("nv")) for lbl, v in d.items()
                   if isinstance(v, dict) and (v.get("nv") or 0) >= MIN_N_TREND]
            if row:
                print(f"  {name} {r}: " + " | ".join(f"{l} {sr:.0%}(n{n})" for l, sr, n in row if sr is not None))
    for r in RUNS:
        print(f"[diag {r}] {results[r]['diag']}")
    print(f"\n[판정 8] {out.get('verdict_8')}")
    if "cells_8" in out:
        for k, v in out["cells_8"].items():
            print(f"   {k}: {v['branch']} z={v['z_by_run']}")
    print(f"[판정 9] {out.get('verdict_9')}")
    print(f"[done] {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
