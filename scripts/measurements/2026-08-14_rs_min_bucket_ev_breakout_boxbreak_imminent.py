"""
rs_min 85→80(v5.60) 재측정 — 돌파/박스돌파/돌파임박 RS 구간별 2R 레이스 EV
(2026-08-14)

배경: docs/pullback_stop_width_and_entry_timing.md "기준선 불일치 조사"에서
눌림목 EV 재측정이 원 기록(Script A)과 66% 어긋난 걸 발견했는데, 원 스크립트가
저장소에 없어 원인 특정에 실패했다. 그 사고와 구조가 같은(인접 구간 이진비교,
임계값 근처) 결정 중 하나가 v5.60의 rs_min 85→80(breakout/boxbreak/imminent
3개 탭) — "80-85 구간 EV가 85-90보다 같거나 높다"는 근거였는데 원 스크립트가
없어 재현 불가 상태였다. 우선순위 3위(docs 문서의 "Script A 기반 결정 재측정
우선순위" 표 참고)로 재측정.

측정: RS 구간 5개(75-80/80-85/85-90/90-95/95+)별 2R 레이스 EV·n. rs_min을
낮춰야 75-80·80-85 구간이 애초에 analyze_*() 안에서 걸러지지 않고 나온다 —
CONFIG의 rs_min을 70으로 임시로 낮춘 복사본을 써서(원본 CONFIG는 안 건드림)
전 구간이 히트로 나오게 한 다음, 실제 rs_rank로 사후에 버킷을 나눈다.

공통 하네스(harness.py) 사용 — RS계산/2R레이스/체크포인트/저유동성필터를
새로 구현하지 않음(같은 걸 스크립트마다 따로 만들다 기준선이 갈렸던 사고,
docs 문서 참고).

실행: 리포 루트에서
`python3 scripts/measurements/2026-08-14_rs_min_bucket_ev_breakout_boxbreak_imminent.py`
(전체 유니버스 fetch 포함 5~7분, 네트워크 필요).
"""
import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import harness
from scanner import (analyze_breakout, BREAKOUT_CONFIG,
                      analyze_boxbreak, BOXBREAK_CONFIG,
                      analyze_imminent, IMMINENT_CONFIG)

OFFSETS = harness.checkpoints(60, 250, 10)
MIN_BARS_FLOOR = 140  # boxbreak(140)이 최소 — 각 analyze_fn이 자기 cfg["min_bars"]로 또 거른다

# rs_min을 70으로 낮춘 측정 전용 cfg 복사본 — 75-80/80-85 구간이 함수 내부
# RS게이트에서 걸러지지 않게 함. 원본 CONFIG(scanner.py)는 안 건드림 —
# 프로덕션 동작에 영향 없음.
TABS = {
    "돌파": (analyze_breakout, dict(BREAKOUT_CONFIG, rs_min=70)),
    "박스돌파": (analyze_boxbreak, dict(BOXBREAK_CONFIG, rs_min=70)),
    "돌파임박": (analyze_imminent, dict(IMMINENT_CONFIG, rs_min=70)),
}

BUCKETS = [(75, 80), (80, 85), (85, 90), (90, 95), (95, 100)]  # [lo, hi) 반열림, 마지막만 100 포함
MIN_N_FOR_JUDGMENT = 30  # 이보다 적으면 노이즈로 보고 판단 보류


def bucket_of(rs_rank):
    if rs_rank is None:
        return None
    for lo, hi in BUCKETS:
        if lo <= rs_rank < hi:
            return f"{lo}-{hi if hi < 100 else '99'}"
    return None


def run(data, bench, out_path=None):
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None

    tickers = list(data.keys())
    results = {name: [] for name in TABS}
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
            if rr is None or rr < 75:
                continue  # 이번 측정 범위(75+) 밖 — 애초에 5구간에 안 들어감
            future = harness.future_after(data[t], off)
            for tab_name, (fn, cfg) in TABS.items():
                try:
                    hit = fn(hist, rs_rank=rr, rs_mom=rm, cfg=cfg, is_kr=ikr)
                except Exception:
                    continue
                if hit is None:
                    continue
                if not harness.passes_liquidity_filter(hit, ikr):
                    continue
                results[tab_name].append({
                    "ticker": t, "off": off, "rs_rank": rr,
                    "close": hit.get("close"), "stop": hit.get("stop"),
                    "future": future,
                })
        print(f"offset {off} done ({oi+1}/{len(OFFSETS)}) elapsed={time.time()-t0:.0f}s "
              f"hits={ {k: len(v) for k,v in results.items()} }", flush=True)

    report = {}
    for tab, hits in results.items():
        bucket_report = {}
        for lo, hi in BUCKETS:
            key = f"{lo}-{hi if hi < 100 else '99'}"
            sub = [h for h in hits if lo <= h["rs_rank"] < hi]
            outcomes = [harness.race(h["close"], h["stop"], h["future"]) for h in sub]
            summ = harness.ev_summary(outcomes)
            summ["reliable"] = summ["nv"] >= MIN_N_FOR_JUDGMENT
            bucket_report[key] = summ
        report[tab] = bucket_report
        print(f"[RS-BUCKET] {tab}:", flush=True)
        for key, summ in bucket_report.items():
            flag = "" if summ["reliable"] else "  (n<30, 노이즈 가능 — 판단 보류)"
            print(f"   {key}: n={summ['n_hits']} EV={summ['ev_R']}{flag}", flush=True)

    if out_path:
        with open(out_path, "w") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"SAVED report to {out_path}", flush=True)
    return report


if __name__ == "__main__":
    data, kr_u, us_u = harness.fetch_universe_data()
    bench = harness.fetch_kr_benchmarks()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "2026-08-14_rs_min_bucket_ev_breakout_boxbreak_imminent.results.json")
    run(data, bench, out_path=out)
