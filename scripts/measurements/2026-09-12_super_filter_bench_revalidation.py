"""
슈퍼대장 필터 EV 재검증 — 벤치마크 룩어헤드 수정 (2026-09-12, KR+US).

사전등록: docs/super_filter_bench_revalidation.md §1 (커밋 5aafe60 — 실행 전 고정).
재검증 대상: 2026-09-03_super_filter_ev_90cp_revalidation.py (원본 무수정).

원본의 판정 코드(window_report/ev_of/OFFSETS/OFFSETS_20/MIN_BARS_FLOOR)는 importlib로
그대로 가져다 쓴다 — 재구현 없음(사전등록 1.2). 새로 쓰는 건 히트 수집 한 곳뿐:

  R0 원본 복제   — 원본 collect_hits와 동일 + 오염된 벤치마크(900일 + 옛 폴백) 의도적 재현
  R1 벤치마크만   — 벤치마크만 harness.bench_score_at_date(1900일, 날짜 기준)로 교체
  R2 + 시총 필터 — R1 + KR 시총 1000억 추정 필터(RS 계산 전, KR만)
  R3 (참고)      — R2 + 730일 창 + clean_at_checkpoint + 날짜 기준 truncate

원본 실행일(2026-09-03)에 날짜·fetch 시작일·유니버스·미래 구간을 맞춘다(사전등록 1.4):
offset = 원본 offset + Δ(시장별로 따로 계산), 미래 구간은 2026-09-03까지만.

실행: 리포 루트에서 `python3 scripts/measurements/2026-09-12_super_filter_bench_revalidation.py`
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
import naver_kr
import universe as universe_mod
from scanner import analyze, CONFIG, analyze_super


def _load(fname, modname):
    spec = importlib.util.spec_from_file_location(modname, os.path.join(MEAS, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ORIG = _load("2026-09-03_super_filter_ev_90cp_revalidation.py", "orig_super_filter")
# 오염 재현 함수는 5번 재검증 스크립트 것을 재사용(사본을 또 만들지 않음 — 사전등록 1.4).
PREV5 = _load("2026-09-11_confirm_entry_close_bench_revalidation.py", "prev5_entry_close")
PREV_IMM = _load("2026-09-11_imminent_score_rank_vs_return.py", "prev_imminent_rank")

ORIG_DATE = pd.Timestamp("2026-09-03")
ORIG_UNIV_FILE = "kr_universe_v6_1500_20260903_eod.json"
OFFSETS = ORIG.OFFSETS                     # 60..950 (원본 라벨)
MIN_BARS_FLOOR = ORIG.MIN_BARS_FLOOR
RUNS = ("R0", "R1", "R2", "R3")
DECISIVE = ("R1", "R2")                    # 사전등록 1.5 / §3 확정
OUT_PATH = os.path.join(MEAS, "2026-09-12_super_filter_bench_revalidation.results.json")

# 원본(2026-09-03 발표) 값 — 비교표 + R0 재현 게이트용 리터럴
ORIGINAL = {
    "90개창": {"무필터": (0.0378, 10272), "소속": (-0.0199, 1707), "비소속": (0.0493, 8565),
               "소속_KR": (-0.0892, 538), "소속_US": (0.0120, 1169),
               "gap": -0.0577, "z": -1.5665},
    "20개창": {"무필터": (0.0379, 1636), "소속": (0.0441, 227), "비소속": (0.0369, 1409),
               "소속_KR": (0.0476, 63), "소속_US": (0.0427, 164),
               "gap": 0.0062, "z": 0.0608},
}
R0_GATE_TOL = 0.02   # 사전등록 1.5(사용자 지시): R0 nv가 원본 대비 ±2% 이내여야 진행


def orig_kr_universe():
    """2026-09-03 시점 get_universe("kr") 재현: 정적 + 그날 EOD 동적 + 워치리스트 KR."""
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
    kr_u = orig_kr_universe()
    us_u = universe_mod.get_universe("us")   # 코드 파일 기반, 09-03 이후 변경 없음(git 확인)
    data = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        for t, df in ex.map(lambda t: harness._fetch_kr_one(t, 1900 + dd), list(kr_u)):
            if df is not None:
                data[t] = df
    print(f"[fetch] kr {len(data)}/{len(kr_u)} {time.time()-t0:.0f}s", flush=True)
    us_tickers = list(us_u)
    for i in range(0, len(us_tickers), 100):
        data.update(harness._fetch_us_batch(us_tickers[i:i + 100], period="5y"))
        if (i // 100) % 5 == 0:
            print(f"[fetch] us batch {i//100+1}/{(len(us_tickers)+99)//100} {time.time()-t0:.0f}s", flush=True)
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
    """US 거래일 기준 Δ — 09-03 이후 봉 수. 유동성 큰 US 티커들의 최빈값으로 잡고,
    표본이 갈리면(휴장 처리 차이) 실패시킨다."""
    from collections import Counter
    c = Counter()
    for t, df in data.items():
        if harness.is_kr_ticker(t) or len(df) < 300:
            continue
        after = df.index[df.index > ORIG_DATE]
        c[len(after)] += 1
        if sum(c.values()) >= 200:
            break
    assert c, "US 데이터 없음"
    delta, n = c.most_common(1)[0]
    assert n / sum(c.values()) > 0.8, f"US Δ 불일치 — 분포 {c.most_common(5)}"
    return delta


def collect(run):
    """원본 collect_hits()와 같은 hits 리스트를 만든다. 실행별 차이는 여기서만."""
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
    hits = []
    diag = {"delta_kr": int(d_kr), "delta_us": int(d_us), "mcap_dropped": 0,
            "mcap_unknown": 0, "no_bar_on_cp": 0, "bench_fallback": 0}
    t0 = time.time()
    for oi, off in enumerate(OFFSETS):
        cp = cal[-(off + d_kr + 1)]
        if run == "R0":
            b_k = PREV5._legacy_bench_score_at_CONTAMINATED(k900, off + d_kr)
            b_q = PREV5._legacy_bench_score_at_CONTAMINATED(q900, off + d_kr)
        else:
            b_k = harness.bench_score_at_date(k19, cp)
            b_q = harness.bench_score_at_date(q19, cp)

        trunc_cache = {}
        cp_eff = {}   # R3: 종목별 실제 체크포인트 날짜(KR=cp, US=자체 offset 날짜)
        for t, df in data.items():
            ikr = harness.is_kr_ticker(t)
            offp = off + (d_kr if ikr else d_us)
            if run == "R3":
                tr = df.loc[(df.index >= cp - win) & (df.index <= cp)] if ikr else None
                if not ikr:
                    # US는 KOSPI 캘린더와 달라 자체 offset으로 날짜를 잡는다.
                    if len(df) - offp < 1:
                        continue
                    cp_us = df.index[len(df) - offp - 1]
                    cp_eff[t] = cp_us
                    tr = df.loc[(df.index >= cp_us - win) & (df.index <= cp_us)]
                else:
                    cp_eff[t] = cp
                if tr is None or tr.empty:
                    diag["no_bar_on_cp"] += 1
                    continue
                if ikr and tr.index[-1] != cp:
                    diag["no_bar_on_cp"] += 1
                    continue
                tr = harness.clean_at_checkpoint(tr)
                if tr.empty:
                    diag["no_bar_on_cp"] += 1
                    continue
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
            rr, rm = rs_ranks.get(t), rs_moms.get(t)
            try:
                hit = analyze(hist, rs_rank=rr, rs_mom=rm, cfg=CONFIG, is_kr=ikr)
            except Exception:
                continue
            if hit is None or not harness.passes_liquidity_filter(hit, ikr):
                continue
            try:
                is_super = analyze_super(hist, rs_rank=rr, rs_mom=rm, is_kr=ikr) is not None
            except Exception:
                is_super = False
            # R0~R2는 원본과 같은 offset 기준, R3는 날짜 기준 truncate라 미래도
            # 날짜로 잡는다(봉 빠진 종목에서 offset↔날짜가 어긋나 룩어헤드 assert에
            # 걸렸던 버그 수정 — §1-부록에 기록).
            if run == "R3":
                fut = data[t].loc[data[t].index > cp_eff[t]]
            else:
                fut = harness.future_after(data[t], offp)
            fut = fut.loc[fut.index <= ORIG_DATE]       # 원본과 같은 정보 집합
            if len(fut):
                assert fut.index[0] > hist.index[-1], (t, fut.index[0], hist.index[-1])
            outcome = harness.race(hit.get("close"), hit.get("stop"), fut)
            hits.append({"ticker": t, "off": off, "market": "KR" if ikr else "US",
                         "is_super": is_super, "outcome": outcome})
        if (oi + 1) % 15 == 0:
            print(f"[{run}] cp {oi+1}/90 off={off} {cp.date()} hits={len(hits)} "
                  f"{time.time()-t0:.0f}s", flush=True)
    return hits, diag


def _cells(rep):
    g = lambda k: (rep[k]["ev_R"], rep[k]["nv"])
    return {"무필터": g("무필터_전체"), "소속": g("슈퍼대장_소속"), "비소속": g("슈퍼대장_비소속"),
            "소속_KR": g("소속_KR단독"), "소속_US": g("소속_US단독"),
            "gap": rep["소속_vs_무필터"]["gap_R"], "z": rep["소속_vs_무필터"]["z"],
            "significant": rep["소속_vs_무필터"]["significant"],
            "gap_vs_비소속": rep["소속_vs_비소속(엄밀한 독립비교)"]["gap_R"],
            "z_vs_비소속": rep["소속_vs_비소속(엄밀한 독립비교)"]["z"]}


def do_run(run):
    t0 = time.time()
    hits, diag = collect(run)
    if run != "R0":
        assert PREV5.LEGACY_CALLS == 0, f"{run}이 오염 재현 사본을 호출함"
    else:
        diag["legacy_fallback"] = dict(PREV5.LEGACY_FALLBACK)
    hits_20 = [h for h in hits if h["off"] in ORIG.OFFSETS_20]
    out = {"90개창": _cells(ORIG.window_report(hits)),
           "20개창": _cells(ORIG.window_report(hits_20)) if hits_20 else None,
           "diag": diag, "elapsed_s": round(time.time() - t0)}
    print(f"[{run}] done {out['elapsed_s']}s 90cp={out['90개창']}", flush=True)
    return run, out


def _passes(cell):
    """원본 판정 기준 그대로 — gap>0 이고 |z|>=1.96(harness.ev_gap_zscore의 significant)."""
    return bool(cell["gap"] is not None and cell["gap"] > 0 and cell["significant"])


def main():
    global BLOB
    t0 = time.time()
    BLOB = prepare()
    ctx = mp.get_context("fork")
    with ctx.Pool(len(RUNS)) as pool:
        results = dict(pool.map(do_run, RUNS))

    # ── R0 재현 게이트(사전등록 1.5) ──
    r0 = results["R0"]["90개창"]
    gate = {}
    for key, orig_key in (("무필터", "무필터"), ("소속", "소속"), ("비소속", "비소속")):
        orig_n = ORIGINAL["90개창"][orig_key][1]
        got_n = r0[key][1]
        gate[key] = {"original_nv": orig_n, "r0_nv": got_n,
                     "diff_pct": round((got_n - orig_n) / orig_n * 100, 2),
                     "pass": abs(got_n - orig_n) / orig_n <= R0_GATE_TOL}
    gate_pass = all(v["pass"] for v in gate.values())

    out = {"meta": {"prereg": "docs/super_filter_bench_revalidation.md §1 (5aafe60)",
                    "fetched_at": BLOB["fetched_at"], "dd": BLOB["dd"],
                    "n_kr_universe": len(BLOB["kr_u"]), "n_us_universe": len(BLOB["us_u"]),
                    "n_fetched": len(BLOB["data"])},
           "original": ORIGINAL, "runs": results, "r0_gate": gate, "r0_gate_pass": gate_pass}

    if not gate_pass:
        out["verdict"] = "판정 없음 — R0 재현 게이트 실패(원인 규명 필요)"
    else:
        keep_withdrawn = not all(_passes(results[r]["90개창"]) for r in DECISIVE)
        out["verdict"] = "철회 유지" if keep_withdrawn else "철회 취소 후보 — 복원은 별도 사전등록"
        r0_pass, r1_pass = _passes(r0), _passes(results["R1"]["90개창"])
        if not r0_pass and r1_pass:
            out["interpretation"] = "원래 철회는 벤치마크 오염 탓이었다."
        elif not r0_pass:
            out["interpretation"] = "원래 철회 판정은 벤치마크 오염과 무관하다."
        else:
            out["interpretation"] = "R0이 원래와 다른 방향(원래 결과가 데이터 변동에 민감) — 판정은 R1/R2로."
    json.dump(out, open(OUT_PATH, "w"), ensure_ascii=False, indent=1, default=str)

    print("\n=== R0 재현 게이트 ===")
    for k, v in gate.items():
        print(f"  {k}: 원본 {v['original_nv']} vs R0 {v['r0_nv']} ({v['diff_pct']:+.2f}%) "
              f"{'OK' if v['pass'] else 'FAIL'}")
    print(f"  → {'PASS' if gate_pass else 'FAIL'}")
    print("\n=== 90개창 ===")
    print(f"  원래: 무필터 {ORIGINAL['90개창']['무필터']} 소속 {ORIGINAL['90개창']['소속']} "
          f"gap {ORIGINAL['90개창']['gap']} z {ORIGINAL['90개창']['z']}")
    for r in RUNS:
        c = results[r]["90개창"]
        print(f"  {r}: 무필터 {c['무필터']} 소속 {c['소속']} 비소속 {c['비소속']} "
              f"gap {c['gap']} z {None if c['z'] is None else round(c['z'], 3)} "
              f"KR {c['소속_KR']} US {c['소속_US']} → {'통과' if _passes(c) else '미달'}")
    print("\n=== 20개창(참고) ===")
    for r in RUNS:
        c = results[r]["20개창"]
        if c:
            print(f"  {r}: 무필터 {c['무필터']} 소속 {c['소속']} gap {c['gap']} "
                  f"z {None if c['z'] is None else round(c['z'], 3)}")
    for r in RUNS:
        print(f"[diag {r}] {results[r]['diag']}")
    print(f"\n[판정] {out['verdict']}")
    if "interpretation" in out:
        print(f"[해석] {out['interpretation']}")
    print(f"[done] {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
