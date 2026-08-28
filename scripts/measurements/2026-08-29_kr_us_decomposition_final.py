"""
KR/US 전략 분리 확정을 위한 마지막 측정 2개 (2026-08-29, 사용자 지시).
측정 스크립트만 — scanner.py/app.py 미수정. 규칙 6/7/8(scripts/measurements/
README.md) 준수: 규칙6(대조군 유동성매칭)은 이 스크립트엔 해당 없음(대조군과
비교하는 게 아니라 실제 히트 코호트를 KR/US로만 쪼개는 측정이라 대조군
자체가 없음). 규칙7(z검정)은 모든 KR vs US 비교에 harness.ev_gap_zscore를
쓴다. 규칙8(KR+US 혼합 코호트는 반드시 시장별 분해 병기)이 이 스크립트의
존재 이유 그 자체.

【측정 1】E 게이트 증분(v5.71, cohort_b=E\\A, 혼합 EV 0.235R, n=869)의 KR/US
분해. v5.71부터 `scanner.analyze()`(눌림목)가 이미 게이트 E를 프로덕션으로
쓰고 있고, 히트 dict에 `rs_path`("12M"|"3M"|"momentum")를 직접 노출한다
(docs/rs_gate_e_and_depth_atr_v5.71.md) — 2026-08-23 스크립트가 썼던
CFG_NO_RS_GATE 우회 없이, rs_path=="3M" 또는 "momentum"인 실제 프로덕션
히트만 모으면 그게 곧 E\\A 증분이다(더 프로덕션에 충실한 방법).

【측정 2】전 탭 KR/US 분해 총정리:
  - 눌림목/슈퍼대장: 이미 KR/US 분해 완료(docs/pullback_ev_kr_us_regime_
    investigation.md) — 재계산 없이 그대로 인용.
  - 돌파임박/돌파/박스돌파/추세전환: 미분해 — 이번에 각 탭 현재 프로덕션
    CONFIG(BREAKOUT_CONFIG/BOXBREAK_CONFIG/IMMINENT_CONFIG/TURN_CONFIG,
    v5.70의 rs_min 탭별 분리 반영된 최신값) 그대로 새로 측정.
  - E 증분: 측정 1 재사용.
  - depth_atr 증분: v5.71 전 "고정%눌림폭 vs depth_atr[0.5,3.0]" 비교
    (원 측정 0.194R, n=480, RS는 A단독 유지)를 KR/US로 분해. depth_atr는
    이미 프로덕션 게이트라 "무력화"가 불가능해졌으므로, depth_atr 게이트만
    범위를 넓혀(min=-999,max=999) 전부 통과시킨 뒤, hit 딕셔너리가 직접
    주는 `pullback_pct`/`depth_atr` 실값으로 사후 분류한다 — v5.71에서
    제거된 옛 고정값(pullback_min=0.03/pullback_max_kr=0.15/
    pullback_max_us=0.12, git log -S 확인)만 이 스크립트에 상수로 복원
    (CLAUDE.md 리터럴 사본 원칙 — scanner.py엔 더 이상 없는 값이라 동기화
    대상 아님, 히스토리컬 재현 목적으로만 하드코딩했음을 명시).

공통 하네스(harness.py) 재사용 — RS/2R레이스/체크포인트/저유동성필터
새로 구현 안 함. checkpoints(60,250,10) 20개 지점, 전부 실제
analyze()/analyze_turnaround()/analyze_breakout()/analyze_boxbreak()/
analyze_imminent() 함수를 그대로 호출(cfg 주입 지점만 사용, 게이트
재구현 아님).

실행: 리포 루트에서
  python3 scripts/measurements/2026-08-29_kr_us_decomposition_final.py
(전체 유니버스 fetch 5~7분 + RS 사전계산(offset 40개: 20+lookback20) +
5개 탭 × 20 체크포인트 analyze 호출. 30~40분 예상)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import time

import harness
from scanner import (
    CONFIG, analyze,
    analyze_turnaround, TURN_CONFIG,
    analyze_breakout, BREAKOUT_CONFIG,
    analyze_boxbreak, BOXBREAK_CONFIG,
    analyze_imminent, IMMINENT_CONFIG,
)

OFFSETS = harness.checkpoints(60, 250, 10)      # 20개 — 전 측정 공용 표준 스펙
RS_DELTA_LOOKBACK = 20
RS_MIN_BARS = 200                               # rs_3m/rs_delta 사전계산용 trunc_cache 진입 문턱(2026-08-23 스크립트와 동일)

# v5.71 이전 존재했던 고정% 눌림폭 게이트 — git log -S'"pullback_max_kr"'로
# 확인한 마지막 값. scanner.py CONFIG엔 더 이상 없음(depth_atr로 완전
# 대체됨) — depth_atr 증분(옛 게이트 대비 신규 채택분)을 재현하려는
# 목적으로만 여기 하드코딩. scanner.py와 동기화할 대상 자체가 없으므로
# CLAUDE.md "CONFIG는 cfg[...]로 참조" 원칙 예외(주석으로 사유 명시).
OLD_PULLBACK_MIN = 0.03
OLD_PULLBACK_MAX_KR = 0.15
OLD_PULLBACK_MAX_US = 0.12

# depth_atr 게이트만 사실상 무제한으로 열어(현재 프로덕션 CONFIG는 이미
# depth_atr[0.5,3.0]가 게이트라 "무력화"가 필요) 전부 통과시키는 사본 —
# RS 게이트 등 나머지는 CONFIG 그대로.
CFG_DEPTH_WIDE_OPEN = dict(CONFIG)
CFG_DEPTH_WIDE_OPEN["depth_atr_min"] = -999.0
CFG_DEPTH_WIDE_OPEN["depth_atr_max"] = 999.0


def rs_3m_ranks(trunc_cache):
    """2026-08-23_reject_tracer_ev_and_gate_e.py와 동일 정의 — 3개월 수익률만의
    RS 백분위. scanner.py에 없는 파생 지표라 그 스크립트에서 그대로 재사용."""
    from scanner import to_rs_rank
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
    """OFFSETS ∪ (OFFSETS+20) 전체에 대해 rs_ranks/rs_moms/r3_ranks를 한 번에
    계산해 캐시 — 5개 탭이 전부 이 캐시를 공유(RS 계산은 탭 무관, 눌림목만
    r3/rs_delta를 추가로 씀)."""
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


# ══════════════════════════════════════════════════════════════════
# 측정 2의 "미분해 4개 탭" — 표준 히트 코호트를 KR/US로 나눠 수집
# ══════════════════════════════════════════════════════════════════
def collect_simple_tab(data, analyze_fn, cfg, rs_cache, label):
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


# ══════════════════════════════════════════════════════════════════
# 측정 1(E 증분) + depth_atr 증분 — 눌림목 analyze() 기반, 한 루프에서 같이 수집
# ══════════════════════════════════════════════════════════════════
def collect_pullback_increments(data, rs_cache, r3_cache):
    t0 = time.time()
    e_incr_kr, e_incr_us = [], []          # 측정1: rs_path in (3M, momentum)
    depth_incr_kr, depth_incr_us = [], []  # depth_atr 증분(옛 고정% 밖, 신규 depth_atr 범위 안)
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
            future = harness.future_after(df, off)

            # ── 측정 1: 표준 프로덕션 analyze()(게이트 E 그대로) — rs_path로 증분 판별 ──
            try:
                hit = analyze(hist, rs_rank=rs, rs_mom=rm, cfg=CONFIG, is_kr=ikr,
                              rs_3m=rs3m, rs_delta=rs_delta)
            except Exception:
                hit = None
            if hit is not None and harness.passes_liquidity_filter(hit, ikr):
                if hit.get("rs_path") in ("3M", "momentum"):
                    outcome = harness.race(hit["close"], hit["stop"], future)
                    (e_incr_kr if ikr else e_incr_us).append(outcome)

            # ── depth_atr 증분: RS는 A단독(12M) 유지 — rs_3m/rs_delta 생략하면
            # analyze() 내부 path_3m/path_mom이 자동으로 False가 돼 12M 게이트만 남음.
            # depth_atr 게이트는 와이드오픈, 사후에 옛 고정%밖·신depth_atr범위안만 채택.
            if rs is None or rs < CONFIG["rs_min"]:
                continue
            try:
                hit_wide = analyze(hist, rs_rank=rs, rs_mom=rm, cfg=CFG_DEPTH_WIDE_OPEN, is_kr=ikr)
            except Exception:
                hit_wide = None
            if hit_wide is None or not harness.passes_liquidity_filter(hit_wide, ikr):
                continue
            pb_frac = hit_wide.get("pullback_pct", 0.0) / 100.0
            old_max = OLD_PULLBACK_MAX_KR if ikr else OLD_PULLBACK_MAX_US
            within_old = OLD_PULLBACK_MIN <= pb_frac <= old_max
            if within_old:
                continue  # 옛 게이트로도 이미 통과 = 증분 아님
            da = hit_wide.get("depth_atr")
            if da is None or not (CONFIG["depth_atr_min"] <= da <= CONFIG["depth_atr_max"]):
                continue
            outcome = harness.race(hit_wide["close"], hit_wide["stop"], future)
            (depth_incr_kr if ikr else depth_incr_us).append(outcome)

        print(f"[pullback-incr] offset {off} 완료 ({oi+1}/{len(OFFSETS)}) "
              f"e_kr={len(e_incr_kr)} e_us={len(e_incr_us)} "
              f"depth_kr={len(depth_incr_kr)} depth_us={len(depth_incr_us)} "
              f"elapsed={time.time()-t0:.0f}s", flush=True)
    return e_incr_kr, e_incr_us, depth_incr_kr, depth_incr_us


def report_pair(label, kr_outcomes, us_outcomes):
    ev_kr = harness.ev_summary(kr_outcomes)
    ev_us = harness.ev_summary(us_outcomes)
    z, sig = harness.ev_gap_zscore(ev_kr, ev_us) if (ev_kr["nv"] and ev_us["nv"]) else (None, False)
    print(f"\n  {label}")
    print(f"    KR: n={ev_kr['n_hits']} (nv={ev_kr['nv']}) "
          f"EV={ev_kr['ev_R']:.3f}R" if ev_kr["ev_R"] is not None else f"    KR: n={ev_kr['n_hits']} EV=N/A(표본부족)")
    print(f"    US: n={ev_us['n_hits']} (nv={ev_us['nv']}) "
          f"EV={ev_us['ev_R']:.3f}R" if ev_us["ev_R"] is not None else f"    US: n={ev_us['n_hits']} EV=N/A(표본부족)")
    if z is not None:
        print(f"    gap(US-KR)={ev_us['ev_R']-ev_kr['ev_R']:.3f}R  z={z:.2f}  {'유의(|z|>=1.96)' if sig else '유의하지 않음'}")
    else:
        print("    z검정 불가(표본부족)")
    return ev_kr, ev_us, z, sig


if __name__ == "__main__":
    _t0 = time.time()
    data, kr_u, us_u = harness.fetch_universe_data()
    bench = harness.fetch_kr_benchmarks()
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None

    rs_cache, r3_cache = precompute_rs(data, kospi_close, kosdaq_close)

    print("\n" + "=" * 70)
    print("미분해 4개 탭 — 표준 코호트 KR/US 수집")
    print("=" * 70)
    turn_kr, turn_us = collect_simple_tab(data, analyze_turnaround, TURN_CONFIG, rs_cache, "추세전환")
    brk_kr, brk_us = collect_simple_tab(data, analyze_breakout, BREAKOUT_CONFIG, rs_cache, "돌파")
    box_kr, box_us = collect_simple_tab(data, analyze_boxbreak, BOXBREAK_CONFIG, rs_cache, "박스돌파")
    imm_kr, imm_us = collect_simple_tab(data, analyze_imminent, IMMINENT_CONFIG, rs_cache, "돌파임박")

    print("\n" + "=" * 70)
    print("측정1(E 증분) + depth_atr 증분 — 눌림목 analyze() 기반 수집")
    print("=" * 70)
    e_kr, e_us, depth_kr, depth_us = collect_pullback_increments(data, rs_cache, r3_cache)

    print("\n" + "=" * 70)
    print("【측정 1】E 게이트 증분(v5.71, 혼합 EV 0.235R, n=869) KR/US 분해")
    print("=" * 70)
    report_pair("E\\A 증분 (rs_path in {3M, momentum})", e_kr, e_us)

    print("\n" + "=" * 70)
    print("【측정 2】전 탭 KR/US 분해 총정리")
    print("=" * 70)
    print("\n  -- 기존 측정 인용(재계산 안 함, docs/pullback_ev_kr_us_regime_investigation.md) --")
    print("  눌림목    KR: n=636  EV=0.002R   US: n=1328 EV=0.206R   z≈2.95(유의)")
    print("  슈퍼대장  KR: n=103  EV=-0.214R  US: n=205  EV=+0.346R  z≈3.36(유의)")
    print("\n  -- 이번에 새로 측정 --")
    report_pair("돌파임박", imm_kr, imm_us)
    report_pair("돌파", brk_kr, brk_us)
    report_pair("박스돌파", box_kr, box_us)
    report_pair("추세전환", turn_kr, turn_us)
    report_pair("E 증분(위와 동일, 재출력)", e_kr, e_us)
    report_pair("depth_atr 증분(v5.71 이전 고정% 대비, RS는 A단독 유지)", depth_kr, depth_us)

    print(f"\n[main] 전체 완료, elapsed={time.time()-_t0:.0f}s", flush=True)
