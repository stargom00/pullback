"""
RS 게이트 E(v5.71) 규칙9 재검증 — 90개 체크포인트 (2026-09-01)

배경: `docs/kr_us_strategy_map.md` "20개 창 채택 결론 재검증 대기 목록"
우선순위2. RS 게이트 E(=A∪B∪C, A=12개월RS≥80/B=3개월RS≥80/C=RS≥50且
20거래일랭크상승≥25)는 원래 KR+US 혼합 20개 체크포인트로 채택됐고
(`docs/rs_gate_e_and_depth_atr_v5.71.md`, cohort b E\\A증분 EV=0.235R
(n=869) > cohort a A단독 EV=0.108R(n=1838)), 이후 규칙8 재분해에서 KR
z=0.99·US z=0.99 둘 다 비유의(`kr_us_strategy_map.md` "결과" 표의
"E 게이트 증분" 행)로 나왔다 — 근데 이것도 20개 체크포인트였다. 이
스크립트는 같은 cohort(b) 정의를 checkpoints(60,950,10)=90개로
재측정한다.

【방법론 — 원측정과 완전 동일】 `2026-08-23_reject_tracer_ev_and_gate_e.py`의
cohort(a)/(b) 정의를 그대로 재현 — depth_atr 재검증(우선순위1)과 달리
이번엔 CONFIG 자체를 건드릴 필요가 없다: `CFG_NO_RS_GATE`(rs_min=-999)로
`analyze()`의 RS 필터만 무력화해(이러면 path_12m이 항상 참이라 A∪B∪C
분기 전체가 사실상 우회됨) 나머지 게이트(눌림폭·이평선지지·RSI·리스크
등, v5.132 원복 후 현재 라이브 코드 그대로)를 통과한 후보를 얻고,
그 후보들을 `variant_flags(rs, rs3m, rs_delta)`로 **외부에서** A/E
재분류한다 — analyze() 자체의 판단이 아니라 원 데이터(rs/rs3m/rs_delta)로
직접 분류하므로 "무엇이 A를 통과하고 무엇이 E까지 가야 통과하는지"를
정확히 나눌 수 있다(원 스크립트와 동일 기법, 재구현 아님).
  cohort(a) = 후보 중 A(12개월 RS≥80) 실제로 만족
  cohort(b) = 후보 중 A는 불만족이지만 E(A∪B∪C)는 만족 — "E 덕분에
              새로 잡힌 증분"

【규칙6/7/8/9】 유동성(`harness.passes_liquidity_filter`), 유의성
(`harness.ev_gap_zscore`), KR/US 완전분리 보고, 90개 체크포인트
(`harness.checkpoints(60,950,10)`) — `harness.fetch_universe_data(kr_days=1900,
us_period="5y")`로 확장 fetch(우선순위1 재검증에서 발견한 버그의
해결책을 그대로 재사용 — harness.py에 옵션으로 승격됨, README 규칙3).

근거 문서: docs/kr_us_strategy_map.md (재검증 결과 절 추가 예정)
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import harness
from scanner import CONFIG, analyze, to_rs_rank

RS_DELTA_LOOKBACK = 20
RS_MIN_BARS = 200

CFG_NO_RS_GATE = dict(CONFIG)
CFG_NO_RS_GATE["rs_min"] = -999


def rs_3m_ranks(trunc_cache):
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


def variant_flags(rs, rs3m, rs_delta):
    """원 스크립트(2026-08-23_reject_tracer_ev_and_gate_e.py)와 동일 정의."""
    a = rs is not None and rs >= 80
    b = rs3m is not None and rs3m >= 80
    c = (rs is not None and rs >= 50) and (rs_delta is not None and rs_delta >= 25)
    e = a or b or c
    return a, e


def log(msg):
    print(msg, flush=True)


def main():
    t0 = time.time()
    data, kr_u, us_u = harness.fetch_universe_data(kr_days=1900, us_period="5y")
    bench = harness.fetch_kr_benchmarks(days=1900)
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None
    tickers = list(data.keys())
    log(f"[main] 유니버스 fetch 완료 {len(data)}종목 elapsed={time.time()-t0:.0f}s")

    offsets = harness.checkpoints(60, 950, 10)   # 90개(규칙9)
    extra = sorted(set(offsets) | {o + RS_DELTA_LOOKBACK for o in offsets})
    log(f"[main] 체크포인트 {len(offsets)}개, RS사전계산 {len(extra)}개 지점")

    rs_cache, r3_cache = {}, {}
    for i, off in enumerate(extra):
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
        if (i + 1) % 20 == 0 or i == len(extra) - 1:
            log(f"[rs] {i+1}/{len(extra)} elapsed={time.time()-t0:.0f}s")

    cohort_a, cohort_b = [], []
    for oi, off in enumerate(offsets):
        rs_ranks, rs_moms = rs_cache[off]
        r3_ranks = r3_cache[off]
        rs_20ago, _ = rs_cache.get(off + RS_DELTA_LOOKBACK, ({}, {}))

        for t in tickers:
            df = data[t]
            if len(df) - off < CONFIG["min_bars"]:
                continue
            ikr = harness.is_kr_ticker(t)
            hist = harness.truncate_at(df, off)
            rs = rs_ranks.get(t)
            rs3m = r3_ranks.get(t)
            rs_delta = (rs - rs_20ago.get(t)) if (rs is not None and t in rs_20ago) else None
            rm = rs_moms.get(t)
            future = harness.future_after(df, off)

            try:
                hit = analyze(hist, rs_rank=rs, rs_mom=rm, cfg=CFG_NO_RS_GATE, is_kr=ikr,
                               rs_3m=rs3m, rs_delta=rs_delta)
            except Exception:
                hit = None
            if hit is None or not harness.passes_liquidity_filter(hit, ikr):
                continue
            a, e = variant_flags(rs, rs3m, rs_delta)
            rec = {"ticker": t, "is_kr": ikr, "close": hit.get("close"),
                   "stop": hit.get("stop"), "future": future}
            if a:
                cohort_a.append(rec)
            elif e:
                cohort_b.append(rec)

        if (oi + 1) % 10 == 0 or oi == len(offsets) - 1:
            log(f"[ev] checkpoint {off} 완료 ({oi+1}/{len(offsets)}) elapsed={time.time()-t0:.0f}s "
                f"a={len(cohort_a)} b={len(cohort_b)}")

    report(cohort_a, cohort_b)
    log(f"\n[main] 총 소요시간 {time.time()-t0:.0f}s")


def report(cohort_a, cohort_b):
    def _ev(hits):
        outcomes = [harness.race(h["close"], h["stop"], h["future"]) for h in hits]
        return harness.ev_summary(outcomes)

    log("\n" + "=" * 70)
    log("결과 — RS게이트E 증분(cohort b=E\\A) KR/US 분해, 90개 체크포인트")
    log("=" * 70)
    b_kr = [h for h in cohort_b if h["is_kr"]]
    b_us = [h for h in cohort_b if not h["is_kr"]]
    ev_b_kr, ev_b_us = _ev(b_kr), _ev(b_us)
    log(f"  KR: n={ev_b_kr['n_hits']} EV={ev_b_kr['ev_R']:.3f}R" if ev_b_kr['ev_R'] is not None
        else f"  KR: n={ev_b_kr['n_hits']} EV=N/A(표본부족)")
    log(f"  US: n={ev_b_us['n_hits']} EV={ev_b_us['ev_R']:.3f}R" if ev_b_us['ev_R'] is not None
        else f"  US: n={ev_b_us['n_hits']} EV=N/A(표본부족)")
    if ev_b_kr['ev_R'] is not None and ev_b_us['ev_R'] is not None:
        gap = ev_b_us['ev_R'] - ev_b_kr['ev_R']
        z, sig = harness.ev_gap_zscore(ev_b_kr, ev_b_us)
        log(f"  gap(US-KR)={gap:+.3f}R  z={z:.2f}({'유의' if sig else '유의하지 않음'})" if z is not None
            else f"  gap(US-KR)={gap:+.3f}R  z=계산불가")
    log("\n  [원측정(20개 체크포인트) 대비]")
    log("  KR n=240 EV=0.025R / US n=540 EV=0.135R / gap=+0.110R / z=0.99(비유의)")

    log("\n" + "=" * 70)
    log("참고 — cohort(a) A단독 vs cohort(b) E\\A증분, KR+US 혼합(원채택 비교축)")
    log("=" * 70)
    ev_a_mixed = _ev(cohort_a)
    ev_b_mixed = _ev(cohort_b)
    log(f"  cohort(a) A단독: n={ev_a_mixed['n_hits']} EV={ev_a_mixed['ev_R']:.3f}R"
        if ev_a_mixed['ev_R'] is not None else f"  cohort(a): n={ev_a_mixed['n_hits']} EV=N/A")
    log(f"  cohort(b) E\\A증분: n={ev_b_mixed['n_hits']} EV={ev_b_mixed['ev_R']:.3f}R"
        if ev_b_mixed['ev_R'] is not None else f"  cohort(b): n={ev_b_mixed['n_hits']} EV=N/A")
    log("  [원측정 참고] cohort(a) A단독 n=1838 EV=0.108R / cohort(b) E\\A증분 n=869 EV=0.235R"
        " (2026-08-23, checkpoints 20개)")


if __name__ == "__main__":
    main()
