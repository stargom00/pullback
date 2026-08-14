"""
눌림목 손절폭 배지 UI + 확인 후 진입(안C') 측정 (2026-08-14, v5.66~v5.68)

측정한 것:
  ① 눌림목 및 4개 핵심 탭(추세전환/돌파/박스돌파/돌파임박)의 손절폭≤5% vs
     5%+ 구간별 EV — "손절 좁음=나쁜 신호"라는 사용자 전제 재현 시도.
  ③ 눌림목 안A(즉시진입)/안C'(1~3봉 내 거래량1.5배+고가돌파 확인 후 진입)/
     안D'(같은 시점·무조건 진입, 대조군) 2R 레이스 비교 + 하이브리드 EV.

결과·해석·기준선 불일치 조사 전체 경위는 docs/pullback_stop_width_and_entry_timing.md
참고 — 이 파일은 그 문서가 인용하는 실제 코드. 실행: 리포 루트에서
`python3 scripts/measurements/2026-08-14_pullback_stop_width_and_entry_timing.py`
(전체 유니버스 fetch 포함 총 5~7분 정도, 네트워크 필요).

공통 하네스(scripts/measurements/harness.py)를 쓴다 — RS계산/2R레이스/
저유동성필터를 여기서 다시 구현하지 않는 이유는
docs/pullback_stop_width_and_entry_timing.md "기준선 불일치 조사" 절 참고
(같은 걸 스크립트마다 따로 구현하다 기준선이 갈렸던 사고).
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import harness
from scanner import (analyze, CONFIG, analyze_turnaround, TURN_CONFIG,
                      analyze_breakout, BREAKOUT_CONFIG,
                      analyze_boxbreak, BOXBREAK_CONFIG,
                      analyze_imminent, IMMINENT_CONFIG)

OFFSETS = harness.checkpoints(60, 250, 10)

CORE_TABS = {
    "눌림목": (analyze, CONFIG),
    "추세전환": (analyze_turnaround, TURN_CONFIG),
    "돌파": (analyze_breakout, BREAKOUT_CONFIG),
    "박스돌파": (analyze_boxbreak, BOXBREAK_CONFIG),
    "돌파임박": (analyze_imminent, IMMINENT_CONFIG),
}
MIN_BARS_FLOOR = 140  # 5개 탭 중 최소(boxbreak=140) — 각 analyze_fn이 자기 cfg["min_bars"]로 또 거른다


def run(data, bench, out_path=None):
    import time
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None

    tickers = list(data.keys())
    results = {name: [] for name in CORE_TABS}
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
            future = harness.future_after(data[t], off)
            for tab_name, (fn, cfg) in CORE_TABS.items():
                try:
                    hit = fn(hist, rs_rank=rr, rs_mom=rm, cfg=cfg, is_kr=ikr)
                except Exception:
                    continue
                if hit is None:
                    continue
                if not harness.passes_liquidity_filter(hit, ikr):
                    continue
                results[tab_name].append({
                    "ticker": t, "off": off,
                    "close": hit.get("close"), "stop": hit.get("stop"),
                    "risk_pct": hit.get("risk_pct"),
                    "signal_high": float(hist["High"].iloc[-1]),
                    "trailing50_vol": float(hist["Volume"].iloc[-50:].mean()),
                    "future": future,
                })
        print(f"offset {off} done ({oi+1}/{len(OFFSETS)}) elapsed={time.time()-t0:.0f}s "
              f"hits={ {k: len(v) for k,v in results.items()} }", flush=True)

    report = {}

    # ① 손절폭 세그먼트
    seg_report = {}
    for tab, hits in results.items():
        outcomes_all = [harness.race(h["close"], h["stop"], h["future"]) for h in hits]
        narrow_idx = [i for i, h in enumerate(hits) if h["risk_pct"] is not None and h["risk_pct"] <= 5.0]
        wide_idx = [i for i, h in enumerate(hits) if h["risk_pct"] is not None and h["risk_pct"] > 5.0]
        seg_report[tab] = {
            "overall": harness.ev_summary(outcomes_all),
            "narrow_le5": harness.ev_summary([outcomes_all[i] for i in narrow_idx]),
            "wide_gt5": harness.ev_summary([outcomes_all[i] for i in wide_idx]),
            "daily_avg_hits": round(len(hits) / len(OFFSETS), 1),
        }
        print(f"[STOP-WIDTH] {tab}: {seg_report[tab]}", flush=True)
    report["stop_width_by_tab"] = seg_report

    # ③ 눌림목 안A/안C'/안D'/하이브리드
    pb_hits = results["눌림목"]

    def find_confirm(h, k_max=3):
        fut = h["future"]
        trigger = h["signal_high"]
        base_vol = h["trailing50_vol"]
        avail = min(k_max, len(fut))
        for k in range(1, avail + 1):
            c = float(fut["Close"].iloc[k - 1])
            vv = float(fut["Volume"].iloc[k - 1])
            if c > trigger and vv >= 1.5 * base_vol:
                return k, trigger, c
        return None

    a_outcomes, c_outcomes, d_outcomes, hybrid_outcomes = [], [], [], []
    n_confirmed = 0
    for h in pb_hits:
        a_out = harness.race(h["close"], h["stop"], h["future"])
        a_outcomes.append(a_out)
        conf = find_confirm(h)
        if conf is None:
            hybrid_outcomes.append(a_out)
            continue
        k, trigger_price, close_price = conf
        n_confirmed += 1
        fut_after = h["future"].iloc[k:]
        c_out = harness.race(trigger_price, h["stop"], fut_after)
        d_out = harness.race(close_price, h["stop"], fut_after)
        c_outcomes.append(c_out)
        d_outcomes.append(d_out)
        hybrid_outcomes.append(c_out)

    pullback_report = {
        "n_hits_total": len(pb_hits),
        "안A_전체": harness.ev_summary(a_outcomes),
        "안C'_진입분": harness.ev_summary(c_outcomes),
        "안D'_진입분(시점매칭 대조군)": harness.ev_summary(d_outcomes),
        "하이브리드_전체(미진입=안A로 대체)": harness.ev_summary(hybrid_outcomes),
        "진입률_C'": round(n_confirmed / len(pb_hits), 4) if pb_hits else None,
    }
    report["pullback_entry_timing"] = pullback_report
    print(f"[PULLBACK A/C'/D'] {pullback_report}", flush=True)

    if out_path:
        with open(out_path, "w") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"SAVED report to {out_path}", flush=True)
    return report


if __name__ == "__main__":
    data, kr_u, us_u = harness.fetch_universe_data()
    bench = harness.fetch_kr_benchmarks()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "2026-08-14_pullback_stop_width_and_entry_timing.results.json")
    run(data, bench, out_path=out)
