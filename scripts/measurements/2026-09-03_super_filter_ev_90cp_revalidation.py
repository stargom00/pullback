"""
슈퍼대장 필터(눌림목 탭 [👑 슈퍼대장만]) EV 90개 체크포인트 재검증
(2026-09-03, 사용자 지시, README 규칙9).

【배경】GUIDE.md "👑 슈퍼대장 활용법" 우선순위1이 "실측된 모든 필터 조합
중 최강(EV 0.266 vs 무필터 0.172)"이라고 인용하는 원본 수치는 원본
스크립트 소실로 재현 미검증 상태였다(`scripts/measurements/README.md`
2026-08-14 감사표). 그런데 후속 스크립트
`2026-08-26_pullback_ev_cohort_and_pipeline_diff.py`가 이미 공통
하네스 + 실제 `analyze_super()`로 같은 절차("①유니버스 필터로 쓰기")를
재현해뒀다 — 다만 `checkpoints(60,250,10)`=20개뿐이라 README 규칙9
(채택 판정은 90개+ 체크포인트) 미달이라 오늘 확장 재검증한다.

이 스크립트는 위 08-26 스크립트를 복제해 OFFSETS만 90개로 늘린 것 —
히트 판정(analyze+CONFIG, analyze_super+SUPER_CONFIG)·유동성 필터·레이스
전부 원본과 동일(코드 대조는 08-26 스크립트 docstring의 DIFF 표 참고,
이번엔 파이프라인 자체를 바꾸지 않았으므로 재대조 불필요). harness.py는
다른 세션이 GUIDE.md·static/index.html을 작업 중이라는 사용자 지시로
수정하지 않았다(README 규칙3 그대로 재사용만).

【20개창 vs 90개창 나란히 비교】한 번의 fetch/수집으로 두 창을 동시에
낸다 — `checkpoints(60,250,10)`(20개)이 `checkpoints(60,950,10)`(90개)의
앞쪽 20개 원소와 정확히 일치하므로(둘 다 시작60·간격10), 90개 수집
결과에서 off<=250인 히트만 걸러내면 20개창 결과와 100% 동일하다(같은
fetch·같은 날짜 데이터라 08-26 스크립트를 오늘 그대로 재실행한 것보다
더 엄격한 비교 — 측정일 차이가 섞이지 않는다).

채택 판정 기준(README 규칙9): 20개창에서 이미 참고했던 "슈퍼대장 소속
EV가 무필터보다 높다"는 방향이, 90개창(z검정 포함, 규칙7)에서도
유지되는지로 판단. 유지 → GUIDE 캐비어트("재현 미검증") 제거 가능.
무너짐(격차 축소/역전 또는 유의성 상실) → GUIDE의 "가장 효과적인 필터"
서술 자체를 재검토 대상으로 격상.

KR/US 분해 병기(README 규칙8) — 08-26 스크립트가 이미 KR 단독에서
방향 역전(-0.214R)을 확인해뒀으므로 이번에도 필수로 같이 낸다.

실행: 리포 루트에서
`python3 scripts/measurements/2026-09-03_super_filter_ev_90cp_revalidation.py`
(확장 fetch 포함 총 10~15분 예상, 네트워크 필요).
"""
import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import harness
from scanner import analyze, CONFIG, analyze_super, SUPER_CONFIG

OFFSETS = harness.checkpoints(60, 950, 10)   # 90개 — README 규칙9 표준
OFFSETS_20 = set(harness.checkpoints(60, 250, 10))   # 08-26 원측정과 동일 창(OFFSETS의 앞 20개와 일치)
MIN_BARS_FLOOR = max(CONFIG["min_bars"], SUPER_CONFIG["min_bars"])
GAP_MIN_R = 0.05   # 참고용(README에 사전등록된 문턱 아님) — 방향/유의성 판정이 핵심


def collect_hits(data, bench):
    """08-26 스크립트의 collect_hits와 로직 100% 동일, OFFSETS만 90개."""
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
            ikr = harness.is_kr_ticker(t)
            rr = rs_ranks.get(t)
            rm = rs_moms.get(t)
            try:
                hit = analyze(hist, rs_rank=rr, rs_mom=rm, cfg=CONFIG, is_kr=ikr)
            except Exception:
                continue
            if hit is None:
                continue
            if not harness.passes_liquidity_filter(hit, ikr):
                continue
            try:
                is_super = analyze_super(hist, rs_rank=rr, rs_mom=rm, is_kr=ikr) is not None
            except Exception:
                is_super = False
            outcome = harness.race(hit.get("close"), hit.get("stop"), harness.future_after(data[t], off))
            hits.append({
                "ticker": t, "off": off, "market": "KR" if ikr else "US",
                "is_super": is_super, "outcome": outcome,
            })
        print(f"[collect] off={off} done ({oi+1}/{len(OFFSETS)}) elapsed={time.time()-t0:.0f}s "
              f"hits_so_far={len(hits)}", flush=True)
    return hits


def ev_of(hits):
    return harness.ev_summary([h["outcome"] for h in hits])


