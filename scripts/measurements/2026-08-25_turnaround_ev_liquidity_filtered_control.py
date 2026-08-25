"""
추세전환 EV 재측정 — 유동성 필터 통과 히트 + 시점매칭 대조군 (2026-08-25,
8/17 캠페인 ③-후속).

배경: `all_tabs_common_yardstick_investigation.md`의 Script A(2026-08-08,
유동성 필터 없음, 원본 소실)는 추세전환 EV=0.362로 "탭 중 최고"라고
기록했다. 2026-08-14 재측정(`pullback_stop_width_and_entry_timing.py`
"다른 4개 핵심 탭도 같이 측정" 절, harness 기반, 저유동성 필터 포함)은
이미 EV=0.233으로 나왔었지만, 그 측정엔 시점매칭 대조군이 없었다(손절폭
narrow/wide 세그먼트 비교만 했음) — "탭 중 최고" 지위가 유지되는지도
그때 명시적으로 안 짚었다. 이번 스크립트가 채우는 것: 대조군 포함 +
"최고 지위" 여부를 다른 4개 탭의 기존 수치와 나란히 명시.

**scanner.py는 전혀 수정하지 않는다**(요청 사항) — `select_pivot`/
`apply_atr_buffer`/`atr`는 기존 함수를 그대로 import해서 대조군 stop
계산에 재사용(아래 "대조군 방법론" 참고), analyze_turnaround 자체는
안 건드림.

대조군 방법론: "시점매칭 대조군 = 같은 체크포인트, 무작위 종목, 동일
레이스"(all_tabs 문서 원문)를 따르되, 대조군 티커는 애초에
analyze_turnaround() 게이트를 통과 못 해 `stop` 필드가 없다. `stop`
계산식 자체(2154~2161행)는 게이트와 무관한 일반 기술적 계산(60일선×0.98,
최근10일 저가, ATR 버퍼)이라 — 이 부분만 스크립트에 복사해 "동일 레이스"
조건을 맞춘다(진입=신호일 종가는 실제 히트와 동일). `select_pivot`은
`stop` 계산에 안 쓰여서 필요 없음. ⚠️ scanner.py의 이 손절 공식이
바뀌면 이 스크립트도 갱신 필요(CLAUDE.md 리터럴 사본 동기화 원칙과
같은 이유) — 2154~2161행 원문 그대로 복사했음을 주석에 명시.

실행: 리포 루트에서
`python3 scripts/measurements/2026-08-25_turnaround_ev_liquidity_filtered_control.py`
(전체 유니버스 fetch 포함 5~7분, 네트워크 필요)
"""
import sys
import os
import json
import random
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import harness
from scanner import analyze_turnaround, TURN_CONFIG, apply_atr_buffer

OFFSETS = harness.checkpoints(60, 250, 10)  # Script A/2026-08-14 재측정과 동일 표준 스펙
MIN_BARS_FLOOR = TURN_CONFIG["min_bars"]
RANDOM_SEED = 42


def control_stop(hist):
    """scanner.py analyze_turnaround() 2154~2161행의 stop 계산식 그대로
    복사(게이트 없이도 계산 가능한 일반 기술적 부분만 — select_pivot은
    stop 계산에 안 쓰여서 불필요). 종가/저가/고가만 있으면 아무 티커나
    가능 — 대조군용."""
    c, h, lo = hist["Close"], hist["High"], hist["Low"]
    if len(c) < 60:
        return None
    close = float(c.iloc[-1])
    m60 = float(c.rolling(60).mean().iloc[-1])
    if close <= 0:
        return None
    stop = m60 * 0.98
    candidates = [x for x in (stop, float(lo.iloc[-10:].min())) if x < close]
    stop = max(candidates) if candidates else float(lo.iloc[-10:].min())
    stop, _, _ = apply_atr_buffer(stop, h, lo, c, 0.3)
    if stop is None or stop >= close:
        return None
    return stop


def run(data, bench, out_path=None):
    rng = random.Random(RANDOM_SEED)
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None

    tickers = list(data.keys())
    real_outcomes = []
    control_outcomes = []
    daily_hits = []
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

        hit_tickers = []
        for t, hist in trunc_cache.items():
            ikr = harness.is_kr_ticker(t)
            try:
                hit = analyze_turnaround(hist, rs_rank=rs_ranks.get(t), rs_mom=rs_moms.get(t),
                                          cfg=TURN_CONFIG, is_kr=ikr)
            except Exception:
                continue
            if hit is None:
                continue
            if not harness.passes_liquidity_filter(hit, ikr):
                continue
            hit_tickers.append(t)
            future = harness.future_after(data[t], off)
            real_outcomes.append(harness.race(hit["close"], hit["stop"], future))

        daily_hits.append(len(hit_tickers))

        # 대조군: 같은 체크포인트 전체 유니버스(필터 미적용)에서 히트 수만큼 무작위(페어)
        pool = list(trunc_cache.keys())
        sample_n = min(len(hit_tickers), len(pool))
        for t in (rng.sample(pool, sample_n) if sample_n else []):
            hist = trunc_cache[t]
            close = float(hist["Close"].iloc[-1])
            stop = control_stop(hist)
            if stop is None:
                continue
            future = harness.future_after(data[t], off)
            control_outcomes.append(harness.race(close, stop, future))

        print(f"[TURNAROUND-EV] off={off} hits={len(hit_tickers)} control_n={sample_n} "
              f"elapsed={time.time()-t0:.0f}s ({oi+1}/{len(OFFSETS)})", flush=True)

    real_ev = harness.ev_summary(real_outcomes)
    control_ev = harness.ev_summary(control_outcomes)
    daily_avg = round(sum(daily_hits) / len(daily_hits), 1) if daily_hits else None

    report = {
        "daily_avg_hits": daily_avg,
        "real": real_ev,
        "control": control_ev,
        "reference": {
            "script_a_2026_08_08_no_liquidity_filter": {"ev_R": 0.362, "daily_avg_hits": 88.4, "note": "탭 중 최고로 기록됐던 원 수치, 원본 소실"},
            "2026_08_14_liquidity_filter_no_control": {"ev_R": 0.233, "daily_avg_hits": 50.1},
            # 2026-08-14 재측정에서 같이 나온 다른 4개 핵심 탭 EV(대조군 없음,
            # "탭 중 최고" 여부 판단용 — 그대로 인용, 재측정 안 함).
            "other_tabs_2026_08_14": {
                "눌림목": 0.291, "돌파": 0.212, "박스돌파": 0.161, "돌파임박": 0.232,
            },
        },
    }
    print(f"[TURNAROUND-EV SUMMARY] 일평균히트={daily_avg}, 실제 EV={real_ev['ev_R']}"
          f"(n={real_ev['nv']}, 손절률={real_ev['stop_rate']}, 도달률={real_ev['target_rate']}), "
          f"대조군 EV={control_ev['ev_R']}(n={control_ev['nv']})", flush=True)

    if out_path:
        with open(out_path, "w") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"SAVED report to {out_path}", flush=True)
    return report


if __name__ == "__main__":
    data, kr_u, us_u = harness.fetch_universe_data()
    bench = harness.fetch_kr_benchmarks()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "2026-08-25_turnaround_ev_liquidity_filtered_control.results.json")
    run(data, bench, out_path=out)
