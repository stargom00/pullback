"""
안D(피벗 buy-stop 체결) 재검증 — 벤치마크 룩어헤드 수정 (2026-09-12).

사전등록: docs/buystop_bench_revalidation.md §1 (커밋 624656d — 실행 전 고정).
재검증 대상: 2026-09-04_kr_confirm_entry_all_tabs_90cp_entry_buystop.py (원본 무수정).

원본의 판정 코드(TABS / find_confirm_close / find_buystop_fill / run_variants /
half_split / judge_d)는 importlib로 그대로 가져다 쓴다 — 재구현 없음(사전등록 1.2).
새로 쓰는 건 히트 수집 한 곳뿐이고 실행별 차이도 거기서만 난다:

  R0 원본 복제   — 원본 collect_hits와 동일 + 오염된 벤치마크(900일 + 옛 폴백) 재현
  R1 벤치마크만   — harness.bench_score_at_date(1900일, 날짜 기준)로 교체
  R2 + 시총 필터 — R1 + KR 시총 1000억 추정 필터(RS 계산 전, KR만)
  R3 (참고)      — R2 + 730일 창 + clean_at_checkpoint + 날짜 기준 truncate(미래도 날짜 기준)

원본 실행일(2026-09-04)에 날짜·fetch 시작일·유니버스·미래 구간을 맞춘다(사전등록 1.4).

실행: 리포 루트에서 `python3 scripts/measurements/2026-09-12_buystop_bench_revalidation.py`
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


ORIG = _load("2026-09-04_kr_confirm_entry_all_tabs_90cp_entry_buystop.py", "orig_buystop")
PREV5 = _load("2026-09-11_confirm_entry_close_bench_revalidation.py", "prev5_entry_close")
PREV_IMM = _load("2026-09-11_imminent_score_rank_vs_return.py", "prev_imminent_rank")

ORIG_DATE = pd.Timestamp("2026-09-04")
ORIG_UNIV_FILE = "kr_universe_v6_1500_20260904_eod.json"
OFFSETS = ORIG.OFFSETS
NEED_BARS = ORIG.NEED_BARS
RUNS = ("R0", "R1", "R2", "R3")
DECISIVE = ("R1", "R2")
OUT_PATH = os.path.join(MEAS, "2026-09-12_buystop_bench_revalidation.results.json")

# 원본(2026-09-04 발표) KR 안D — 비교표 + R0 재현 게이트(nv, 사전등록 1.5)
ORIGINAL = {
    "눌림목":   {"ev": 0.0706, "nv": 2211, "z": 0.553, "fill": 0.642},
    "돌파임박": {"ev": -0.0995, "nv": 5196, "z": -6.218, "fill": 0.644},
    "박스돌파": {"ev": 0.2050, "nv": 722, "z": 1.152, "fill": 0.672},
    "돌파":     {"ev": 0.1926, "nv": 836, "z": 1.793, "fill": 0.691},
    "추세전환": {"ev": 0.0678, "nv": 1431, "z": 0.665, "fill": 0.610},
}
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
    """원본 collect_hits()와 같은 {탭: [히트]} 를 만든다. 실행별 차이는 여기서만."""
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
    hits = {name: [] for name in ORIG.TABS}
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
            # 미래 구간: R0~R2는 원본과 같은 offset 기준, R3는 날짜 기준
            # (2번 재검증에서 offset↔날짜 혼용이 룩어헤드 assert를 건드린 버그).
            if run == "R3":
                fut = data[t].loc[data[t].index > cp_eff[t]]
            else:
                fut = harness.future_after(data[t], offp)
            fut = fut.loc[fut.index <= ORIG_DATE]   # 원본과 같은 정보 집합
            if len(fut):
                assert fut.index[0] > hist.index[-1], (t, fut.index[0], hist.index[-1])
            rr, rm = rs_ranks.get(t), rs_moms.get(t)
            signal_high = float(hist["High"].iloc[-1])
            signal_low = float(hist["Low"].iloc[-1])
            signal_date = str(hist.index[-1].date())
            trailing50_vol = float(nonzero_vol_mean(hist["Volume"].iloc[-50:]))
            for name, spec in ORIG.TABS.items():
                try:
                    r = spec["fn"](hist, rs_rank=rr, rs_mom=rm, cfg=spec["cfg"], is_kr=ikr)
                except Exception:
                    r = None
                if r is None or not harness.passes_liquidity_filter(r, ikr):
                    continue
                hits[name].append({
                    "ticker": t, "off": off, "is_kr": ikr, "signal_date": signal_date,
                    "close": r.get("close"), "stop": r.get("stop"),
                    "signal_high": signal_high, "signal_low": signal_low,
                    "trailing50_vol": trailing50_vol, "future": fut,
                })
        if (oi + 1) % 15 == 0:
            print(f"[{run}] cp {oi+1}/90 off={off} {cp.date()} "
                  f"{ {k: len(v) for k, v in hits.items()} } {time.time()-t0:.0f}s", flush=True)
    return hits, diag


def _cell(kr_result, kr_half, verdict):
    d = kr_result["안D_buystop"]
    e = kr_half.get("초반(최근시점)", {})
    l = kr_half.get("후반(과거시점)", {})
    ge = lambda x: (x.get("안D_buystop", {}) or {}).get("ev_R") if isinstance(x, dict) else None
    gn = lambda x: (x.get("안D_buystop", {}) or {}).get("nv") if isinstance(x, dict) else None
    return {"ev": d["ev_R"], "nv": d["nv"], "z": kr_result["안D_vs_안A_z"],
            "significant": kr_result["안D_vs_안A_significant"],
            "fill_rate": kr_result["체결율(안D)"],
            "early": (ge(e), gn(e)), "late": (ge(l), gn(l)),
            "z_dedup": kr_result["안D_vs_안A_z_dedup"],
            "ev_slip": kr_result["안D_buystop_슬리피지0.3%"]["ev_R"],
            "ev_cprime": kr_result["안C'_종가진입_확인일저가손절"]["ev_R"],
            "ev_a": kr_result["안A_전체"]["ev_R"],
            "verdict": verdict, "valid": "KR 유효" in verdict}


def do_run(run):
    t0 = time.time()
    hits, diag = collect(run)
    if run != "R0":
        assert PREV5.LEGACY_CALLS == 0, f"{run}이 오염 재현 사본을 호출함"
    else:
        diag["legacy_fallback"] = dict(PREV5.LEGACY_FALLBACK)
    out = {}
    for name, spec in ORIG.TABS.items():
        kr_hits = [h for h in hits[name] if h["is_kr"]]
        kr_result = ORIG.run_variants(kr_hits, spec["stop_key"]) if kr_hits else None
        kr_half = ORIG.half_split(kr_hits, spec["stop_key"]) if kr_hits else {}
        verdict = ORIG.judge_d(kr_result, kr_half)
        out[name] = _cell(kr_result, kr_half, verdict) if kr_result else {"verdict": verdict}
        out[name]["n_kr_hits"] = len(kr_hits)
    diag["lookahead_checks"] = ORIG._lookahead_checks
    diag["elapsed_s"] = round(time.time() - t0)
    print(f"[{run}] done {diag['elapsed_s']}s", flush=True)
    return run, {"tabs": out, "diag": diag}


def main():
    global BLOB
    t0 = time.time()
    BLOB = prepare()
    ctx = mp.get_context("fork")
    with ctx.Pool(len(RUNS)) as pool:
        results = dict(pool.map(do_run, RUNS))

    # ── R0 재현 게이트(사전등록 1.5): 탭별 KR 안D nv ±2% ──
    gate = {}
    for name, orig in ORIGINAL.items():
        got = results["R0"]["tabs"][name].get("nv")
        ok = got is not None and abs(got - orig["nv"]) / orig["nv"] <= R0_GATE_TOL
        gate[name] = {"original_nv": orig["nv"], "r0_nv": got,
                      "diff_pct": None if got is None else round((got - orig["nv"]) / orig["nv"] * 100, 2),
                      "pass": bool(ok)}
    gate_pass = all(v["pass"] for v in gate.values())

    out = {"meta": {"prereg": "docs/buystop_bench_revalidation.md §1 (624656d)",
                    "fetched_at": BLOB["fetched_at"], "dd": BLOB["dd"],
                    "n_fetched": len(BLOB["data"])},
           "original": ORIGINAL, "runs": results, "r0_gate": gate, "r0_gate_pass": gate_pass}

    if not gate_pass:
        out["verdict"] = "판정 없음 — R0 재현 게이트 실패(원인 규명 필요)"
    else:
        flipped = [n for n in ORIG.TABS
                   if all(results[r]["tabs"][n].get("valid") for r in DECISIVE)]
        out["flipped_tabs_restore_candidates"] = flipped
        out["verdict"] = ("전 탭 미달 유지" if not flipped
                          else f"복원 후보(채택 아님, 별도 사전등록 필요): {flipped}")
        # 돌파임박 z 세 갈래(사전등록 1.5) — R1·R2 중 더 보수적인(위쪽) 갈래 적용
        def branch(z):
            if z is None:
                return "판정불가"
            if z <= -1.96:
                return "1. 금지 문구 유지"
            if z < 0:
                return "2. z 인용 제거 — 완화 여부는 별도 사전등록"
            return "3. 금지 문구 철회 검토 — 별도 사전등록"
        zs = {r: results[r]["tabs"]["돌파임박"].get("z") for r in DECISIVE}
        order = {"1. 금지 문구 유지": 0, "2. z 인용 제거 — 완화 여부는 별도 사전등록": 1,
                 "3. 금지 문구 철회 검토 — 별도 사전등록": 2, "판정불가": 3}
        out["imminent_warning_branch"] = {"z_by_run": zs,
                                          "branch": max((branch(z) for z in zs.values()), key=lambda b: order[b])}
    json.dump(out, open(OUT_PATH, "w"), ensure_ascii=False, indent=1, default=str)

    print("\n=== R0 재현 게이트(KR 안D nv) ===")
    for k, v in gate.items():
        print(f"  {k}: 원본 {v['original_nv']} vs R0 {v['r0_nv']} ({v['diff_pct']}%) "
              f"{'OK' if v['pass'] else 'FAIL'}")
    print(f"  → {'PASS' if gate_pass else 'FAIL'}")
    for name in ORIG.TABS:
        o = ORIGINAL[name]
        print(f"\n== {name}")
        print(f"  원래: EV {o['ev']:+.4f} (nv {o['nv']}) z {o['z']:+.3f} 체결율 {o['fill']:.1%}")
        for r in RUNS:
            c = results[r]["tabs"][name]
            if "ev" not in c:
                print(f"  {r:3s}: {c['verdict']}")
                continue
            print(f"  {r:3s}: EV {c['ev']:+.4f} (nv {c['nv']}) z {c['z']:+.3f} "
                  f"체결율 {c['fill_rate']:.1%} 초반 {c['early'][0]} 후반 {c['late'][0]} "
                  f"→ {'유효' if c['valid'] else '미달'}")
    for r in RUNS:
        print(f"[diag {r}] {results[r]['diag']}")
    print(f"\n[판정] {out['verdict']}")
    if "imminent_warning_branch" in out:
        print(f"[금지문구] {out['imminent_warning_branch']}")
    print(f"[done] {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
