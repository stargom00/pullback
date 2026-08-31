"""
KR 눌림목 EV≈0의 유동성 편중 가설 검증 — 3분위 분해 (2026-08-31, 사용자
지시, docs/kr_us_market_structure.md §5 후속 측정).

【사전 등록 가설】 "KR 눌림목 EV≈0은 저유동성 편중 때문이다" — 즉 눌림목
셋업 자체가 약한 게 아니라, 히트 표본이 저유동성 종목에 치우쳐 있어서
전체 EV가 깎인다는 가설. 고유동성 종목만 보면 실제로 작동할 수 있다는
뜻.

측정 스크립트만 — scanner.py/app.py 미수정. 공통 하네스(harness.py) 재사용.
n=636 KR 단독 눌림목 히트는 docs/pullback_ev_kr_us_regime_investigation.md에
인용된 기존 수치로, 그 원본 파이프라인(2026-08-26_pullback_ev_cohort_and_
pipeline_diff.py의 collect_hits, analyze()+CONFIG+checkpoints(60,250,10)+
harness.passes_liquidity_filter, KR+US 유니버스에서 KR만 사후분해)과 100%
동일한 방법론으로 오늘 날짜 기준 재수집한다(원본 결과 스냅샷은 README
규칙5에 따라 커밋 안 함 — 재실행하면 재현되는 게 요점). 정확히 n=636이
아닐 수 있음(오늘 유니버스/데이터 상태 반영, 방법론 재현이 핵심이지 표본
크기 고정이 핵심이 아님) — 실제 재현치를 그대로 보고.

규칙6(대조군 유동성매칭): 이 측정은 "대조군 vs 히트" 비교가 아니라 "히트
표본 내부의 유동성 3분위 비교"라 규칙6이 원형 그대로 적용되진 않지만,
정신은 지킨다 — 3분위 경계 자체를 production 유동성 하한(harness.KR/US_
LIQUIDITY_FLOOR) 통과분 안에서만 정의해서, "애초에 스캔 대상이 될 만큼
유동적인 종목들 사이에서" 비교한다(2026-08-31_kr_us_market_structure.py의
liquidity_tier()와 동일 사상).
규칙7(z검정): 상위/하위 3분위 EV 격차는 harness.ev_gap_zscore로 유의성까지
확인한다(격차·단조성만으론 불충분 — README 규칙7 사례 참고).
규칙8(KR/US 미혼합): 전 절 KR/US 완전 분리 보고. 3절이 US 대칭성 확인이라
US도 재는 것이지, KR/US를 섞어 하나의 수치로 내지 않는다.

【조작적 정의 — 3분위 경계】 각 티커의 "현재" 평균거래대금(최근 252거래일
Close*Volume 평균, 2026-08-31_kr_us_market_structure.py의 section_1과 동일
정의)을 계산하고, 각 시장의 production 유동성 하한을 통과하는 종목만 모아
33/67 백분위로 3등분해 경계를 정한다. 이 경계를 각 히트의 (그 히트 시점에
analyze()가 계산한) avg_turnover에 적용해 티어를 매긴다 — 경계 자체는
"현재" 시점 스냅샷이라 히트가 발생한 과거 시점과 완벽히 시점일치하진 않음
(2026-08-31_kr_us_market_structure.py의 동일 단순화를 그대로 따름, 판단
사항으로 아래 보고).

【사전 판정 기준】 KR 눌림목 상위3분위 EV가 하위3분위 대비 +0.15R 이상 &
z>=1.96 -> "KR 눌림목 = 고유동성 한정 사용" 규칙 채택(코드/필터 구현은
이번 태스크 범위 아님, docs 기록만). 미달 -> "KR 눌림목 무용 확정 유지".

실행: 리포 루트에서
  python3 scripts/measurements/2026-08-31_kr_pullback_liquidity_tercile_ev.py
(전체 유니버스 fetch + 20 체크포인트 x 4개 탭(눌림목+돌파계열3개) analyze
호출, kr_us_market_structure.py와 비슷한 비용)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import time
import statistics as stats

import harness
from scanner import (
    analyze, CONFIG,
    analyze_breakout, BREAKOUT_CONFIG,
    analyze_boxbreak, BOXBREAK_CONFIG,
    analyze_turnaround, TURN_CONFIG,
)

OFFSETS = harness.checkpoints(60, 250, 10)
SPECS = [
    ("눌림목", analyze, CONFIG),
    ("돌파", analyze_breakout, BREAKOUT_CONFIG),
    ("박스돌파", analyze_boxbreak, BOXBREAK_CONFIG),
    ("추세전환", analyze_turnaround, TURN_CONFIG),
]
MIN_BARS_FLOOR = max(cfg["min_bars"] for _, _, cfg in SPECS)


def is_kr(t):
    return harness.is_kr_ticker(t)


def current_avg_turnover(df):
    if len(df) < 60:
        return None
    return float((df["Close"] * df["Volume"]).iloc[-252:].mean())


def compute_tercile_bounds(data):
    """시장별: production 유동성 하한 통과분의 '현재' turnover를 33/67
    백분위로 3등분. 반환: {"kr": (t1,t2), "us": (t1,t2)}."""
    bounds = {}
    for ikr, key in ((True, "kr"), (False, "us")):
        floor_ = harness.KR_LIQUIDITY_FLOOR if ikr else harness.US_LIQUIDITY_FLOOR
        vals = []
        for t, df in data.items():
            if is_kr(t) != ikr:
                continue
            at = current_avg_turnover(df)
            if at is not None and at >= floor_:
                vals.append(at)
        vals.sort()
        n = len(vals)
        if n < 3:
            bounds[key] = (None, None)
            continue
        t1 = vals[n // 3]
        t2 = vals[(2 * n) // 3]
        bounds[key] = (t1, t2)
        print(f"[tercile-bounds] {key}: n_liquid={n} t1(33%)={t1:,.0f} t2(67%)={t2:,.0f}", flush=True)
    return bounds


def tercile_of(avg_turn, ikr, bounds):
    key = "kr" if ikr else "us"
    t1, t2 = bounds[key]
    if avg_turn is None or t1 is None:
        return None
    if avg_turn < t1:
        return "bottom"
    if avg_turn < t2:
        return "mid"
    return "top"


def collect_all_hits(data, bench, bounds):
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None
    tickers = list(data.keys())
    hits = []
    t0 = time.time()
    for oi, off in enumerate(OFFSETS):
        b_kospi = harness.bench_score_at(kospi_close, off)
        b_kosdaq = harness.bench_score_at(kosdaq_close, off)
        trunc_cache = {}
        for t in tickers:
            df = data[t]
            if len(df) - off < MIN_BARS_FLOOR:
                continue
            trunc_cache[t] = harness.truncate_at(df, off)
        rs_ranks, rs_moms = harness.compute_rs_at_checkpoint(trunc_cache, b_kospi, b_kosdaq)

        for t, hist in trunc_cache.items():
            ikr = is_kr(t)
            rr, rm = rs_ranks.get(t), rs_moms.get(t)
            for label, fn, cfg in SPECS:
                if len(data[t]) - off < cfg["min_bars"]:
                    continue
                try:
                    hit = fn(hist, rs_rank=rr, rs_mom=rm, cfg=cfg, is_kr=ikr)
                except Exception:
                    continue
                if hit is None or not harness.passes_liquidity_filter(hit, ikr):
                    continue
                outcome = harness.race(hit.get("close"), hit.get("stop"), harness.future_after(data[t], off))
                hits.append({
                    "ticker": t, "off": off, "tab": label, "market": "KR" if ikr else "US",
                    "avg_turnover": hit.get("avg_turnover"),
                    "tercile": tercile_of(hit.get("avg_turnover"), ikr, bounds),
                    "outcome": outcome,
                })
        print(f"[collect] off={off} ({oi+1}/{len(OFFSETS)}) hits_so_far={len(hits)} "
              f"elapsed={time.time()-t0:.0f}s", flush=True)
    return hits


def ev_of(hits):
    return harness.ev_summary([h["outcome"] for h in hits])


def tercile_breakdown(hits):
    out = {}
    for tier in ("bottom", "mid", "top"):
        sub = [h for h in hits if h["tercile"] == tier]
        out[tier] = {"n": len(sub), **ev_of(sub)}
    return out


def composition(hits):
    n = len(hits)
    if n == 0:
        return {}
    out = {}
    for tier in ("bottom", "mid", "top"):
        k = sum(1 for h in hits if h["tercile"] == tier)
        out[tier] = (k / n, k)
    out["unclassified"] = sum(1 for h in hits if h["tercile"] is None)
    return out


if __name__ == "__main__":
    _t0 = time.time()
    data, kr_u, us_u = harness.fetch_universe_data()
    bench = harness.fetch_kr_benchmarks()

    print("\n" + "=" * 70)
    print("3분위 경계 계산 (production 유동성 하한 통과분, 현재 turnover 기준)")
    print("=" * 70)
    bounds = compute_tercile_bounds(data)

    print("\n" + "=" * 70)
    print("히트 수집 (눌림목 + 돌파계열 3개, KR+US, checkpoints 60~250)")
    print("=" * 70)
    all_hits = collect_all_hits(data, bench, bounds)

    kr_pb = [h for h in all_hits if h["tab"] == "눌림목" and h["market"] == "KR"]
    us_pb = [h for h in all_hits if h["tab"] == "눌림목" and h["market"] == "US"]
    kr_fam = [h for h in all_hits if h["tab"] in ("돌파", "박스돌파", "추세전환") and h["market"] == "KR"]

    print("\n" + "=" * 70)
    print("1절: KR 눌림목 히트 유동성 3분위 구성비")
    print("=" * 70)
    comp = composition(kr_pb)
    print(f"  n_total={len(kr_pb)} composition={comp}")

    print("\n" + "=" * 70)
    print("2절: KR 눌림목 3분위별 EV/승률/손절률")
    print("=" * 70)
    kr_pb_tiers = tercile_breakdown(kr_pb)
    for k, v in kr_pb_tiers.items():
        print(f"  {k}: {v}")
    z_kr, sig_kr = harness.ev_gap_zscore(kr_pb_tiers["bottom"], kr_pb_tiers["top"])
    print(f"  bottom vs top z={z_kr} significant={sig_kr}")

    print("\n" + "=" * 70)
    print("3절: US 눌림목 3분위별 EV (대칭성 확인)")
    print("=" * 70)
    us_pb_tiers = tercile_breakdown(us_pb)
    for k, v in us_pb_tiers.items():
        print(f"  {k}: {v}")
    z_us, sig_us = harness.ev_gap_zscore(us_pb_tiers["bottom"], us_pb_tiers["top"])
    print(f"  bottom vs top z={z_us} significant={sig_us}")

    print("\n" + "=" * 70)
    print("4절: KR 돌파계열 3분위별 EV (대조: 이미 작동하는 계열도 고유동성 우위인가)")
    print("=" * 70)
    kr_fam_tiers = tercile_breakdown(kr_fam)
    for k, v in kr_fam_tiers.items():
        print(f"  {k}: {v}")
    z_fam, sig_fam = harness.ev_gap_zscore(kr_fam_tiers["bottom"], kr_fam_tiers["top"])
    print(f"  bottom vs top z={z_fam} significant={sig_fam}")

    print("\n" + "=" * 70)
    print("사전 판정")
    print("=" * 70)
    gap = None
    if kr_pb_tiers["top"]["ev_R"] is not None and kr_pb_tiers["bottom"]["ev_R"] is not None:
        gap = kr_pb_tiers["top"]["ev_R"] - kr_pb_tiers["bottom"]["ev_R"]
    verdict = "미확정(데이터 부족)"
    if gap is not None:
        if gap >= 0.15 and sig_kr:
            verdict = "채택 — KR 눌림목 = 고유동성 한정 사용"
        else:
            verdict = "미달 — KR 눌림목 무용 확정 유지"
    print(f"  gap(top-bottom)={gap} z={z_kr} significant={sig_kr} => {verdict}")

    print(f"\n[main] 전체 완료, elapsed={time.time()-_t0:.0f}s", flush=True)

    import json
    with open("/tmp/kr_pullback_liquidity_tercile_result.json", "w") as f:
        json.dump({
            "bounds": bounds,
            "kr_pullback_composition": comp,
            "kr_pullback_tiers": kr_pb_tiers, "kr_pullback_zgap": [z_kr, sig_kr],
            "us_pullback_tiers": us_pb_tiers, "us_pullback_zgap": [z_us, sig_us],
            "kr_family_tiers": kr_fam_tiers, "kr_family_zgap": [z_fam, sig_fam],
            "verdict": verdict, "n_kr_pullback": len(kr_pb), "n_us_pullback": len(us_pb),
            "n_kr_family": len(kr_fam),
        }, f, default=str, indent=2)
    print("[main] 결과 JSON: /tmp/kr_pullback_liquidity_tercile_result.json (커밋 대상 아님, 참고용)")
