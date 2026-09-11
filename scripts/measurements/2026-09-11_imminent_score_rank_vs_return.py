"""
돌파임박 score 순위와 5거래일 수익률의 관계 (2026-09-11, KR 전용, 90개 체크포인트).

사전등록: docs/imminent_score_rank_vs_return.md §1 (커밋 e9c9395 — 실행 전 고정).
판정 기준·게이트·임계값의 출처는 전부 그 문서에 있고, 여기 상수는 그 사본이
아니라 "문서 1.x" 참조를 달아둔 구현값이다.

하네스와 다르게 재는 부분(README 규칙3 — 이유는 사전등록 1.3):
  1. 체크포인트를 KOSPI 지수 캘린더의 "날짜"로 고정하고 종목별로 df.loc[:cp_date]
     (harness.truncate_at의 "끝에서 off봉"은 봉 누락 종목의 날짜를 어긋나게 해
     같은-날 횡단면 순위 비교를 깨뜨림).
  2. 벤치마크도 날짜 기준 truncate + 253봉 미만이면 실패(harness.bench_score_at의
     조용한 0.0/전체시계열 폴백 회피 — docs/kr_us_strategy_map.md "벤치마크
     룩어헤드 재검증" 절).
  3. 프로덕션 KR 시총 1000억 필터(app._MCAP_MIN_EOK)를 현재시총×가격비 추정치로 재현.
  4. 프로덕션 fetch 창(naver_kr.fetch_history 기본 days, 현재 730일)을 체크포인트
     시점에 재현 + harness.clean_at_checkpoint()(= app._downcast)를 창 적용 후에.
     (4는 사전등록 커밋 이후 추가 — §1-부록 참고, 결과를 보기 전 결정.)

실행: 리포 루트에서
  python3 scripts/measurements/2026-09-11_imminent_score_rank_vs_return.py [--anchor-only]
환경변수 MEAS_CACHE=경로 를 주면 fetch 결과를 그 pickle에 캐시/재사용.
"""
import sys
import os
import json
import time
import math
import pickle
import inspect

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts", "measurements"))

import numpy as np
import pandas as pd
import requests

import harness
import naver_kr
import universe as universe_mod
import app
from scanner import analyze_imminent, IMMINENT_CONFIG, rs_raw_score, volume_info

OFFSETS = harness.checkpoints(60, 950, 10)   # 90개 — 규칙9 표준
RECENT_OFFSETS = set(OFFSETS[:45])            # off 60~500 (최근 반기)
OLDER_OFFSETS = set(OFFSETS[45:])             # off 510~950 (이전 반기)

# 프로덕션 fetch 창 — naver_kr.fetch()가 fetch_history() 기본값을 쓴다. 리터럴 복사
# 대신 시그니처에서 읽어 동기화(CLAUDE.md CONFIG 동기화 원칙과 같은 취지).
PROD_WINDOW_DAYS = inspect.signature(naver_kr.fetch_history).parameters["days"].default
MCAP_MIN_EOK = app._MCAP_MIN_EOK                # 프로덕션 시총 컷(억원)

T_PLUS = 5                 # 사전등록 1.4-1 (사용자 지시: 5거래일)
MIN_HITS_FOR_RANK = 5      # 사전등록 1.3 — AI 판단 임의값(사용자 승인)
RHO_MIN = 0.10             # 사전등록 1.5 — 사용자 지시 원문, 임의값
QUINTILE_MIN_N = 100       # 사전등록 1.5 — 사용자 지시 원문
VALIDITY_MAX_GAP = 0.03    # 사전등록 1.6b — 사용자 지시 원문(±3%p)
ANCHOR_JACCARD_MIN = 0.90  # 사전등록 1.6a — AI 판단 임의값
T5_MISSING_WARN = 0.02     # 사전등록 1.4-1 — AI 판단 임의값(보고 트리거, 판정 아님)
BENCH_MIN_BARS = 253       # 사전등록 1.7-4 (rs_raw_score 4분기 = 252+1봉)

