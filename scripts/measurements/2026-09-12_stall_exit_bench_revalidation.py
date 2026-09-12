"""
정체 조기청산 EV 재검증 — 벤치마크 룩어헤드 수정 (2026-09-12, 10번).

사전등록: docs/stall_exit_bench_revalidation.md §1 (커밋 6c44317 — 실행 전 고정).
재검증 대상: 2026-09-02_post_entry_stall_exit_ev.py (원본 무수정).

원본의 판정 코드(TAB_SPECS / find_stall / branch_outcomes / analyze_combo /
summarize_rows / time_split_check / N_LIST / X_LIST / GAP_MIN_R)를 importlib로
그대로 가져다 쓴다 — 재구현 없음. 새로 쓰는 건 히트 수집뿐.

  R0 원본 복제 / R1 벤치마크만 수정 / R2 +시총필터 / R3 참고(프로덕션 데이터 경로)

앵커(사전등록 1.3, 사용자 확정):
  KR = 2026-09-02 확정봉 기준(원본 실행 KST 15:33 = 정규장 마감 직후)
  US = 2026-09-01  ← 실행 시각이 ET 09-02 새벽이라 그날 US 세션은 아직 없었다.
       시장별로 미래 구간 컷오프와 Δ를 따로 잡는다(앞선 재검증들과 다른 점).

실행: `python3 scripts/measurements/2026-09-12_stall_exit_bench_revalidation.py`
  MEAS_CACHE=경로   fetch 캐시
  RUNS=R0           실행 목록 축소(기본 R0,R1,R2,R3)
  KR_ANCHOR_EXTRA=1 KR만 Δ를 하나 더(사전등록 1.3의 (b) 진단용)
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


def _load(fname, modname):
    spec = importlib.util.spec_from_file_location(modname, os.path.join(MEAS, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ORIG = _load("2026-09-02_post_entry_stall_exit_ev.py", "orig_stall")
PREV5 = _load("2026-09-11_confirm_entry_close_bench_revalidation.py", "prev5_entry_close")
PREV_IMM = _load("2026-09-11_imminent_score_rank_vs_return.py", "prev_imminent_rank")

KR_ANCHOR = pd.Timestamp("2026-09-02")   # 원본 실행 시점 KR 마지막 봉
US_ANCHOR = pd.Timestamp("2026-09-01")   # 같은 시점 US 마지막 봉(ET 새벽 실행)
ORIG_UNIV_FILE = "kr_universe_v6_1500_20260902_eod.json"
OFFSETS = ORIG.OFFSETS
MIN_BARS_FLOOR = ORIG.MIN_BARS_FLOOR
RUNS = tuple(os.environ.get("RUNS", "R0,R1,R2,R3").split(","))
DECISIVE = ("R1", "R2")
KR_ANCHOR_EXTRA = int(os.environ.get("KR_ANCHOR_EXTRA", "0"))
OUT_PATH = os.path.join(MEAS, "2026-09-12_stall_exit_bench_revalidation"
                        + (f".kr_anchor+{KR_ANCHOR_EXTRA}" if KR_ANCHOR_EXTRA else "")
                        + ".results.json")

# R0 재현 게이트(사전등록 1.4) — 원본 results.json의 n_stall 상위 3조합
GATE = {("눌림목", "N2_X2pct"): 3552, ("돌파임박", "N2_X3pct"): 10559,
        ("박스돌파", "N5_X3pct"): 368}
R0_GATE_TOL = 0.02


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
    dd = (datetime.now().date() - KR_ANCHOR.date()).days
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
            "run_stamp": harness.run_stamp(data)}
    print(f"[fetch] total {len(data)} tickers, {time.time()-t0:.0f}s "
          f"stamp={blob['run_stamp']}", flush=True)
    if cache:
        pickle.dump(blob, open(cache, "wb"))
    return blob


BLOB = None


def _delta_us(data):
    """US Δ — US_ANCHOR(09-01) 이후 US 거래일 수. 최빈값으로 잡고 갈리면 실패."""
    from collections import Counter
    c = Counter()
    for t, df in data.items():
        if harness.is_kr_ticker(t) or len(df) < 300:
            continue
        c[len(df.index[df.index > US_ANCHOR])] += 1
        if sum(c.values()) >= 200:
            break
    assert c, "US 데이터 없음"
    delta, n = c.most_common(1)[0]
    assert n / sum(c.values()) > 0.8, f"US Δ 불일치 — {c.most_common(5)}"
    return delta


def collect(run):
    data, mcap = BLOB["data"], BLOB["mcap"]
    k900 = BLOB["bench900"]["kospi"]["Close"].dropna()
    q900 = BLOB["bench900"]["kosdaq"]["Close"].dropna()
    k19 = BLOB["bench1900"]["kospi"]["Close"].dropna()
    q19 = BLOB["bench1900"]["kosdaq"]["Close"].dropna()
    cal = k19.index
    assert KR_ANCHOR in cal, "KR 앵커 날짜가 지수 캘린더에 없음"
    d_kr = len(cal) - 1 - cal.get_loc(KR_ANCHOR) + KR_ANCHOR_EXTRA
    d_us = _delta_us(data)
    win = pd.Timedelta(days=PREV_IMM.PROD_WINDOW_DAYS)
    hits = {tab: [] for tab in ORIG.TAB_SPECS}
    diag = {"delta_kr": int(d_kr), "delta_us": int(d_us), "kr_anchor_extra": KR_ANCHOR_EXTRA,
            "mcap_dropped": 0, "mcap_unknown": 0, "no_bar_on_cp": 0, "short_future": 0}
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
                if len(df) - offp < MIN_BARS_FLOOR:
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
            cutoff = KR_ANCHOR if ikr else US_ANCHOR   # 시장별 컷오프(사전등록 1.3)
            if run == "R3":
                fut = data[t].loc[data[t].index > cp_eff[t]]
            else:
                fut = harness.future_after(data[t], offp)
            fut = fut.loc[fut.index <= cutoff]
            if len(fut):
                assert fut.index[0] > hist.index[-1], (t, fut.index[0], hist.index[-1])
            if len(fut) < max(ORIG.N_LIST):   # 원본 가드 그대로
                diag["short_future"] += 1
                continue
            rr, rm = rs_ranks.get(t), rs_moms.get(t)
            for tab, (fn, cfg) in ORIG.TAB_SPECS.items():
                try:
                    h = fn(hist, rs_rank=rr, rs_mom=rm, cfg=cfg, is_kr=ikr)
                except Exception:
                    h = None
                if h is None or not harness.passes_liquidity_filter(h, ikr):
                    continue
                entry, stop = h.get("close"), h.get("stop")
                if entry is None or stop is None or entry <= stop:
                    continue
                hits[tab].append({"ticker": t, "off": off, "is_kr": ikr,
                                   "entry": entry, "stop": stop, "future": fut})
        if (oi + 1) % 20 == 0:
            print(f"[{run}] cp {oi+1}/90 off={off} {cp.date()} "
                  + " ".join(f"{k}={len(v)}" for k, v in hits.items())
                  + f" {time.time()-t0:.0f}s", flush=True)
    return hits, diag


def do_run(run):
    t0 = time.time()
    hits, diag = collect(run)
    if run != "R0":
        assert PREV5.LEGACY_CALLS == 0, f"{run}이 오염 재현 사본을 호출함"
    else:
        diag["legacy_fallback"] = dict(PREV5.LEGACY_FALLBACK)
    out = {}
    for tab, tab_hits in hits.items():
        combos = {}
        for N in ORIG.N_LIST:
            for X in ORIG.X_LIST:
                key = f"N{N}_X{int(round(X*100))}pct"
                rows = ORIG.analyze_combo(tab_hits, N, X)
                summary = ORIG.summarize_rows(rows)
                # 채택 조건 3(시기반분)은 gap·z를 통과한 조합에만 원본이 돌린다 —
                # 같은 순서로 여기서도 필요할 때만 계산.
                gap = summary["B_vs_A"]["gap_R"]
                passes_12 = gap is not None and gap >= ORIG.GAP_MIN_R and summary["B_vs_A"]["significant"]
                split = ORIG.time_split_check(rows) if passes_12 else None
                combos[key] = {"n_stall": summary["n_stall"],
                                "ev_a": summary["A_즉시청산"]["ev_R"],
                                "ev_b": summary["B_계속보유"]["ev_R"],
                                "gap": gap, "z": summary["B_vs_A"]["z"],
                                "significant": summary["B_vs_A"]["significant"],
                                "passes_gap_and_z": passes_12,
                                "time_split": split}
        out[tab] = {"n_hits_total": len(tab_hits), "combos": combos}
    diag["elapsed_s"] = round(time.time() - t0)
    print(f"[{run}] done {diag['elapsed_s']}s", flush=True)
    return run, {"tabs": out, "diag": diag}


def _adopted(cell):
    """원본 채택 조건 3개 — gap>=0.15 & z유의 & 시기반분 두 반기 gap>=0.15."""
    if not cell["passes_gap_and_z"] or not cell["time_split"]:
        return False
    sp = cell["time_split"]
    older = (sp.get("전반부(이전, off510~950)") or {}).get("B_vs_A", {}).get("gap_R")
    recent = (sp.get("후반부(최근, off60~500)") or {}).get("B_vs_A", {}).get("gap_R")
    return (older is not None and older >= ORIG.GAP_MIN_R
            and recent is not None and recent >= ORIG.GAP_MIN_R)


def main():
    global BLOB
    t0 = time.time()
    BLOB = prepare()
    ctx = mp.get_context("fork")
    with ctx.Pool(len(RUNS)) as pool:
        results = dict(pool.map(do_run, RUNS))

    gate = {}
    if "R0" in results:
        for (tab, key), n_orig in GATE.items():
            got = results["R0"]["tabs"][tab]["combos"][key]["n_stall"]
            gate[f"{tab}|{key}"] = {"original": n_orig, "r0": got,
                                     "diff_pct": round((got - n_orig) / n_orig * 100, 2),
                                     "pass": abs(got - n_orig) / n_orig <= R0_GATE_TOL}
    gate_pass = bool(gate) and all(v["pass"] for v in gate.values())

    out = {"meta": {"prereg": "docs/stall_exit_bench_revalidation.md §1 (6c44317)",
                    "kr_anchor": str(KR_ANCHOR.date()), "us_anchor": str(US_ANCHOR.date()),
                    "kr_anchor_extra": KR_ANCHOR_EXTRA, "runs": list(RUNS),
                    "run_stamp": BLOB.get("run_stamp"), "n_fetched": len(BLOB["data"])},
           "gate_original_n_stall": {f"{t}|{k}": v for (t, k), v in GATE.items()},
           "r0_gate": gate, "r0_gate_pass": gate_pass, "runs": results}
    if gate_pass and all(r in results for r in DECISIVE):
        adopted = [f"{tab}|{key}" for tab in ORIG.TAB_SPECS for key in results[DECISIVE[0]]["tabs"][tab]["combos"]
                   if all(_adopted(results[r]["tabs"][tab]["combos"][key]) for r in DECISIVE)]
        out["adopted_candidates"] = adopted
        out["verdict"] = ("27조합 전부 기각 유지" if not adopted
                          else f"채택 후보(규칙 도입은 별도 사전등록): {adopted}")
    else:
        out["verdict"] = "판정 없음 — R0 게이트 실패 또는 판정 실행 미포함"
    json.dump(out, open(OUT_PATH, "w"), ensure_ascii=False, indent=1, default=str)

    print("\n=== R0 재현 게이트(n_stall) ===")
    for k, v in gate.items():
        print(f"  {k}: 원본 {v['original']} vs R0 {v['r0']} ({v['diff_pct']:+.2f}%) "
              f"{'OK' if v['pass'] else 'FAIL'}")
    print(f"  → {'PASS' if gate_pass else 'FAIL'}")
    for tab in ORIG.TAB_SPECS:
        print(f"\n== {tab}")
        for key in results[RUNS[0]]["tabs"][tab]["combos"]:
            parts = [f"  {key:12s}"]
            for r in RUNS:
                c = results[r]["tabs"][tab]["combos"][key]
                gap_s = "—" if c["gap"] is None else format(c["gap"], "+.3f")
                z_s = "—" if c["z"] is None else format(c["z"], ".2f")
                parts.append(f"{r} n{c['n_stall']} gap {gap_s} z {z_s}")
            print(" | ".join(parts))
    for r in RUNS:
        print(f"[diag {r}] {results[r]['diag']}")
    print(f"\n[판정] {out['verdict']}")
    print(f"[done] {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
