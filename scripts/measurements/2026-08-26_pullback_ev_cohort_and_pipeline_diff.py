"""
수급 캠페인 부수 발견 정합성 조사 (2026-08-26, 사용자 지시, 우선순위 높음).
scanner.py는 전혀 수정하지 않는다.

【배경 — 무엇이 충돌하는가】
2026-08-25 기관/외국인 수급 캠페인(institutional_flow_pullback_ev)에서
KR 단독 눌림목 히트 636건의 전체 EV가 +0.008R로 나왔다. 이는 두 기존
수치와 충돌한다:
  (a) 눌림목 전체 EV 0.291R — `2026-08-14_pullback_stop_width_and_entry_timing.py`
      (재현 가능, 커밋돼 있음). 단 이 스크립트는 **KR+US combined**로
      돌았다(harness.fetch_universe_data() 기본 markets=("kr","us")).
  (b) 슈퍼대장 소속 눌림목 EV +0.266R(n=320) — `super_pullback_prefilter_check.py`
      (원본 스크립트 소실, README 감사 표에 "재현 불가"로 이미 기록돼
      있던 수치 — 이번이 처음 문제 삼는 게 아니라 원래도 불신 대상이었음).
      수급 캠페인에서 KR 단독 재현 시 -0.214R(n=103)로 정반대 부호.

【이번 조사 3갈래】
1. (a)와의 정합성: (a)를 KR/US로 사후분해해서 KR 단독이 이번 +0.008R과
   맞는지 확인 — "US가 견인" 가설.
2. 파이프라인 diff: 어제(2026-08-25) 수급 스크립트의 히트 추출 로직과
   (a) 스크립트의 히트 추출 로직을 코드 레벨로 대조 (아래 DIFF 섹션,
   docs에 표로 옮김).
3. (b)와의 정합성: 원본 소실이라 직접 재현은 불가 — 대신 (a)와 같은
   방법론(analyze() + CONFIG, KR+US, off=60~250)에 `analyze_super()`를
   추가로 얹어 "① 유니버스 필터로 쓰기" 절차를 오늘 날짜로 재현하고,
   KR/US 분해 + 전반/후반 6개월(체크포인트 절반) 분해.

【DIFF — (a) vs 2026-08-25 수급 스크립트 히트 추출, 코드 대조 결과】
| 항목 | (a) 2026-08-14 스크립트 | 2026-08-25 수급 스크립트 | 실질적 영향 |
|---|---|---|---|
| 유니버스 | `fetch_universe_data()` 기본값 = KR+US 동시 | `fetch_universe_data(markets=("kr",))` KR만 | **있음** — 비교 모집단 자체가 다름(이번 조사의 핵심 변수) |
| RS 랭크 계산 | KR+US 섞인 trunc_cache로 `compute_rs_at_checkpoint` 호출 | KR만 있는 trunc_cache로 동일 함수 호출 | **없음** — 함수 내부가 kr_raw/us_raw를 분리 집계 후 각자 `to_rs_rank()` 하므로 US 존재 여부가 KR 랭크에 영향 없음(코드 확인) |
| MIN_BARS_FLOOR (trunc_cache 사전 컷) | 140(5개 탭 중 최소, boxbreak 기준) | 210(pullback·super 공통 min_bars) | **없음** — `analyze()` 자체가 내부에서 `cfg["min_bars"]=210` 미만이면 어차피 None 반환, 사전 컷은 trunc_cache 구성 비용만 좌우 |
| 히트 판정 | `analyze(hist, rs_rank=rr, rs_mom=rm, cfg=CONFIG, is_kr=ikr)` | 동일 호출(마켓별 is_kr 정확히 전달) | 없음 — 동일 |
| 유동성 필터 | `harness.passes_liquidity_filter(hit, ikr)` | 동일 함수 | 없음 — 동일 |
| 체크포인트 | `harness.checkpoints(60, 250, 10)` | 동일 | 없음 — 동일 |
| 레이스 | `harness.race(close, stop, future)` | 동일 함수 | 없음 — 동일 |
| 실행 날짜(=오늘 기준 과거 데이터 범위) | 2026-08-14 | 2026-08-25 (11거래일 뒤) | 있음(작음) — 아래서 별도 정량화 |
| is_super 부착 | 없음(계산 안 함) | `analyze_super()` 추가 호출(app.py 실제 로직과 동일) | 눌림목 EV 자체엔 영향 없음(is_super는 부가 필드) |

**결론(코드 레벨)**: 두 스크립트의 눌림목 히트 판정·레이스·필터 로직은
100% 동일하다 — 파이프라인 버그/불일치는 없음. 유일한 실질적 차이는
"KR만 vs KR+US"와 "실행 날짜(11거래일 차이)"뿐. 아래 실측으로 두 변수의
기여도를 분리한다.

실행: 리포 루트에서
`python3 scripts/measurements/2026-08-26_pullback_ev_cohort_and_pipeline_diff.py`
(KR+US 전체 유니버스 fetch, 5~7분, 네트워크 필요)
"""
import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import harness
from scanner import analyze, CONFIG, analyze_super, SUPER_CONFIG