# ── 앵커 (사전등록 1.6a) — 원본 파일이 다른 세션 scratchpad에 있어 리터럴로 고정 ──
ANCHOR_DATE = "2026-09-04"
ANCHOR_UNIV_FILE = "kr_universe_v6_1500_20260904_eod.json"
# 앵커 A: 프로덕션 sector_snapshot daykey 2026-09-06(일, 09-04 종가 데이터) KR imminent
ANCHOR_A = ['001450.KS', '001540.KQ', '003010.KS', '010950.KS', '010955.KS', '011560.KQ',
            '017890.KQ', '035610.KQ', '036800.KQ', '041520.KQ', '041830.KQ', '041960.KQ',
            '051160.KQ', '051360.KQ', '053260.KQ', '071200.KQ', '072870.KQ', '078930.KS',
            '086670.KQ', '089600.KQ', '092460.KQ', '092730.KQ', '093190.KQ', '093320.KQ',
            '093520.KQ', '096530.KQ', '100120.KQ', '101160.KQ', '123330.KQ', '161890.KS',
            '181710.KS', '204610.KQ', '204620.KQ', '228850.KQ', '241710.KQ', '257720.KQ',
            '340570.KQ', '377450.KQ', '439090.KQ']
# 앵커 B: 프로덕션 signal_snapshot signal_date=2026-09-04 KR 돌파임박(09-11 수신본 13개)
ANCHOR_B = ['001540.KQ', '003010.KS', '053260.KQ', '086670.KQ', '089600.KQ', '092460.KQ',
            '100120.KQ', '204610.KQ', '204620.KQ', '228850.KQ', '340570.KQ', '348350.KQ',
            '377450.KQ']
# 창 2 recon.py(app._fetch/app._compute_rs_ranks 경로) 재구성 score — 대조용
RECON_SCORES = {'001450.KS': 58.1, '001540.KQ': 50.6, '003010.KS': 59.4, '010950.KS': 53.1,
                '010955.KS': 58.8, '011560.KQ': 79.0, '017890.KQ': 75.5, '035610.KQ': 76.7,
                '036800.KQ': 54.8, '041520.KQ': 73.8, '041830.KQ': 69.3, '041960.KQ': 65.9,
                '051160.KQ': 55.2, '051360.KQ': 52.0, '053260.KQ': 57.4, '071200.KQ': 80.3,
                '072870.KQ': 75.5, '078930.KS': 62.9, '086670.KQ': 70.7, '089600.KQ': 65.9,
                '092460.KQ': 51.4, '092730.KQ': 61.6, '093190.KQ': 78.1, '093320.KQ': 73.8,
                '093520.KQ': 71.5, '096530.KQ': 68.7, '100120.KQ': 70.0, '101160.KQ': 71.3,
                '123330.KQ': 58.6, '161890.KS': 58.3, '181710.KS': 76.2, '204610.KQ': 68.2,
                '204620.KQ': 61.9, '228850.KQ': 65.9, '241710.KQ': 79.8, '257720.KQ': 78.1,
                '340570.KQ': 55.4, '377450.KQ': 68.5, '439090.KQ': 41.9}

OUT_PATH = os.path.join(ROOT, "scripts", "measurements",
                        "2026-09-11_imminent_score_rank_vs_return.results.json")


# ── 시총(현재) — 프로덕션 fetch_high_marketcap_allowed()는 1000억 이상 목록만
#    주므로 추정에 쓸 수 없어, 같은 naver 모바일 시총 API로 전 종목 시총을 받는다
#    (창 2 scratchpad mcap.py와 같은 호출). marketValue 단위 = 억원.
def fetch_mcap_now():
    out = {}
    for market, suf in (("KOSPI", ".KS"), ("KOSDAQ", ".KQ")):
        page = 1
        while True:
            r = requests.get(naver_kr._MSTOCK_MARKETVALUE_URL.format(market=market),
                             params={"page": page, "pageSize": 100},
                             headers=naver_kr._HEADERS, timeout=10)
            d = r.json()
            st = d.get("stocks") or []
            if not st:
                break
            for s in st:
                try:
                    mv = float(str(s.get("marketValue")).replace(",", ""))
                    cl = float(str(s.get("closePrice")).replace(",", ""))
                except Exception:
                    continue
                if mv > 0 and cl > 0:
                    out[s["itemCode"] + suf] = (mv, cl)
            page += 1
            time.sleep(0.1)
            if (page - 1) * 100 >= (d.get("totalCount") or 0):
                break
    return out


