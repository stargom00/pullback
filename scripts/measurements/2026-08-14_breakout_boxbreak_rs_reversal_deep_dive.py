"""
돌파·박스돌파 RS 역방향 심층 조사 (2026-08-14)

배경: 2026-08-14_rs_min_bucket_ev_breakout_boxbreak_imminent.py에서 돌파·
박스돌파는 RS 75-80 구간이 오히려 EV 최고치(95-99는 돌파 -0.081R까지
음수)라는 역방향을 발견했다 — U/D volume ratio 조사(신호등에서 돌파 RS를
뺀 이유: ok군 0.232R < warn군 0.482R)와 같은 방향. 세 가지를 추가로 확인:

  1. rs_min을 65/70/75까지 낮추면 실제로 어떻게 되는지(외삽 아니라 실측).
     낮은 구간 히트가 급증할 텐데 그게 실용적인지, EV가 유지되는지.
  2. (참고) 탭별 rs_min 분리 여부는 이 스크립트 결과 + 사람 판단으로 별도
     결정 — 이 스크립트는 숫자만 낸다.
  3. "RS 높은 돌파 종목이 이미 extended(과확장) 상태라 나쁘다"는 가설 —
     RS 구간별로 AVWAP 이격(anchored_vwap dist_pct), 200일선 이격
     (ext200_pct), 52주 고점 대비 위치(off_high_pct)를 비교.

공통 하네스(harness.py) 재사용. 돌파(breakout)·박스돌파(boxbreak)만 대상
(돌파임박은 반대 방향이 이미 확인돼 이번 조사 범위 밖).

실행: 리포 루트에서
`python3 scripts/measurements/2026-08-14_breakout_boxbreak_rs_reversal_deep_dive.py`
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
                      off_high_pct)

OFFSETS = harness.checkpoints(60, 250, 10)
MIN_BARS_FLOOR = 140  # boxbreak(140)이 최소

# 60까지 내려서 60-65 구간도 잡는다. rs_min을 55로 낮춘 측정 전용 사본 —
# 원본 CONFIG는 안 건드림.
TABS = {
    "돌파": (analyze_breakout, dict(BREAKOUT_CONFIG, rs_min=55)),
    "박스돌파": (analyze_boxbreak, dict(BOXBREAK_CONFIG, rs_min=55)),
}

BUCKETS = [(60, 65), (65, 70), (70, 75), (75, 80), (80, 85), (85, 90), (90, 95), (95, 100)]
# "rs_min을 이 값으로 낮추면" 누적(threshold 이상 전부) 관점 — 실용성 판단용
THRESHOLDS = [80, 75, 70, 65, 60]


def bucket_key(lo, hi):
    return f"{lo}-{hi if hi < 100 else '99'}"


def median(xs):
    xs = sorted(x for x in xs if x is not None)
    n = len(xs)
    if n == 0:
        return None
    mid = n // 2
    return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2


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
            if rr is None or rr < 60:
                continue
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
                avwap = hit.get("avwap") or {}
                results[tab_name].append({
                    "ticker": t, "off": off, "rs_rank": rr,
                    "close": hit.get("close"), "stop": hit.get("stop"),
                    "future": future,
                    "avwap_dist_pct": avwap.get("dist_pct"),
                    "ext200_pct": hit.get("ext200_pct"),
                    "off_high_pct": off_high_pct(hist["Close"]),
                })
        print(f"offset {off} done ({oi+1}/{len(OFFSETS)}) elapsed={time.time()-t0:.0f}s "
              f"hits={ {k: len(v) for k,v in results.items()} }", flush=True)

    report = {}
    for tab, hits in results.items():
        # ── 1) 구간별 EV (외삽 없이 실측) ──
        bucket_report = {}
        for lo, hi in BUCKETS:
            key = bucket_key(lo, hi)
            sub = [h for h in hits if lo <= h["rs_rank"] < hi]
            outcomes = [harness.race(h["close"], h["stop"], h["future"]) for h in sub]
            summ = harness.ev_summary(outcomes)
            summ["daily_avg_hits"] = round(len(sub) / len(OFFSETS), 2)
            summ["avwap_dist_pct_median"] = median([h["avwap_dist_pct"] for h in sub])
            summ["ext200_pct_median"] = median([h["ext200_pct"] for h in sub])
            summ["off_high_pct_median"] = median([h["off_high_pct"] for h in sub])
            bucket_report[key] = summ

        # ── 2) 임계값별 누적(threshold 이상 전부) — "여기로 낮추면" 실용성 ──
        threshold_report = {}
        for th in THRESHOLDS:
            sub = [h for h in hits if h["rs_rank"] >= th]
            outcomes = [harness.race(h["close"], h["stop"], h["future"]) for h in sub]
            summ = harness.ev_summary(outcomes)
            summ["daily_avg_hits"] = round(len(sub) / len(OFFSETS), 2)
            threshold_report[f"rs_min={th}"] = summ

        report[tab] = {"buckets": bucket_report, "thresholds": threshold_report}

        print(f"\n[{tab}] 구간별 EV + extended 지표(median):", flush=True)
        for key, summ in bucket_report.items():
            print(f"   {key}: n={summ['n_hits']} EV={summ['ev_R']} "
                  f"일평균={summ['daily_avg_hits']} "
                  f"AVWAP이격={summ['avwap_dist_pct_median']} "
                  f"200일선이격={summ['ext200_pct_median']} "
                  f"52주고점대비={summ['off_high_pct_median']}", flush=True)
        print(f"[{tab}] 임계값별 누적(rs_min을 이 값으로 낮추면):", flush=True)
        for key, summ in threshold_report.items():
            print(f"   {key}: n={summ['n_hits']} EV={summ['ev_R']} 일평균={summ['daily_avg_hits']}", flush=True)

    if out_path:
        with open(out_path, "w") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\nSAVED report to {out_path}", flush=True)
    return report


if __name__ == "__main__":
    data, kr_u, us_u = harness.fetch_universe_data()
    bench = harness.fetch_kr_benchmarks()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "2026-08-14_breakout_boxbreak_rs_reversal_deep_dive.results.json")
    run(data, bench, out_path=out)