OFFSETS = harness.checkpoints(60, 250, 10)
MIN_BARS_FLOOR = max(CONFIG["min_bars"], SUPER_CONFIG["min_bars"])
# "전반 6개월"(오래된 절반) vs "후반 6개월"(최근 절반) — 체크포인트를
# 반으로 잘라 근사. off가 작을수록 최근 시점.
RECENT_OFFSETS = set(OFFSETS[:10])   # 60~150
OLDER_OFFSETS = set(OFFSETS[10:])    # 160~250


def collect_hits(data, bench):
    """(a) 2026-08-14 스크립트의 CORE_TABS["눌림목"] 로직과 100% 동일
    (analyze/CONFIG/liquidity filter/checkpoints/race) + is_super 추가
    (2026-08-25 수급 스크립트와 동일하게 app.py 실제 로직 재현)."""
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
        print(f"[PASS1] off={off} hits_so_far={len(hits)} elapsed={time.time()-t0:.0f}s ({oi+1}/{len(OFFSETS)})", flush=True)
    return hits


def ev_of(hits):
    return harness.ev_summary([h["outcome"] for h in hits])


def run(data, bench, out_path=None):
    hits = collect_hits(data, bench)
    daily_avg = round(len(hits) / len(OFFSETS), 1)
    kr_hits = [h for h in hits if h["market"] == "KR"]
    us_hits = [h for h in hits if h["market"] == "US"]

    report = {
        "daily_avg_hits": daily_avg,
        "n_hits_total": len(hits),
        "① 오늘_재현_전체(KR+US, (a)와 동일 방법론)": ev_of(hits),
        "①-KR단독": ev_of(kr_hits),
        "①-US단독": ev_of(us_hits),
    }
    print(f"[측정①] 전체={report['① 오늘_재현_전체(KR+US, (a)와 동일 방법론)']}", flush=True)
    print(f"[측정①] KR단독={report['①-KR단독']}", flush=True)
    print(f"[측정①] US단독={report['①-US단독']}", flush=True)

    super_hits = [h for h in hits if h["is_super"]]
    nonsuper_hits = [h for h in hits if not h["is_super"]]
    report["③ 슈퍼대장_소속_전체(KR+US)"] = ev_of(super_hits)
    report["③ 슈퍼대장_비소속_전체(KR+US)"] = ev_of(nonsuper_hits)
    super_kr = [h for h in super_hits if h["market"] == "KR"]
    super_us = [h for h in super_hits if h["market"] == "US"]
    report["③-KR단독"] = ev_of(super_kr)
    report["③-US단독"] = ev_of(super_us)
    print(f"[측정③] 슈퍼대장 소속 전체={report['③ 슈퍼대장_소속_전체(KR+US)']}", flush=True)
    print(f"[측정③] 슈퍼대장 KR단독={report['③-KR단독']} US단독={report['③-US단독']}", flush=True)

    super_kr_recent = [h for h in super_kr if h["off"] in RECENT_OFFSETS]
    super_kr_older = [h for h in super_kr if h["off"] in OLDER_OFFSETS]
    report["③-KR_최근절반(off60~150)"] = ev_of(super_kr_recent)
    report["③-KR_이전절반(off160~250)"] = ev_of(super_kr_older)
    print(f"[측정③-기간] KR 최근절반={report['③-KR_최근절반(off60~150)']}", flush=True)
    print(f"[측정③-기간] KR 이전절반={report['③-KR_이전절반(off160~250)']}", flush=True)

    if out_path:
        with open(out_path, "w") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"SAVED report to {out_path}", flush=True)
    return report


if __name__ == "__main__":
    data, kr_u, us_u = harness.fetch_universe_data()
    bench = harness.fetch_kr_benchmarks()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "2026-08-26_pullback_ev_cohort_and_pipeline_diff.results.json")
    run(data, bench, out_path=out)
