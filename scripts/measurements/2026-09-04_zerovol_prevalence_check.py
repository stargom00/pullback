"""
5탭 전체 KR base_vol50<=0(nonzero_vol_mean 유효봉 0개) 발생 여부 점검
(2026-09-04, 사용자 지시 후속) — 박스돌파 진단(2026-09-04_boxbreak_
basevol_diagnostic.py)에서 발견한 find_confirm_close() 가드 누락 버그가
박스돌파 외 다른 탭에도 실제 영향을 줬는지 확인하는 카운트 전용
스크립트. 조사 전용, app.py/scanner.py 무변경.

실행: `python3 scripts/measurements/2026-09-04_zerovol_prevalence_check.py`
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import harness
from scanner import (
    analyze, CONFIG,
    analyze_imminent, IMMINENT_CONFIG,
    analyze_boxbreak, BOXBREAK_CONFIG,
    analyze_breakout, BREAKOUT_CONFIG,
    analyze_turnaround, TURN_CONFIG,
    nonzero_vol_mean,
)

OFFSETS = harness.checkpoints(60, 950, 10)
TABS = {
    "눌림목": {"fn": analyze, "cfg": CONFIG},
    "돌파임박": {"fn": analyze_imminent, "cfg": IMMINENT_CONFIG},
    "박스돌파": {"fn": analyze_boxbreak, "cfg": BOXBREAK_CONFIG},
    "돌파": {"fn": analyze_breakout, "cfg": BREAKOUT_CONFIG},
    "추세전환": {"fn": analyze_turnaround, "cfg": TURN_CONFIG},
}
NEED_BARS = max(t["cfg"]["min_bars"] for t in TABS.values())


def main():
    data, kr_u, us_u = harness.fetch_universe_data(markets=("kr",), kr_days=1900, validate_offsets=OFFSETS)
    bench = harness.fetch_kr_benchmarks()
    kospi_close = bench["kospi"]["Close"].dropna()
    kosdaq_close = bench["kosdaq"]["Close"].dropna()
    tickers = list(data.keys())

    n_hits = {name: 0 for name in TABS}
    n_zero_base = {name: 0 for name in TABS}
    n_any_zero_day = {name: 0 for name in TABS}

    t0 = time.time()
    for oi, off in enumerate(OFFSETS):
        b_kospi = harness.bench_score_at(kospi_close, off)
        b_kosdaq = harness.bench_score_at(kosdaq_close, off)
        trunc_cache = {}
        for t in tickers:
            df = data[t]
            if len(df) - off < NEED_BARS:
                continue
            trunc_cache[t] = harness.truncate_at(df, off)
        rs_ranks, rs_moms = harness.compute_rs_at_checkpoint(trunc_cache, b_kospi, b_kosdaq)

        for t, hist in trunc_cache.items():
            ikr = harness.is_kr_ticker(t)
            if not ikr:
                continue
            rr = rs_ranks.get(t)
            rm = rs_moms.get(t)
            vol_window = hist["Volume"].iloc[-50:]
            base_vol = float(nonzero_vol_mean(vol_window))
            any_zero_day = bool((vol_window == 0).any())
            for name, spec in TABS.items():
                try:
                    r = spec["fn"](hist, rs_rank=rr, rs_mom=rm, cfg=spec["cfg"], is_kr=ikr)
                except Exception:
                    r = None
                if r is None or not harness.passes_liquidity_filter(r, ikr):
                    continue
                n_hits[name] += 1
                if base_vol <= 0:
                    n_zero_base[name] += 1
                if any_zero_day:
                    n_any_zero_day[name] += 1
        print(f"[PASS] offset {off} done ({oi+1}/{len(OFFSETS)}) elapsed={time.time()-t0:.0f}s "
              f"n_hits={n_hits}", flush=True)

    print("\n=== 결과: 탭별 base_vol50<=0 / 0거래량일 포함 히트 카운트 ===")
    for name in TABS:
        pct_zero_day = 100 * n_any_zero_day[name] / n_hits[name] if n_hits[name] else 0
        print(f"{name}: n_hits={n_hits[name]} base_vol<=0={n_zero_base[name]} "
              f"0거래량일>=1개={n_any_zero_day[name]}({pct_zero_day:.2f}%)")


if __name__ == "__main__":
    main()
