"""
KR 눌림목 부진 원인 탐색 마무리 — 결정적 확인 (2026-08-31, 사용자 지시).
긴 조사 스레드(베이스구조/유동성3분위/붕괴가설/월별자기상관 — 전부 기각
또는 역방향)를 닫기 위한 최종 측정. 두 가지를 6배 표본(1900일 확장,
2026-08-31_kr_pullback_support_breach_1900d.py와 동일 fetch/checkpoints
컨벤션, checkpoints 60~950 step10=90개)에서 재확인한다:

  1) KR 눌림목 vs US 눌림목 EV 격차 — 원래 관찰됐던 부진(KR 0.002R vs
     US 0.206R, z≈2.95, 2026-08-26 pullback_ev_cohort_and_pipeline_diff.md)이
     대표본에서도 재현되는지.
  2) "KR=돌파 계열 우위" 채택 결론(docs/kr_us_strategy_map.md, 2026-08-29
     breakout_vs_pullback_family_kr_us.py, KR 격차+0.283R z=4.88)이 같은
     6배 표본에서 유지되는지 — 원본은 checkpoints(60,250,10)=20개,
     harness.fetch_universe_data() 표준 fetch였음. 여기선 돌파계열(돌파+
     박스돌파+추세전환)과 되돌림계열(눌림목+돌파임박) 정의를 그대로
     재사용하고 checkpoints/fetch만 확장.

측정 스크립트만 — scanner.py/app.py 미수정.
규칙3(하네스와 다르게 재는 이유): KR/US 확장 fetch는 b911c4f와 완전히
동일(naver_kr.fetch_history(days=1900), yf.download(period="5y")) — 새로
만들지 않고 그대로 재사용.
규칙6: 전부 harness.passes_liquidity_filter 통과 히트만.
규칙7: harness.ev_gap_zscore 사용.
규칙8: KR/US 전 절 분리 보고, 혼합 수치 없음.

실행: 리포 루트에서
  python3 scripts/measurements/2026-08-31_kr_pullback_final_largesample_check.py
(5개 탭 × 90 체크포인트, 1900일/5y fetch — b911c4f보다 무거움, 장시간 예상)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import time

import naver_kr
from universe import get_universe

import harness
from scanner import (
    CONFIG, analyze, to_rs_rank,
    analyze_turnaround, TURN_CONFIG,
    analyze_breakout, BREAKOUT_CONFIG,
    analyze_boxbreak, BOXBREAK_CONFIG,
    analyze_imminent, IMMINENT_CONFIG,
)

OFFSETS = harness.checkpoints(60, 950, 10)   # 90개 — b911c4f와 동일 확장 규모
KR_FETCH_DAYS = 1900
US_FETCH_PERIOD = "5y"
RS_DELTA_LOOKBACK = 20
RS_MIN_BARS = 200


def is_kr(t):
    return harness.is_kr_ticker(t)


# ── b911c4f과 동일한 확장 fetch(그대로 복사 — README 규칙3: 하네스 고정
#    period로는 60~950 체크포인트를 못 감당해서 자체 구현) ──
def fetch_kr_long_universe(concurrency=10):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    kr_u = get_universe("kr")
    data = {}
    t0 = time.time()

    def _one(ticker):
        try:
            df = naver_kr.fetch_history(ticker, days=KR_FETCH_DAYS)
            if df is None or df.empty:
                return ticker, None
            return ticker, df
        except Exception:
            return ticker, None

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = {ex.submit(_one, t): t for t in kr_u}
        done = 0
        for fut in as_completed(futs):
            t, df = fut.result()
            if df is not None:
                data[t] = df
            done += 1
            if done % 300 == 0:
                print(f"[fetch-kr] {done}/{len(kr_u)} elapsed={time.time()-t0:.0f}s", flush=True)
    print(f"[fetch-kr] 완료 {len(data)}/{len(kr_u)} elapsed={time.time()-t0:.0f}s", flush=True)
    return data


def fetch_us_long_universe(batch_size=100):
    import yfinance as yf
    us_u = get_universe("us")
    us_tickers = list(us_u.keys())
    data = {}
    t0 = time.time()
    batches = [us_tickers[i:i + batch_size] for i in range(0, len(us_tickers), batch_size)]

    def _batch(tickers):
        out = {}
        if not tickers:
            return out
        try:
            raw = yf.download(tickers, period=US_FETCH_PERIOD, interval="1d",
                               auto_adjust=True, group_by="ticker",
                               threads=True, progress=False)
        except Exception:
            return out
        if raw is None or len(raw) == 0:
            return out
        single = len(tickers) == 1
        for t in tickers:
            try:
                df = raw.copy() if single else raw[t].copy()
                df = df.dropna(how="all")
                if df is None or df.empty or "Close" not in df.columns:
                    continue
                if df["Close"].dropna().empty:
                    continue
                out[t] = df[df["Close"].notna()]
            except Exception:
                continue
        return out

    for i, b in enumerate(batches):
        data.update(_batch(b))
        print(f"[fetch-us] batch {i+1}/{len(batches)} elapsed={time.time()-t0:.0f}s", flush=True)
    print(f"[fetch-us] 완료 {len(data)}/{len(us_tickers)} elapsed={time.time()-t0:.0f}s", flush=True)
    return data


def rs_3m_ranks(trunc_cache):
    kr3, us3 = {}, {}
    for t, hist in trunc_cache.items():
        r3 = harness.ret_pct(hist["Close"], 63)
        if r3 is None:
            continue
        if is_kr(t):
            kr3[t] = r3
        else:
            us3[t] = r3
    return {**to_rs_rank(kr3), **to_rs_rank(us3)}


def precompute_rs(data, kospi_close, kosdaq_close):
    t0 = time.time()
    tickers = list(data.keys())
    extra_offsets = sorted(set(OFFSETS) | {o + RS_DELTA_LOOKBACK for o in OFFSETS})
    rs_cache, r3_cache = {}, {}
    for oi, off in enumerate(extra_offsets):
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
        if (oi + 1) % 10 == 0 or oi == len(extra_offsets) - 1:
            print(f"[rs-precompute] {oi+1}/{len(extra_offsets)} offset={off} elapsed={time.time()-t0:.0f}s", flush=True)
    return rs_cache, r3_cache


def collect_simple_tab(data, analyze_fn, cfg, rs_cache, label):
    t0 = time.time()
    outcomes_kr, outcomes_us = [], []
    for oi, off in enumerate(OFFSETS):
        rs_ranks, rs_moms = rs_cache[off]
        for t, df in data.items():
            if len(df) - off < cfg["min_bars"]:
                continue
            ikr = is_kr(t)
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
        if (oi + 1) % 10 == 0 or oi == len(OFFSETS) - 1:
            print(f"[{label}] {oi+1}/{len(OFFSETS)} off={off} kr={len(outcomes_kr)} us={len(outcomes_us)} "
                  f"elapsed={time.time()-t0:.0f}s", flush=True)
    return outcomes_kr, outcomes_us


def collect_pullback(data, rs_cache, r3_cache):
    t0 = time.time()
    outcomes_kr, outcomes_us = [], []
    for oi, off in enumerate(OFFSETS):
        rs_ranks, rs_moms = rs_cache[off]
        r3_ranks = r3_cache[off]
        rs_20ago, _ = rs_cache.get(off + RS_DELTA_LOOKBACK, ({}, {}))
        for t, df in data.items():
            if len(df) - off < CONFIG["min_bars"]:
                continue
            ikr = is_kr(t)
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
        if (oi + 1) % 10 == 0 or oi == len(OFFSETS) - 1:
            print(f"[눌림목] {oi+1}/{len(OFFSETS)} off={off} kr={len(outcomes_kr)} us={len(outcomes_us)} "
                  f"elapsed={time.time()-t0:.0f}s", flush=True)
    return outcomes_kr, outcomes_us


def report_pair(label, kr_outcomes, us_outcomes):
    ev_kr = harness.ev_summary(kr_outcomes)
    ev_us = harness.ev_summary(us_outcomes)
    z, sig = harness.ev_gap_zscore(ev_kr, ev_us) if (ev_kr["nv"] and ev_us["nv"]) else (None, False)
    print(f"\n  {label}")
    print(f"    KR: n={ev_kr['n_hits']} (nv={ev_kr['nv']}) EV={ev_kr['ev_R']:.3f}R stop={ev_kr['stop_rate']:.1%}" if ev_kr["ev_R"] is not None else f"    KR: n={ev_kr['n_hits']} EV=N/A")
    print(f"    US: n={ev_us['n_hits']} (nv={ev_us['nv']}) EV={ev_us['ev_R']:.3f}R stop={ev_us['stop_rate']:.1%}" if ev_us["ev_R"] is not None else f"    US: n={ev_us['n_hits']} EV=N/A")
    if z is not None:
        print(f"    gap(US-KR)={ev_us['ev_R']-ev_kr['ev_R']:.3f}R  z={z:.2f}  {'유의' if sig else '비유의'}")
    return ev_kr, ev_us, z, sig


def within_market_gap(label, family_a_outcomes, family_b_outcomes):
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
    print("=" * 70)
    print(f"확장 fetch: KR days={KR_FETCH_DAYS}, US period={US_FETCH_PERIOD}, checkpoints={len(OFFSETS)}개")
    print("=" * 70)
    kr_data = fetch_kr_long_universe()
    us_data = fetch_us_long_universe()
    data = {**kr_data, **us_data}
    bench = harness.fetch_kr_benchmarks(days=KR_FETCH_DAYS)
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None

    print("\n" + "=" * 70)
    print("RS 사전계산 (5개 탭 공용)")
    print("=" * 70)
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
    print("1) KR vs US 눌림목 EV (대표본, 개별탭)")
    print("=" * 70)
    ev_pb_kr, ev_pb_us, z_pb, sig_pb = report_pair("눌림목", pb_kr, pb_us)
    print("  (참고: 2026-08-26 원측정 KR 0.002R vs US 0.206R, z≈2.95)")

    print("\n" + "=" * 70)
    print("개별 탭 EV (KR/US 분해, 참고용)")
    print("=" * 70)
    report_pair("돌파", brk_kr, brk_us)
    report_pair("박스돌파", box_kr, box_us)
    report_pair("추세전환", turn_kr, turn_us)
    report_pair("돌파임박", imm_kr, imm_us)

    breakout_family_kr = brk_kr + box_kr + turn_kr
    breakout_family_us = brk_us + box_us + turn_us
    pullback_family_kr = pb_kr + imm_kr
    pullback_family_us = pb_us + imm_us

    print("\n" + "=" * 70)
    print("2) 【원 사전등록 검정 재확인】 돌파 계열 vs 되돌림 계열 (대표본)")
    print("=" * 70)
    ev_a_kr, ev_b_kr, gap_kr, z_kr, sig_kr = within_market_gap("KR", breakout_family_kr, pullback_family_kr)
    ev_a_us, ev_b_us, gap_us, z_us, sig_us = within_market_gap("US", breakout_family_us, pullback_family_us)
    print("  (참고: 원측정 KR 격차+0.283R z=4.88, US 격차-0.008R z=-0.16, n_kr_brk=860 n_kr_pb=2554, checkpoints=20개)")

    print("\n" + "=" * 70)
    print("판정")
    print("=" * 70)
    kr_us_pb_gap_holds = (sig_pb and ev_pb_us and ev_pb_kr and ev_pb_us["ev_R"] is not None
                           and ev_pb_kr["ev_R"] is not None and ev_pb_us["ev_R"] > ev_pb_kr["ev_R"])
    family_gap_holds = (gap_kr is not None and z_kr is not None and gap_kr >= 0.1 and z_kr >= 1.96)
    print(f"  1) KR<US 눌림목 EV 격차 유지? {kr_us_pb_gap_holds} (z={z_pb})")
    print(f"  2) 'KR=돌파계열 우위'(z=4.88) 원결론 유지? {family_gap_holds} (격차={gap_kr}, z={z_kr})")
    if kr_us_pb_gap_holds or family_gap_holds:
        print("  => 부진 현상은 대표본에서도 재현됨. 원인 미규명이나 현상 자체는 견고 — 원인 탐색 종결.")
    else:
        print("  => 대표본에서 격차가 약화/소멸. 전략지도 재검토 필요.")

    print(f"\n[main] 전체 완료, elapsed={time.time()-_t0:.0f}s", flush=True)

    import json
    with open("/tmp/kr_pullback_final_largesample_check_result.json", "w") as f:
        json.dump({
            "pullback_kr": ev_pb_kr, "pullback_us": ev_pb_us, "pullback_gap_z": z_pb,
            "family_breakout_kr": ev_a_kr, "family_pullback_kr": ev_b_kr,
            "family_gap_kr": gap_kr, "family_gap_z_kr": z_kr,
            "family_breakout_us": ev_a_us, "family_pullback_us": ev_b_us,
            "family_gap_us": gap_us, "family_gap_z_us": z_us,
            "kr_us_pb_gap_holds": kr_us_pb_gap_holds, "family_gap_holds": family_gap_holds,
        }, f, default=str, indent=2)
    print("[main] 결과 JSON: /tmp/kr_pullback_final_largesample_check_result.json (커밋 대상 아님, 참고용)")
