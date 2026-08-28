"""
사전 등록 합산 검정 (2026-08-29, 사용자 지시). 전략 지도(docs/kr_us_strategy_map.md)
후속 — 시장별 권장 탭(돌파 계열 vs 되돌림 계열) 채택 여부를 가리는 마지막 측정.

측정 스크립트만 — scanner.py/app.py 미수정. 공통 하네스(harness.py) 재사용,
새 로직 없음. 규칙 6(대조군 유동성매칭)은 해당 없음(대조군 비교가 아니라
실제 프로덕션 히트 코호트를 계열별로 합산 비교). 규칙7(z검정)은
harness.ev_gap_zscore 사용. 규칙8(KR+US 혼합 금지)이 이 스크립트의 핵심 —
KR 내부·US 내부 각각에서만 두 계열을 비교하고 KR/US를 섞지 않는다.

【사전 등록 가설 — 측정 전 고정, 편집 금지】
"KR에서는 돌파 계열(돌파+박스돌파+추세전환 합산)이 되돌림 계열(눌림목+
돌파임박 합산)보다 EV가 높다." US에서는 대칭적으로 반대 방향(되돌림 계열
우위)이 나오면 이야기가 완성된다(비대칭 시장 구조 가설의 대칭성 확인).

【사전 등록 판정 기준 — 측정 전 고정】
KR에서 격차(돌파계열EV - 되돌림계열EV) >= +0.1R **그리고** z >= 1.96이면
"KR = 돌파 계열 우위" 채택 → GUIDE에 시장별 권장 탭 기록.
미달이면 방향성 관찰로만 기록하고 표본 축적 대기(채택 안 함).

각 계열은 "해당 계열에 속한 어느 탭이든 하나라도 잡은 히트"를 전부 풀링한
단일 집합(pooled outcomes)으로 본다 — 한 종목이 같은 체크포인트에서 여러
탭에 동시 히트해도 각 탭 히트는 별개 트레이드(서로 다른 entry/stop/목표)라
그대로 전부 포함한다(기존 스크립트들의 관례와 동일, 탭간 히트 배타성 가정
안 함).

실행: 리포 루트에서
  python3 scripts/measurements/2026-08-29_breakout_vs_pullback_family_kr_us.py
(전체 유니버스 fetch + RS 사전계산 + 5개 탭 × 20 체크포인트 analyze 호출.
2026-08-29_kr_us_decomposition_final.py와 동일 비용 — 30~40분 예상)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import time

import harness
from scanner import (
    CONFIG, analyze, to_rs_rank,
    analyze_turnaround, TURN_CONFIG,
    analyze_breakout, BREAKOUT_CONFIG,
    analyze_boxbreak, BOXBREAK_CONFIG,
    analyze_imminent, IMMINENT_CONFIG,
)

OFFSETS = harness.checkpoints(60, 250, 10)      # 20개 — 전 측정 공용 표준 스펙
RS_DELTA_LOOKBACK = 20
RS_MIN_BARS = 200


def rs_3m_ranks(trunc_cache):
    """2026-08-29_kr_us_decomposition_final.py와 동일 정의(3개월 수익률 RS
    백분위) — 눌림목 프로덕션 E게이트(rs_3m)에 필요."""
    kr3, us3 = {}, {}
    for t, hist in trunc_cache.items():
        r3 = harness.ret_pct(hist["Close"], 63)
        if r3 is None:
            continue
        if harness.is_kr_ticker(t):
            kr3[t] = r3
        else:
            us3[t] = r3
    return {**to_rs_rank(kr3), **to_rs_rank(us3)}


def precompute_rs(data, kospi_close, kosdaq_close):
    t0 = time.time()
    tickers = list(data.keys())
    extra_offsets = sorted(set(OFFSETS) | {o + RS_DELTA_LOOKBACK for o in OFFSETS})
    rs_cache, r3_cache = {}, {}
    for off in extra_offsets:
        b_kospi = harness.bench_score_at(kospi_close, off)
        b_kosdaq = harness.bench_score_at(kosdaq_close, off)
        trunc_cache = {}
        for t in tickers:
            df = data[t]
            if len(df) - off < RS_MIN_BARS:
                continue
            trunc_cache[t] = harness.truncate_at(df, off)
        rs_ranks, rs_moms = harness.compute_rs_at_checkpoint(trunc_cache, b_kospi, b_kosdaq)
        rs_cache[off] = (rs_ranks, rs_moms)
        r3_cache[off] = rs_3m_ranks(trunc_cache)
        print(f"[rs-precompute] offset {off} 완료 elapsed={time.time()-t0:.0f}s", flush=True)
    return rs_cache, r3_cache


def collect_simple_tab(data, analyze_fn, cfg, rs_cache, label):
    """돌파/박스돌파/추세전환/돌파임박 공통 수집 — rs_3m/rs_delta 불필요한
    4개 탭용(2026-08-29_kr_us_decomposition_final.py와 동일 함수)."""
    t0 = time.time()
    outcomes_kr, outcomes_us = [], []
    for oi, off in enumerate(OFFSETS):
        rs_ranks, rs_moms = rs_cache[off]
        for t, df in data.items():
            if len(df) - off < cfg["min_bars"]:
                continue
            ikr = harness.is_kr_ticker(t)
            hist = harness.truncate_at(df, off)
            try:
                hit = analyze_fn(hist, rs_rank=rs_ranks.get(t), rs_mom=rs_moms.get(t), cfg=cfg, is_kr=ikr)
            except Exception:
                continue
            if hit is None or not harness.passes_liquidity_filter(hit, ikr):
                continue
            future = harness.future_after(df, off)
            outcome = harness.race(hit["close"], hit["stop"], future)
            (outcomes_kr if ikr else outcomes_us).append(outcome)
        print(f"[{label}] offset {off} 완료 ({oi+1}/{len(OFFSETS)}) "
              f"kr={len(outcomes_kr)} us={len(outcomes_us)} elapsed={time.time()-t0:.0f}s", flush=True)
    return outcomes_kr, outcomes_us


def collect_pullback(data, rs_cache, r3_cache):
    """눌림목 — 프로덕션 E게이트(rs_3m/rs_delta) 그대로 사용."""
    t0 = time.time()
    outcomes_kr, outcomes_us = [], []
    for oi, off in enumerate(OFFSETS):
        rs_ranks, rs_moms = rs_cache[off]
        r3_ranks = r3_cache[off]
        rs_20ago, _ = rs_cache.get(off + RS_DELTA_LOOKBACK, ({}, {}))
        for t, df in data.items():
            if len(df) - off < CONFIG["min_bars"]:
                continue
            ikr = harness.is_kr_ticker(t)
            hist = harness.truncate_at(df, off)
            rs = rs_ranks.get(t)
            rm = rs_moms.get(t)
            rs3m = r3_ranks.get(t)
            rs_delta = (rs - rs_20ago.get(t)) if (rs is not None and t in rs_20ago) else None
            try:
                hit = analyze(hist, rs_rank=rs, rs_mom=rm, cfg=CONFIG, is_kr=ikr,
                              rs_3m=rs3m, rs_delta=rs_delta)
            except Exception:
                hit = None
            if hit is None or not harness.passes_liquidity_filter(hit, ikr):
                continue
            future = harness.future_after(df, off)
            outcome = harness.race(hit["close"], hit["stop"], future)
            (outcomes_kr if ikr else outcomes_us).append(outcome)
        print(f"[눌림목] offset {off} 완료 ({oi+1}/{len(OFFSETS)}) "
              f"kr={len(outcomes_kr)} us={len(outcomes_us)} elapsed={time.time()-t0:.0f}s", flush=True)
    return outcomes_kr, outcomes_us


def report_pair(label, kr_outcomes, us_outcomes):
    ev_kr = harness.ev_summary(kr_outcomes)
    ev_us = harness.ev_summary(us_outcomes)
    z, sig = harness.ev_gap_zscore(ev_kr, ev_us) if (ev_kr["nv"] and ev_us["nv"]) else (None, False)
    print(f"\n  {label}")
    if ev_kr["ev_R"] is not None:
        print(f"    KR: n={ev_kr['n_hits']} (nv={ev_kr['nv']}) EV={ev_kr['ev_R']:.3f}R")
    else:
        print(f"    KR: n={ev_kr['n_hits']} EV=N/A(표본부족)")
    if ev_us["ev_R"] is not None:
        print(f"    US: n={ev_us['n_hits']} (nv={ev_us['nv']}) EV={ev_us['ev_R']:.3f}R")
    else:
        print(f"    US: n={ev_us['n_hits']} EV=N/A(표본부족)")
    if z is not None:
        print(f"    gap(US-KR)={ev_us['ev_R']-ev_kr['ev_R']:.3f}R  z={z:.2f}  {'유의(|z|>=1.96)' if sig else '유의하지 않음'}")
    else:
        print("    z검정 불가(표본부족)")
    return ev_kr, ev_us, z, sig


def within_market_gap(label, family_a_outcomes, family_b_outcomes):
    """같은 시장 내부에서 계열 A vs 계열 B 비교(사전 등록 검정 본체) —
    KR/US를 섞지 않는다(규칙8)."""
    ev_a = harness.ev_summary(family_a_outcomes)
    ev_b = harness.ev_summary(family_b_outcomes)
    z, sig = harness.ev_gap_zscore(ev_b, ev_a) if (ev_a["nv"] and ev_b["nv"]) else (None, False)
    gap = (ev_a["ev_R"] - ev_b["ev_R"]) if (ev_a["ev_R"] is not None and ev_b["ev_R"] is not None) else None
    print(f"\n  [{label}]")
    print(f"    돌파계열:   n={ev_a['n_hits']} (nv={ev_a['nv']}) EV={ev_a['ev_R']:.3f}R" if ev_a["ev_R"] is not None else f"    돌파계열: n={ev_a['n_hits']} EV=N/A")
    print(f"    되돌림계열: n={ev_b['n_hits']} (nv={ev_b['nv']}) EV={ev_b['ev_R']:.3f}R" if ev_b["ev_R"] is not None else f"    되돌림계열: n={ev_b['n_hits']} EV=N/A")
    if gap is not None:
        print(f"    격차(돌파-되돌림)={gap:.3f}R  z={z:.2f}" if z is not None else f"    격차(돌파-되돌림)={gap:.3f}R  z=계산불가")
    return ev_a, ev_b, gap, z, sig


if __name__ == "__main__":
    _t0 = time.time()
    data, kr_u, us_u = harness.fetch_universe_data()
    bench = harness.fetch_kr_benchmarks()
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None

    rs_cache, r3_cache = precompute_rs(data, kospi_close, kosdaq_close)

    print("\n" + "=" * 70)
    print("돌파 계열 3개 탭 수집")
    print("=" * 70)
    brk_kr, brk_us = collect_simple_tab(data, analyze_breakout, BREAKOUT_CONFIG, rs_cache, "돌파")
    box_kr, box_us = collect_simple_tab(data, analyze_boxbreak, BOXBREAK_CONFIG, rs_cache, "박스돌파")
    turn_kr, turn_us = collect_simple_tab(data, analyze_turnaround, TURN_CONFIG, rs_cache, "추세전환")

    print("\n" + "=" * 70)
    print("되돌림 계열 2개 탭 수집")
    print("=" * 70)
    pb_kr, pb_us = collect_pullback(data, rs_cache, r3_cache)
    imm_kr, imm_us = collect_simple_tab(data, analyze_imminent, IMMINENT_CONFIG, rs_cache, "돌파임박")

    print("\n" + "=" * 70)
    print("개별 탭 EV (KR/US 분해, 참고용)")
    print("=" * 70)
    report_pair("돌파", brk_kr, brk_us)
    report_pair("박스돌파", box_kr, box_us)
    report_pair("추세전환", turn_kr, turn_us)
    report_pair("눌림목", pb_kr, pb_us)
    report_pair("돌파임박", imm_kr, imm_us)

    breakout_family_kr = brk_kr + box_kr + turn_kr
    breakout_family_us = brk_us + box_us + turn_us
    pullback_family_kr = pb_kr + imm_kr
    pullback_family_us = pb_us + imm_us

    print("\n" + "=" * 70)
    print("【사전 등록 검정】 돌파 계열(돌파+박스돌파+추세전환) vs 되돌림 계열(눌림목+돌파임박)")
    print("=" * 70)
    ev_a_kr, ev_b_kr, gap_kr, z_kr, sig_kr = within_market_gap("KR", breakout_family_kr, pullback_family_kr)
    ev_a_us, ev_b_us, gap_us, z_us, sig_us = within_market_gap("US", breakout_family_us, pullback_family_us)

    print("\n" + "=" * 70)
    print("【사전 등록 판정】 KR 격차 >= +0.1R 그리고 z >= 1.96 → 채택")
    print("=" * 70)
    if gap_kr is not None and z_kr is not None and gap_kr >= 0.1 and z_kr >= 1.96:
        print(f"  KR: 격차={gap_kr:.3f}R, z={z_kr:.2f} → 기준 충족. 'KR=돌파 계열 우위' 채택.")
    else:
        gap_s = f"{gap_kr:.3f}R" if gap_kr is not None else "N/A"
        z_s = f"{z_kr:.2f}" if z_kr is not None else "N/A"
        print(f"  KR: 격차={gap_s}, z={z_s} → 기준 미달. 방향성 관찰로만 기록, 채택 보류.")
    if gap_us is not None:
        print(f"  US(대칭 확인): 격차(돌파-되돌림)={gap_us:.3f}R, z={z_us:.2f}" if z_us is not None else f"  US(대칭 확인): 격차={gap_us:.3f}R, z=계산불가")
        if gap_us < 0:
            print("  → US는 되돌림 계열 우위 방향(부호 반대) — KR과 대칭.")
        else:
            print("  → US도 돌파 계열 우위 방향(부호 동일) — 비대칭 가설과 불일치, 대칭 스토리 미완성.")

    print(f"\n[main] 전체 완료, elapsed={time.time()-_t0:.0f}s", flush=True)
