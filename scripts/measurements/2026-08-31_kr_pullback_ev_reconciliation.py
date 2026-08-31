"""
KR 눌림목 EV 괴리 정합성 재조사 (2026-08-31, 사용자 지시).

docs/kr_us_strategy_map.md의 0.002R(n=636, 2026-08-26 재현치 인용)와
2026-08-31_kr_pullback_liquidity_tercile_ev.py의 오늘 재현치(n=559,
3분위 가중평균 약 0.17R) 사이의 큰 괴리를 규명한다.

측정 스크립트만 — scanner.py/app.py 미수정. 공통 하네스(harness.py) 재사용.

【확인 1 — 체크포인트/방법론 차이】
두 계보 모두 checkpoints(60,250,10) (harness.py 표준, 20지점) — 아래
grep으로 확인:
- 2026-08-26_pullback_ev_cohort_and_pipeline_diff.py: OFFSETS = checkpoints(60,250,10)
- 2026-08-31_kr_pullback_liquidity_tercile_ev.py: OFFSETS = checkpoints(60,250,10)
동일. 다만 off는 "그 스크립트를 실행한 날" 기준 역산 트레이딩일이므로,
실행일이 다르면(2026-08-26 vs 2026-08-31, 3거래일 차이 — 8/27,28,31)
포함되는 실제 캘린더 구간이 그만큼 앞으로 밀린다 — 완전히 같은 과거
구간이 아니라 최근 3거래일 shift.

【확인 2 — CONFIG 변경 여부】
git blame으로 scanner.py의 pullback CONFIG(v5.71 게이트E/depth_atr 반영)가
2026-08-26 이후 바뀌었는지 확인 (아래 참고).

【핵심 측정 — 월별 EV 시계열】
동일 파이프라인(analyze()+CONFIG+checkpoints(60,250,10)+
passes_liquidity_filter+race, KR만 fetch)으로 오늘 재수집하되, 각 히트의
캘린더 월(신호일 = truncate_at(df,off)의 마지막 행 날짜)을 기록해 월별
EV/n/승률/손절률을 낸다. "무용 확정"이 국면 의존적 인공물인지 실제
연중 안정적 사실인지 직접 답한다.

실행: python3 scripts/measurements/2026-08-31_kr_pullback_ev_reconciliation.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import time
import json
from collections import defaultdict

import harness
from scanner import analyze, CONFIG

OFFSETS = harness.checkpoints(60, 250, 10)
MIN_BARS_FLOOR = CONFIG["min_bars"]


def collect_kr_pullback_hits_with_dates(data, bench):
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None
    tickers = [t for t in data if harness.is_kr_ticker(t)]
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
            rr, rm = rs_ranks.get(t), rs_moms.get(t)
            try:
                hit = analyze(hist, rs_rank=rr, rs_mom=rm, cfg=CONFIG, is_kr=True)
            except Exception:
                continue
            if hit is None or not harness.passes_liquidity_filter(hit, True):
                continue
            outcome = harness.race(hit.get("close"), hit.get("stop"), harness.future_after(data[t], off))
            signal_date = hist.index[-1]
            hits.append({
                "ticker": t, "off": off,
                "date": str(signal_date.date()) if hasattr(signal_date, "date") else str(signal_date),
                "month": (str(signal_date.date())[:7] if hasattr(signal_date, "date") else str(signal_date)[:7]),
                "outcome": outcome,
            })
        print(f"[collect] off={off} ({oi+1}/{len(OFFSETS)}) hits_so_far={len(hits)} "
              f"elapsed={time.time()-t0:.0f}s", flush=True)
    return hits


def monthly_breakdown(hits):
    by_month = defaultdict(list)
    for h in hits:
        by_month[h["month"]].append(h["outcome"])
    out = {}
    for m in sorted(by_month):
        out[m] = {"n": len(by_month[m]), **harness.ev_summary(by_month[m])}
    return out


if __name__ == "__main__":
    _t0 = time.time()
    data, kr_u, _ = harness.fetch_universe_data(markets=("kr",))
    bench = harness.fetch_kr_benchmarks()

    print("\n" + "=" * 70)
    print("KR 눌림목 히트 재수집 (checkpoints 60~250, 날짜 태깅)")
    print("=" * 70)
    hits = collect_kr_pullback_hits_with_dates(data, bench)

    print("\n" + "=" * 70)
    print("전체 EV (오늘 재현)")
    print("=" * 70)
    overall = harness.ev_summary([h["outcome"] for h in hits])
    print(f"  n={len(hits)} {overall}")

    print("\n" + "=" * 70)
    print("월별 EV 시계열")
    print("=" * 70)
    mb = monthly_breakdown(hits)
    for m, v in mb.items():
        print(f"  {m}: {v}")

    # off 반분 비교 (기존 문서 3/6절과 동일 관행 — 최근/이전)
    recent = [h["outcome"] for h in hits if h["off"] <= 150]
    earlier = [h["outcome"] for h in hits if h["off"] > 150]
    ev_recent = harness.ev_summary(recent)
    ev_earlier = harness.ev_summary(earlier)
    z_half, sig_half = harness.ev_gap_zscore(ev_earlier, ev_recent)
    print("\n" + "=" * 70)
    print("반분 비교 (off<=150 최근 vs off>150 이전)")
    print("=" * 70)
    print(f"  최근(off<=150): n={len(recent)} {ev_recent}")
    print(f"  이전(off>150): n={len(earlier)} {ev_earlier}")
    print(f"  gap(최근-이전) z={z_half} significant={sig_half}")

    print(f"\n[main] 전체 완료, elapsed={time.time()-_t0:.0f}s", flush=True)

    with open("/tmp/kr_pullback_ev_reconciliation_result.json", "w") as f:
        json.dump({
            "n_total": len(hits), "overall": overall, "monthly": mb,
            "recent_half": ev_recent, "earlier_half": ev_earlier,
            "z_half": z_half, "sig_half": sig_half,
        }, f, indent=2, default=str)
    print("[main] saved /tmp/kr_pullback_ev_reconciliation_result.json")