def window_report(hits):
    """주어진 히트 서브셋(이미 창으로 필터링됨)에 대해 무필터/소속/비소속
    + KR/US 분해 + 소속 vs 무필터 z검정(README 규칙7, harness.ev_gap_zscore
    재사용 — 두 그룹은 서로 겹치는 부분집합이 아니라 완전 별도 그룹 비교가
    아님을 주의: '소속' vs '무필터(전체)'는 소속이 전체의 부분집합이라
    엄밀히는 독립표본이 아니다. 다만 08-26 원측정도 같은 비교를 썼고,
    소속 비중이 전체 대비 작아(아래 실측 n 참고) 근사 오차가 작다고
    판단 — 완전히 분리된 비교가 필요하면 '소속 vs 비소속'을 대신 참고
    (아래에 별도로 병기)."""
    all_ev = ev_of(hits)
    super_hits = [h for h in hits if h["is_super"]]
    nonsuper_hits = [h for h in hits if not h["is_super"]]
    super_ev = ev_of(super_hits)
    nonsuper_ev = ev_of(nonsuper_hits)

    z_vs_all, sig_vs_all = harness.ev_gap_zscore(all_ev, super_ev)
    z_vs_nonsuper, sig_vs_nonsuper = harness.ev_gap_zscore(nonsuper_ev, super_ev)

    kr_super = [h for h in super_hits if h["market"] == "KR"]
    us_super = [h for h in super_hits if h["market"] == "US"]

    return {
        "무필터_전체": all_ev,
        "슈퍼대장_소속": super_ev,
        "슈퍼대장_비소속": nonsuper_ev,
        "소속_vs_무필터": {
            "gap_R": None if (super_ev["ev_R"] is None or all_ev["ev_R"] is None)
            else round(super_ev["ev_R"] - all_ev["ev_R"], 4),
            "z": z_vs_all, "significant": sig_vs_all,
        },
        "소속_vs_비소속(엄밀한 독립비교)": {
            "gap_R": None if (super_ev["ev_R"] is None or nonsuper_ev["ev_R"] is None)
            else round(super_ev["ev_R"] - nonsuper_ev["ev_R"], 4),
            "z": z_vs_nonsuper, "significant": sig_vs_nonsuper,
        },
        "소속_KR단독": ev_of(kr_super),
        "소속_US단독": ev_of(us_super),
    }


def run(data, bench, out_path=None):
    hits = collect_hits(data, bench)
    hits_20 = [h for h in hits if h["off"] in OFFSETS_20]

    report_90 = window_report(hits)
    report_20 = window_report(hits_20)

    print("\n=== 20개창(off60~250, 08-26 원측정과 동일 창) ===", flush=True)
    print(json.dumps(report_20, ensure_ascii=False, indent=2), flush=True)
    print("\n=== 90개창(off60~950, README 규칙9 표준) ===", flush=True)
    print(json.dumps(report_90, ensure_ascii=False, indent=2), flush=True)

    # ── 나란히 비교 표 (콘솔용) ──
    def _row(label, get):
        r20, r90 = get(report_20), get(report_90)
        return (f"| {label} | {r20.get('ev_R')} (n={r20.get('n_hits', r20.get('nv'))}) "
                f"| {r90.get('ev_R')} (n={r90.get('n_hits', r90.get('nv'))}) |")

    print("\n| 항목 | 20개창 EV(n) | 90개창 EV(n) |", flush=True)
    print("|---|---|---|", flush=True)
    print(_row("무필터(전체)", lambda r: r["무필터_전체"]), flush=True)
    print(_row("슈퍼대장 소속", lambda r: r["슈퍼대장_소속"]), flush=True)
    print(_row("슈퍼대장 비소속", lambda r: r["슈퍼대장_비소속"]), flush=True)
    print(_row("소속 KR단독", lambda r: r["소속_KR단독"]), flush=True)
    print(_row("소속 US단독", lambda r: r["소속_US단독"]), flush=True)

    gap20 = report_20["소속_vs_무필터"]["gap_R"]
    gap90 = report_90["소속_vs_무필터"]["gap_R"]
    z90 = report_90["소속_vs_무필터"]["z"]
    sig90 = report_90["소속_vs_무필터"]["significant"]
    z90_ns = report_90["소속_vs_비소속(엄밀한 독립비교)"]["z"]
    sig90_ns = report_90["소속_vs_비소속(엄밀한 독립비교)"]["significant"]

    print(f"\n[격차(소속-무필터)] 20개창={gap20} → 90개창={gap90}, z(90)={z90} significant={sig90}", flush=True)
    print(f"[소속 vs 비소속(독립비교), 90개창] z={z90_ns} significant={sig90_ns}", flush=True)

    kr_super_ev = report_90["소속_KR단독"]["ev_R"]
    us_super_ev = report_90["소속_US단독"]["ev_R"]
    if gap90 is not None and gap90 > 0 and sig90:
        if kr_super_ev is not None and kr_super_ev < 0:
            verdict = ("90개창에서도 방향 유지(소속>무필터, 유의) — 단 KR 단독은 여전히 음수라 "
                       "GUIDE의 'KR 종목 주의' 캐비어트는 유지, '재현 미검증' 캐비어트만 제거 검토 대상")
        else:
            verdict = "90개창에서 방향+유의성 모두 유지 — GUIDE 캐비어트('재현 미검증') 제거 검토 가능"
    else:
        verdict = "90개창에서 격차 축소/역전 또는 유의성 상실 — 채택 철회 검토, GUIDE '가장 효과적인 필터' 서술 재검토 대상"
    print(f"\n[최종판정] {verdict}", flush=True)

    result = {"offsets_90": f"{OFFSETS[0]}..{OFFSETS[-1]} step10 ({len(OFFSETS)}개)",
              "20개창": report_20, "90개창": report_90, "판정": verdict}
    if out_path:
        with open(out_path, "w") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"SAVED report to {out_path}", flush=True)
    return result


if __name__ == "__main__":
    data, kr_u, us_u = harness.fetch_universe_data(
        kr_days=1900, us_period="5y", validate_offsets=OFFSETS)
    bench = harness.fetch_kr_benchmarks()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "2026-09-03_super_filter_ev_90cp_revalidation.results.json")
    run(data, bench, out_path=out)
