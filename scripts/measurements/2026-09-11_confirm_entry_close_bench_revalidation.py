"""
종가진입 확인진입 5탭 재검증 — 벤치마크 룩어헤드 수정 (2026-09-11, KR 전용).

사전등록: docs/confirm_entry_close_bench_revalidation.md §1 (커밋 37bf3ab — 실행 전 고정).
재검증 대상: 2026-09-04_kr_confirm_entry_all_tabs_90cp_entry_close.py (원본 무수정).

원본의 판정 코드(TABS / find_confirm_close / run_confirm_analysis / half_split /
judge / OFFSETS)는 importlib로 **그대로 가져와 호출**한다 — 재구현 없음(사전등록 1.2).
이 스크립트가 새로 쓰는 건 히트 수집(collect) 한 곳뿐이고, 실행별 차이는 거기서만 난다:

  R0 원본 복제   — 원본 collect_hits와 동일 + 오염된 벤치마크(900일 + 옛 폴백) 의도적 재현
  R1 벤치마크만   — R0에서 벤치마크 계산만 harness.bench_score_at_date(1900일)로 교체
  R2 + 시총 필터 — R1 + KR 시총 1000억 추정 필터(RS 계산 전)
  R3 (참고)      — R2 + 730일 창 + clean_at_checkpoint + 날짜 기준 truncate + 레이스 구간 무효봉 제거

체크포인트 날짜·fetch 시작일·유니버스·미래 구간을 원본 실행(2026-09-04 장 마감 후)과
같게 맞춘다(사전등록 1.4): offset = 원본 offset + Δ(09-04 이후 봉 수), 미래 구간은
2026-09-04까지만, 유니버스 = 09-04 EOD 파일 기반 get_universe 재현.

실행: 리포 루트에서 `python3 scripts/measurements/2026-09-11_confirm_entry_close_bench_revalidation.py`
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

import numpy as np
import pandas as pd

import harness
from scanner import rs_raw_score


def _load(fname, modname):
    spec = importlib.util.spec_from_file_location(modname, os.path.join(MEAS, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ORIG = _load("2026-09-04_kr_confirm_entry_all_tabs_90cp_entry_close.py", "orig_entry_close")
PREV = _load("2026-09-11_imminent_score_rank_vs_return.py", "prev_imminent_rank")  # anchor_universe, fetch_mcap_now, 창/시총 상수

ORIG_DATE = pd.Timestamp("2026-09-04")
OFFSETS = ORIG.OFFSETS                     # 60..950 (원본 라벨)
NEED_BARS = ORIG.NEED_BARS
RUNS = ("R0", "R1", "R2", "R3")
DECISIVE = ("R1", "R2")                    # 사전등록 1.5 / §3 확정
OUT_PATH = os.path.join(MEAS, "2026-09-11_confirm_entry_close_bench_revalidation.results.json")

# 원래(2026-09-04 발표) KR 결과 — 원본 results.json(gitignore)에서 추출, 비교표용 리터럴
ORIGINAL = {
    "눌림목":   {"ev": 0.1703, "nv": 634,  "z": 1.972, "early": (0.2757, 301), "late": (0.0751, 333), "n_hits": 3449, "valid": False},
    "돌파임박": {"ev": 0.1574, "nv": 1271, "z": 2.371, "early": (0.1534, 678), "late": (0.1619, 593), "n_hits": 8092, "valid": True},
    "박스돌파": {"ev": 0.2861, "nv": 388,  "z": 1.910, "early": (0.4196, 224), "late": (0.1037, 164), "n_hits": 1074, "valid": False},
    "돌파":     {"ev": 0.2254, "nv": 448,  "z": 1.918, "early": (0.3735, 249), "late": (0.0402, 199), "n_hits": 1210, "valid": False},
    "추세전환": {"ev": 0.1444, "nv": 381,  "z": 1.486, "early": (0.3497, 143), "late": (0.0210, 238), "n_hits": 2353, "valid": False},
}


# ══════════════════════════════════════════════════════════════════
# ⚠️ 오염 재현 전용 — 사용 금지. harness.bench_score_at()의 2026-09-11 이전
# 동작(n-off<=0이면 전체 시계열=룩어헤드, rs None이면 0.0) 사본. R0에서만 호출.
# 판정 실행(R1/R2)이 이걸 부르지 않았음을 LEGACY_CALLS로 assert한다(사전등록 1.8).
# ══════════════════════════════════════════════════════════════════
LEGACY_CALLS = 0
LEGACY_FALLBACK = {"full_series_lookahead": 0, "none_to_zero": 0, "ok": 0}


def _legacy_bench_score_at_CONTAMINATED(bench_close, off):
    global LEGACY_CALLS
    LEGACY_CALLS += 1
    if bench_close is None or len(bench_close) == 0:
        return 0.0
    n = len(bench_close)
    trunc = bench_close.iloc[: n - off] if off > 0 and n - off > 0 else bench_close
    if not (off > 0 and n - off > 0):
        LEGACY_FALLBACK["full_series_lookahead"] += 1
    s = rs_raw_score(trunc)
    if s is None:
        LEGACY_FALLBACK["none_to_zero"] += 1
    elif off > 0 and n - off > 0:
        LEGACY_FALLBACK["ok"] += 1
    return s if s is not None else 0.0


def _invalid_mask(df):
    # app._filter_invalid_bars()의 무효봉 조건과 동일(동기화 필요 — 그 함수는
    # 갈래B 트렁케이션까지 하므로 미래 구간 행 제거용으로 직접 쓸 수 없어 조건만 사용).
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    return (o <= 0) | (h <= 0) | (l <= 0) | (c <= 0) | o.isna() | h.isna() | l.isna() | c.isna() | (h < l)


# ── 데이터 준비 ──────────────────────────────────────────────────────
def prepare():
    cache = os.environ.get("MEAS_CACHE")
    if cache and os.path.exists(cache):
        print(f"[cache] load {cache}", flush=True)
        return pickle.load(open(cache, "rb"))
    t0 = time.time()
    # naver_kr._fetch_sise_history는 datetime.now()(로컬) 기준 start를 잡는다 —
    # 원본(2026-09-04 로컬 실행)과 같은 시작일이 되도록 Δd를 더한다(사전등록 1.4).
    dd = (datetime.now().date() - ORIG_DATE.date()).days
    univ = PREV.anchor_universe()
    data = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        for t, df in ex.map(lambda t: harness._fetch_kr_one(t, 1900 + dd), list(univ)):
            if df is not None:
                data[t] = df
    blob = {
        "data": data, "univ_n": len(univ), "dd": dd,
        "bench900": harness.fetch_kr_benchmarks(days=900 + dd),
        "bench1900": harness.fetch_kr_benchmarks(days=1900 + dd),
        "mcap": PREV.fetch_mcap_now(),
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
    }
    print(f"[fetch] univ={len(univ)} fetched={len(data)} dd={dd} {time.time()-t0:.0f}s", flush=True)
    if cache:
        pickle.dump(blob, open(cache, "wb"))
    return blob


BLOB = None


def collect(run):
    """원본 collect_hits()와 같은 형태의 hits dict를 만든다. 실행별 차이는 여기서만."""
    data, mcap = BLOB["data"], BLOB["mcap"]
    k900 = BLOB["bench900"]["kospi"]["Close"].dropna()
    q900 = BLOB["bench900"]["kosdaq"]["Close"].dropna()
    k19 = BLOB["bench1900"]["kospi"]["Close"].dropna()
    q19 = BLOB["bench1900"]["kosdaq"]["Close"].dropna()
    cal = k19.index
    assert ORIG_DATE in cal
    delta = len(cal) - 1 - cal.get_loc(ORIG_DATE)
    win = pd.Timedelta(days=PREV.PROD_WINDOW_DAYS)
    hits = {name: [] for name in ORIG.TABS}
    diag = {"delta": int(delta), "mcap_dropped": 0, "mcap_unknown": 0, "no_bar_on_cp": 0,
            "analyze_exceptions": 0, "rs_universe_mean": 0.0}
    t0 = time.time()
    for oi, off in enumerate(OFFSETS):
        offp = off + delta
        cp = cal[-(offp + 1)]
        if run == "R0":
            if offp + 1 <= len(k900):   # 900일 벤치마크가 닿는 구간에선 캘린더 일치 확인
                assert k900.index[-(offp + 1)] == cp
            b_k = _legacy_bench_score_at_CONTAMINATED(k900, offp)
            b_q = _legacy_bench_score_at_CONTAMINATED(q900, offp)
        else:
            b_k = harness.bench_score_at_date(k19, cp)
            b_q = harness.bench_score_at_date(q19, cp)

        cache = {}
        for t, df in data.items():
            if run == "R3":
                tr = df.loc[(df.index >= cp - win) & (df.index <= cp)]
                if tr.empty or tr.index[-1] != cp:
                    diag["no_bar_on_cp"] += 1
                    continue
                tr = harness.clean_at_checkpoint(tr)
                if tr.empty or tr.index[-1] != cp:
                    diag["no_bar_on_cp"] += 1
                    continue
            else:
                if len(df) - offp < NEED_BARS:
                    continue
                tr = harness.truncate_at(df, offp)
            if run in ("R2", "R3"):
                m = mcap.get(t)
                if m is None:
                    diag["mcap_unknown"] += 1
                elif m[0] * float(tr["Close"].iloc[-1]) / m[1] < PREV.MCAP_MIN_EOK:
                    diag["mcap_dropped"] += 1
                    continue
            cache[t] = tr
        if run == "R3":
            for t, h in cache.items():
                assert h.index.max() == cp, (t, h.index.max(), cp)
        diag["rs_universe_mean"] += len(cache) / len(OFFSETS)
        rs_ranks, rs_moms = harness.compute_rs_at_checkpoint(cache, b_k, b_q)

        for t, hist in cache.items():
            raw = data[t]
            if run == "R3":
                fut = raw.loc[(raw.index > cp) & (raw.index <= ORIG_DATE)]
                fut = fut[~_invalid_mask(fut)]
            else:
                fut = harness.future_after(raw, offp)
                fut = fut.loc[fut.index <= ORIG_DATE]   # 원본과 같은 정보 집합(09-04까지)
            signal_high = float(hist["High"].iloc[-1])
            signal_low = float(hist["Low"].iloc[-1])
            trailing50_vol = float(ORIG.nonzero_vol_mean(hist["Volume"].iloc[-50:]))
            rr, rm = rs_ranks.get(t), rs_moms.get(t)
            for name, spec in ORIG.TABS.items():
                try:
                    r = spec["fn"](hist, rs_rank=rr, rs_mom=rm, cfg=spec["cfg"], is_kr=True)
                except Exception:
                    diag["analyze_exceptions"] += 1
                    r = None
                if r is None or not harness.passes_liquidity_filter(r, True):
                    continue
                hits[name].append({
                    "ticker": t, "off": off, "is_kr": True,
                    "close": r.get("close"), "stop": r.get("stop"),
                    "signal_high": signal_high, "signal_low": signal_low,
                    "trailing50_vol": trailing50_vol, "future": fut,
                })
        if (oi + 1) % 15 == 0:
            print(f"[{run}] cp {oi+1}/90 off={off} {cp.date()} {time.time()-t0:.0f}s "
                  f"{ {k: len(v) for k, v in hits.items()} }", flush=True)
    return hits, diag


def _cell(tab_rep):
    k = tab_rep["KR"]
    h = tab_rep["KR_시기반분"]
    c = k["안C_종가진입"]
    e = h.get("초반(최근시점)", {})
    l = h.get("후반(과거시점)", {})
    ec = e.get("안C_종가진입", {}) if isinstance(e, dict) else {}
    lc = l.get("안C_종가진입", {}) if isinstance(l, dict) else {}
    return {"ev": c["ev_R"], "nv": c["nv"], "z": k["안C_종가진입_vs_안A_z"],
            "early": (ec.get("ev_R"), ec.get("nv")), "late": (lc.get("ev_R"), lc.get("nv")),
            "n_hits": tab_rep["n_kr"], "confirm_rate": k["확인율"],
            "ev_slip": k["안C_종가진입_슬리피지0.3%"]["ev_R"],
            "ev_pivot": k["안C_피벗진입"]["ev_R"],
            "valid": "KR 유효" in tab_rep["판정"], "verdict": tab_rep["판정"]}


def do_run(run):
    t0 = time.time()
    hits, diag = collect(run)
    if run != "R0":
        assert LEGACY_CALLS == 0, f"{run}이 오염 재현 사본을 호출함"
    rep = {}
    invalid_in_window = {}
    for name, spec in ORIG.TABS.items():
        hl = hits[name]
        kr_result = ORIG.run_confirm_analysis(hl, spec["stop_key"]) if hl else None
        kr_half = ORIG.half_split(hl, spec["stop_key"]) if hl else {}
        verdict = ORIG.judge(kr_result, kr_half)
        rep[name] = _cell({"KR": kr_result, "KR_시기반분": kr_half, "n_kr": len(hl), "판정": verdict})
        # 진단: 확인건의 레이스 구간(확인일 다음 60봉)에 무효봉이 섞인 수(사전등록 1.7-4)
        cnt = 0
        for h in hl:
            conf = ORIG.find_confirm_close(h)
            if conf is None:
                continue
            w = h["future"].iloc[conf[0]: conf[0] + 60]
            if len(w) and bool(_invalid_mask(w).any()):
                cnt += 1
        invalid_in_window[name] = cnt
    diag["confirmed_with_invalid_bar_in_race"] = invalid_in_window
    if run == "R0":
        diag["legacy_bench_calls"] = LEGACY_CALLS
        diag["legacy_fallback"] = dict(LEGACY_FALLBACK)
    diag["lookahead_checks"] = ORIG._lookahead_checks
    diag["elapsed_s"] = round(time.time() - t0)
    print(f"[{run}] done {diag['elapsed_s']}s", flush=True)
    return run, rep, diag


def _fmt(c):
    f = lambda x: "—" if x is None else f"{x:+.3f}"
    z = "—" if c["z"] is None else f"{c['z']:.2f}"
    return (f"EV {f(c['ev'])} z {z} nv {c['nv']} | "
            f"초반 {f(c['early'][0])}({c['early'][1]}) 후반 {f(c['late'][0])}({c['late'][1]}) | "
            f"{'✓유효' if c['valid'] else '✗무효'}")


def main():
    global BLOB
    t0 = time.time()
    BLOB = prepare()
    ctx = mp.get_context("fork")
    with ctx.Pool(len(RUNS)) as pool:
        results = pool.map(do_run, RUNS)
    res = {r: {"tabs": rep, "diag": diag} for r, rep, diag in results}

    # ── 판정(사전등록 1.5): 돌파임박 KR이 R1·R2 둘 다 원본 judge()로 "KR 유효"여야 유지 ──
    keep = all(res[r]["tabs"]["돌파임박"]["valid"] for r in DECISIVE)
    verdict = "유지" if keep else "철회"
    im = {"원래": ORIGINAL["돌파임박"], **{r: res[r]["tabs"]["돌파임박"] for r in RUNS}}
    if res["R0"]["tabs"]["돌파임박"]["valid"] and not res["R1"]["tabs"]["돌파임박"]["valid"]:
        interp = "원래 통과는 벤치마크 오염 덕분이었다."
    elif res["R1"]["tabs"]["돌파임박"]["valid"]:
        interp = "원래 통과는 벤치마크 오염과 무관하게 기준을 넘는다."
    elif not res["R0"]["tabs"]["돌파임박"]["valid"]:
        interp = "원래 통과는 오염을 재현한 조건에서도 재현되지 않는다(데이터 변동에 민감)."
    else:
        interp = "(사전 정의된 문장 해당 없음)"
    new_valid_tabs = {r: [n for n in ORIG.TABS if n != "돌파임박" and res[r]["tabs"][n]["valid"]] for r in RUNS}

    out = {"meta": {"prereg": "docs/confirm_entry_close_bench_revalidation.md §1 (37bf3ab)",
                    "fetched_at": BLOB["fetched_at"], "dd": BLOB["dd"],
                    "univ_n": BLOB["univ_n"], "fetched_n": len(BLOB["data"])},
           "original": ORIGINAL, "runs": res,
           "imminent_kr_decomposition": im,
           "verdict_imminent_kr": verdict, "interpretation": interp,
           "other_tabs_newly_valid_not_adopted": new_valid_tabs}
    json.dump(out, open(OUT_PATH, "w"), ensure_ascii=False, indent=1, default=str)

    for name in ORIG.TABS:
        print(f"\n== {name}")
        print(f"  원래  {_fmt(ORIGINAL[name])}")
        for r in RUNS:
            print(f"  {r:4s}  {_fmt(res[r]['tabs'][name])}")
    for r in RUNS:
        print(f"[diag {r}] {res[r]['diag']}")
    print(f"\n[판정] 돌파임박 KR 종가진입: {verdict} — {interp}")
    print(f"[참고] 원래 무효 → 유효로 바뀐 다른 탭(채택 안 함): {new_valid_tabs}")
    print(f"[done] {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