def load_data():
    cache = os.environ.get("MEAS_CACHE")
    if cache and os.path.exists(cache):
        print(f"[cache] load {cache}", flush=True)
        return pickle.load(open(cache, "rb"))
    data, kr_u, _ = harness.fetch_universe_data(markets=("kr",), kr_days=1900,
                                                 validate_offsets=OFFSETS)
    anchor_univ = anchor_universe()
    extra = [t for t in anchor_univ if t not in data]
    for t in extra:
        _, df = harness._fetch_kr_one(t, 1900)
        if df is not None:
            data[t] = df
    bench = harness.fetch_kr_benchmarks(days=1900)
    mcap = fetch_mcap_now()
    blob = {"data": data, "kr_u": dict(kr_u), "bench": bench, "mcap": mcap,
            "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "n_extra_anchor": len(extra)}
    if cache:
        pickle.dump(blob, open(cache, "wb"))
    return blob


def anchor_universe():
    """2026-09-04 시점 프로덕션 get_universe("kr") 재현: 정적 KR_UNIVERSE +
    그날 EOD 동적 파일 + (현재) 워치리스트 KR."""
    dyn = json.load(open(os.path.join(ROOT, ANCHOR_UNIV_FILE)))
    wl = {t: n for t, n in universe_mod.load_watchlist().items() if t.endswith((".KS", ".KQ"))}
    return {**universe_mod.KR_UNIVERSE, **dyn, **wl}


def bench_scores(bench, cp):
    out = {}
    for k in ("kospi", "kosdaq"):
        s = bench[k]["Close"].dropna().loc[:cp]
        assert len(s) and s.index[-1] == cp, f"bench {k} 마지막 날짜 {s.index[-1] if len(s) else None} != {cp}"
        assert len(s) >= BENCH_MIN_BARS, f"bench {k} {len(s)}봉 < {BENCH_MIN_BARS} @ {cp}"
        sc = rs_raw_score(s)
        assert sc is not None
        out[k] = sc
    return out


def scan_at(blob, cp, tickers, cal, want_future=True, full_clean=None):
    """cp_date 하나에서 프로덕션 스캔 재현. 반환: (hits, universe_rets, stats)."""
    data, mcap = blob["data"], blob["mcap"]
    lo_date = cp - pd.Timedelta(days=PROD_WINDOW_DAYS)
    pos = cal.get_loc(cp)
    t5 = cal[pos + T_PLUS] if want_future and pos + T_PLUS < len(cal) else None
    if want_future:
        assert t5 is not None and t5 > cp
    b = bench_scores(blob["bench"], cp)

    st = {"n_tickers": len(tickers), "no_data": 0, "no_bar_on_cp": 0, "invalid_last_bar": 0,
          "mcap_dropped": 0, "mcap_unknown": 0, "full_clean_diff": 0}
    cache = {}
    for t in tickers:
        df = data.get(t)
        if df is None:
            st["no_data"] += 1
            continue
        tr = df.loc[(df.index >= lo_date) & (df.index <= cp)]
        if tr.empty or tr.index[-1] != cp:
            st["no_bar_on_cp"] += 1
            continue
        cl = harness.clean_at_checkpoint(tr)
        if cl is None or cl.empty or cl.index[-1] != cp:
            st["invalid_last_bar"] += 1
            continue
        if full_clean is not None:   # 1.7-6 진단: "전체 정제 후 truncate"와 다른가
            fc = full_clean.get(t)
            fcw = fc.loc[(fc.index >= lo_date) & (fc.index <= cp)] if fc is not None else None
            if fcw is None or len(fcw) != len(cl):
                st["full_clean_diff"] += 1
        m = mcap.get(t)
        if m is None:
            st["mcap_unknown"] += 1   # fail-open (프로덕션과 같은 방향)
        else:
            est = m[0] * float(cl["Close"].iloc[-1]) / m[1]
            if est < MCAP_MIN_EOK:
                st["mcap_dropped"] += 1
                continue
        cache[t] = cl

    # 룩어헤드 assert 1·3 — RS 입력 전체
    for t, h in cache.items():
        assert h.index.max() == cp, f"lookahead: {t} {h.index.max()} > {cp}"
    rs_ranks, rs_moms = harness.compute_rs_at_checkpoint(cache, b["kospi"], b["kosdaq"])

    hits, uni = [], []
    st.update({"price_frozen": 0, "liq_dropped": 0, "t5_missing_hit": 0})
    for t, h in cache.items():
        raw = data[t]
        c0 = float(raw.at[cp, "Close"])
        ret5, c5 = None, None
        if t5 is not None and t5 in raw.index:
            c5 = float(raw.at[t5, "Close"])
            ret5 = c5 / c0 - 1 if c0 > 0 else None
        if len(h) >= IMMINENT_CONFIG["min_bars"]:
            vi = volume_info(float(h["Close"].iloc[-1]), h["Volume"])
            if harness.passes_liquidity_filter({"avg_turnover": vi["avg_turnover"]}, True) and ret5 is not None:
                uni.append(ret5)
        r = analyze_imminent(h, rs_rank=rs_ranks.get(t), rs_mom=rs_moms.get(t),
                             cfg=IMMINENT_CONFIG, is_kr=True)
        if r is None:
            continue
        if r.get("price_frozen"):
            st["price_frozen"] += 1
            continue
        if not harness.passes_liquidity_filter(r, True):
            st["liq_dropped"] += 1
            continue
        # 룩어헤드 assert 2 — analyze가 cp 종가를 봤는지
        assert r["close"] == round(float(h["Close"].iloc[-1]), 2), (t, r["close"])
        assert abs(r["close"] / c0 - 1) < 1e-4, (t, r["close"], c0)
        if want_future and ret5 is None:
            st["t5_missing_hit"] += 1
        hits.append({
            "ticker": t, "score": r["score"], "triggered": bool(r.get("triggered")),
            "late_level": r.get("late_level"), "vol_ratio": r.get("vol_ratio"),
            "rs": r.get("rs"), "pivot_dist_pct": r.get("pivot_dist_pct"),
            "close_T": c0, "date_T5": str(t5.date()) if t5 is not None else None,
            "close_T5": c5, "ret5": ret5,
        })
    return hits, uni, st


# ── 통계 (scipy 없음 — pandas rank + numpy) ─────────────────────────────
def _rank_pct(x):
    s = pd.Series(x, dtype=float)
    return (s.rank(method="average") / len(s)).to_numpy()


def pearson(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 3 or a.std() == 0 or b.std() == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def assign_ranks(hits, key):
    """hits(ret5 유효분만)에 score 순위/백분위/분위 부여. key: 정렬 튜플 함수(큰 게 1위)."""
    n = len(hits)
    kv = pd.Series([key(h) for h in hits], dtype=float)
    rank = kv.rank(ascending=False, method="average").to_numpy()   # 1=최고
    out = []
    for h, rk in zip(hits, rank):
        p = (rk - 0.5) / n
        out.append({**h, "rank": float(rk), "rank_pct": float(p), "quintile": int(min(math.floor(p * 5), 4) + 1)})
    return out


def pooled_rho(groups, key):
    """groups: [hits(ret5 유효)] 체크포인트별. 체크포인트 내부 순위백분위 → 풀링 상관.
    반환 ρ(score 방향, ret5)."""
    xs, ys = [], []
    for g in groups:
        xs.extend(_rank_pct([key(h) for h in g]))
        ys.extend(_rank_pct([h["ret5"] for h in g]))
    return pearson(xs, ys), len(xs)


def bucket_stats(rows):
    r = np.array([h["ret5"] for h in rows], float)
    if len(r) == 0:
        return {"n": 0}
    return {"n": int(len(r)), "mean": float(r.mean()), "median": float(np.median(r)),
            "win_rate": float((r > 0).mean()), "p_ge_+10": float((r >= 0.10).mean()),
            "p_le_-5": float((r <= -0.05).mean())}


# 화면순서 키: run_scan()의 (triggered, setup_score or score) — imminent는 setup_score=None
def key_score(h):
    return h["score"]


def key_display(h):
    return (1 if h["triggered"] else 0) * 1000 + h["score"]   # score≤100이라 triggered 우선과 동치


def run_anchor(blob, cal):
    cp = pd.Timestamp(ANCHOR_DATE)
    res = {}
    for tag, tickers in (("a1_eod_universe", list(anchor_universe().keys())),
                         ("a2_current_universe", list(blob["kr_u"].keys()))):
        hits, _, st = scan_at(blob, cp, tickers, cal, want_future=False)
        P = {h["ticker"] for h in hits}
        A, B = set(ANCHOR_A), set(ANCHOR_B)
        inter = P & A
        jac = len(inter) / len(P | A) if (P | A) else None
        b_in_a = sorted(B & A)
        sc = {h["ticker"]: h["score"] for h in hits}
        diffs = {t: round(sc[t] - RECON_SCORES[t], 2) for t in inter if t in RECON_SCORES}
        res[tag] = {
            "n_pipeline": len(P), "n_anchor_a": len(A), "n_intersection": len(inter),
            "jaccard": jac, "pipeline_only": sorted(P - A), "anchor_only": sorted(A - P),
            "anchor_b_in_a": b_in_a, "anchor_b_in_a_missing": sorted(set(b_in_a) - P),
            "anchor_b_not_in_a_in_pipeline": sorted((B - A) & P),
            "score_match_rate_le_0.1": (sum(1 for d in diffs.values() if abs(d) <= 0.1) / len(diffs)) if diffs else None,
            "score_diffs_nonzero": {t: d for t, d in diffs.items() if abs(d) > 0.1},
            "stats": st,
        }
    a1 = res["a1_eod_universe"]
    a1_pass = (a1["jaccard"] is not None and a1["jaccard"] >= ANCHOR_JACCARD_MIN
               and not a1["anchor_b_in_a_missing"])
    res["gate_pass"] = bool(a1_pass)
    return res


def main():
    anchor_only = "--anchor-only" in sys.argv
    t0 = time.time()
    blob = load_data()
    cal = blob["bench"]["kospi"]["Close"].dropna().index
    assert cal.is_monotonic_increasing and cal.is_unique
    print(f"[data] {len(blob['data'])} tickers, kr_u={len(blob['kr_u'])}, mcap={len(blob['mcap'])}, "
          f"cal {cal[0].date()}~{cal[-1].date()} ({len(cal)}), window={PROD_WINDOW_DAYS}d, "
          f"fetched_at={blob.get('fetched_at')}, {time.time()-t0:.0f}s", flush=True)

    out = {"meta": {"prereg": "docs/imminent_score_rank_vs_return.md §1 (e9c9395)",
                    "fetched_at": blob.get("fetched_at"), "prod_window_days": PROD_WINDOW_DAYS,
                    "mcap_min_eok": MCAP_MIN_EOK, "offsets": OFFSETS}}
    anchor = run_anchor(blob, cal)
    out["anchor"] = anchor
    a1 = anchor["a1_eod_universe"]
    print(f"[anchor a1] n={a1['n_pipeline']} ∩A={a1['n_intersection']}/{a1['n_anchor_a']} "
          f"J={a1['jaccard']:.3f} only_pipe={a1['pipeline_only']} only_A={a1['anchor_only']} "
          f"B-missing={a1['anchor_b_in_a_missing']} score_match={a1['score_match_rate_le_0.1']} "
          f"-> gate={'PASS' if anchor['gate_pass'] else 'FAIL'}", flush=True)
    a2 = anchor["a2_current_universe"]
    print(f"[anchor a2 ref] n={a2['n_pipeline']} ∩A={a2['n_intersection']} J={a2['jaccard']:.3f} "
          f"only_pipe={a2['pipeline_only']} only_A={a2['anchor_only']}", flush=True)
    if anchor_only or not anchor["gate_pass"]:
        out["verdict"] = None if anchor_only else "무효(앵커 게이트 실패)"
        json.dump(out, open(OUT_PATH, "w"), ensure_ascii=False, indent=1, default=str)
        print("[stop] anchor-only" if anchor_only else "[stop] 앵커 게이트 실패 — 90cp 미실행", flush=True)
        return

    # ── 90cp 본 실행 ──
    tickers = list(blob["kr_u"].keys())
    full_clean = {t: harness.clean_at_checkpoint(blob["data"][t]) for t in tickers if t in blob["data"]}
    cps, all_hits, uni_all, uni_med_by_cp = [], [], [], {}
    for i, off in enumerate(OFFSETS):
        cp = cal[-(off + 1)]
        hits, uni, st = scan_at(blob, cp, tickers, cal, want_future=True, full_clean=full_clean)
        valid = [h for h in hits if h["ret5"] is not None]
        ranked = assign_ranks(valid, key_score) if valid else []
        for h in ranked:
            h.update({"off": off, "cp_date": str(cp.date())})
        rho_cp = None
        if len(valid) >= MIN_HITS_FOR_RANK:
            rho_cp = pearson(_rank_pct([h["score"] for h in valid]), _rank_pct([h["ret5"] for h in valid]))
        uni_med = float(np.median(uni)) if uni else None
        uni_med_by_cp[off] = uni_med
        cps.append({"off": off, "cp_date": str(cp.date()), "n_universe_liq": len(uni),
                    "n_hits_kr": len(hits), "n_hits_valid": len(valid), "rho_cp": rho_cp,
                    "universe_ret5_median": uni_med,
                    "hits_ret5_median": float(np.median([h["ret5"] for h in valid])) if valid else None,
                    **st})
        all_hits.extend(ranked)
        uni_all.extend(uni)
        print(f"[cp {i+1}/90] off={off} {cp.date()} hits={len(hits)} valid={len(valid)} "
              f"rho={rho_cp if rho_cp is None else round(rho_cp,3)} uni={len(uni)} "
              f"{time.time()-t0:.0f}s", flush=True)
    out["checkpoints"] = cps
    out["hits"] = all_hits

    # ── 요약 ──
    by_cp = {}
    for h in all_hits:
        by_cp.setdefault(h["off"], []).append(h)
    elig = {off: g for off, g in by_cp.items() if len(g) >= MIN_HITS_FOR_RANK}
    elig_hits = [h for g in elig.values() for h in g]

    rho, n_rho = pooled_rho(list(elig.values()), key_score)
    rho_recent, n_recent = pooled_rho([g for off, g in elig.items() if off in RECENT_OFFSETS], key_score)
    rho_older, n_older = pooled_rho([g for off, g in elig.items() if off in OLDER_OFFSETS], key_score)
    rho_disp, _ = pooled_rho(list(elig.values()), key_display)
    cp_rhos = [c["rho_cp"] for c in cps if c["rho_cp"] is not None]
    t_cp = (np.mean(cp_rhos) / (np.std(cp_rhos, ddof=1) / math.sqrt(len(cp_rhos)))) if len(cp_rhos) >= 3 else None

    quint = {q: bucket_stats([h for h in elig_hits if h["quintile"] == q]) for q in range(1, 6)}
    disp_ranked = []
    for g in elig.values():
        disp_ranked.extend(assign_ranks(g, key_display))
    quint_disp = {q: bucket_stats([h for h in disp_ranked if h["quintile"] == q]) for q in range(1, 6)}
    late = {lv: bucket_stats([h for h in elig_hits if (h["late_level"] or "none") == lv])
            for lv in sorted({(h["late_level"] or "none") for h in elig_hits})}
    caution_by_q = {q: (sum(1 for h in elig_hits if h["quintile"] == q and h["late_level"] == "caution")
                        / max(1, quint[q]["n"])) for q in range(1, 6)}

    all_valid_rets = [h["ret5"] for h in all_hits]
    hit_med = float(np.median(all_valid_rets)) if all_valid_rets else None
    uni_med = float(np.median(uni_all)) if uni_all else None
    gap = hit_med - uni_med if (hit_med is not None and uni_med is not None) else None
    eq_w_gaps = [c["hits_ret5_median"] - c["universe_ret5_median"] for c in cps
                 if c["hits_ret5_median"] is not None and c["universe_ret5_median"] is not None]
    n_hits_total = sum(c["n_hits_kr"] for c in cps)
    n_t5_missing = sum(c["t5_missing_hit"] for c in cps)

    # ── 판정 (사전등록 1.5, 위에서부터) ──
    validity_ok = gap is not None and abs(gap) <= VALIDITY_MAX_GAP
    min_q_n = min(quint[q]["n"] for q in range(1, 6))
    if not validity_ok:
        verdict = "무효(유효성 게이트 실패)"
    elif min_q_n < QUINTILE_MIN_N:
        verdict = "판정불가(분위 n<100)"
    elif rho is None or abs(rho) < RHO_MIN or rho_recent is None or rho_older is None \
            or (math.copysign(1, rho_recent) != math.copysign(1, rho_older)):
        verdict = "무의미(score 순위는 정렬 기준으로 정보 없음)"
    elif math.copysign(1, rho_recent) == math.copysign(1, rho) == math.copysign(1, rho_older):
        verdict = "정보 있음 — " + ("순상관" if rho > 0 else "역상관")
    else:
        verdict = "무의미(score 순위는 정렬 기준으로 정보 없음)"

    out["summary"] = {
        "n_hits_total": n_hits_total, "n_hits_valid": len(all_hits),
        "n_t5_missing": n_t5_missing,
        "t5_missing_rate": n_t5_missing / n_hits_total if n_hits_total else None,
        "t5_missing_warn": (n_t5_missing / n_hits_total > T5_MISSING_WARN) if n_hits_total else None,
        "n_cp_eligible": len(elig), "n_cp_excluded_lt5": len(OFFSETS) - len(elig),
        "hits_per_cp_mean": n_hits_total / len(OFFSETS),
        "rho_pooled": rho, "n_rho": n_rho,
        "rho_pooled_z_naive": rho * math.sqrt(n_rho - 1) if rho is not None else None,
        "rho_recent_half": rho_recent, "n_recent": n_recent,
        "rho_older_half": rho_older, "n_older": n_older,
        "rho_cp_mean": float(np.mean(cp_rhos)) if cp_rhos else None,
        "rho_cp_median": float(np.median(cp_rhos)) if cp_rhos else None,
        "rho_cp_pos_frac": float(np.mean([r > 0 for r in cp_rhos])) if cp_rhos else None,
        "rho_cp_t": t_cp, "n_cp_rho": len(cp_rhos),
        "quintiles_score": quint,
        "ref_rho_display_order": rho_disp, "ref_quintiles_display_order": quint_disp,
        "ref_late_level": late, "ref_caution_rate_by_quintile": caution_by_q,
        "validity": {"hit_ret5_median": hit_med, "universe_ret5_median": uni_med, "gap": gap,
                     "eq_weight_gap_median": float(np.median(eq_w_gaps)) if eq_w_gaps else None,
                     "n_universe_obs": len(uni_all), "pass": validity_ok},
        "diag": {k: sum(c[k] for c in cps) for k in ("no_data", "no_bar_on_cp", "invalid_last_bar",
                                                    "mcap_dropped", "mcap_unknown", "full_clean_diff",
                                                    "price_frozen", "liq_dropped")},
        "min_quintile_n": min_q_n,
        "verdict": verdict,
    }
    out["verdict"] = verdict
    json.dump(out, open(OUT_PATH, "w"), ensure_ascii=False, indent=1, default=str)
    s = out["summary"]
    print(json.dumps({k: v for k, v in s.items() if k not in ("quintiles_score", "ref_quintiles_display_order", "ref_late_level")},
                     ensure_ascii=False, indent=1, default=str))
    print("quintiles", json.dumps(quint, ensure_ascii=False))
    print(f"[done] {verdict}  {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
