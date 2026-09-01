"""
KR 돌파 계열 RSI<50 저모멘텀 필터(v5.94) 규칙9 재검증 — 90개 체크포인트
(2026-09-01)

배경: `docs/kr_us_strategy_map.md` "20개 창 채택 결론 재검증 대기 목록"
우선순위4(마지막). 원측정(`docs/kr_breakout_rsi_investigation.md` "후속
— RSI<50 사전 등록 독립 검증")은 KR 돌파 계열(돌파+박스돌파+추세전환)
에서 RSI<50 그룹이 RSI≥50보다 EV가 유의하게 낮다는 가설을, 시간
반분(이전 off160~250 / 최근 off60~150)으로 검증해 채택했다 — 이전
절반 gap=-0.507R z=-3.15, 최근 절반 gap=-0.645R z=-2.97, 둘 다 사전
기준(gap≤-0.15R & |z|≥1.96) 충족. v5.94에서 `isLowMomentumBreakout()`
UI 배지로 구현됨(⚠️ 저모멘텀 돌파, 게이트 아님).

**재검증 대기 목록의 비고에 이미 적힌 한계**: "'시간분할 독립 재현'도
같은 20점을 반으로 나눈 것이라 진짜 별도 대표본은 아님" — 원측정
전체가 `checkpoints(60,250,10)`=20개 체크포인트를 10개씩 반으로 쪼갠
것이라, 두 반쪽 다 절대 표본 크기가 작다(RSI<50 그룹 n=41~94). 이
스크립트는 같은 반분 설계를 `checkpoints(60,950,10)`=90개(45개씩
반분)로 확대해 재현성을 다시 확인한다.

【방법론 — 원측정과 완전 동일】 `analyze_breakout`/`analyze_boxbreak`/
`analyze_turnaround` KR 전용, 각자 프로덕션 CONFIG, 저유동성 필터,
`hit["rsi"]` 그대로 읽음(재계산 안 함 — 세 함수 다 항상 포함하는 필드).
시간 반분은 원측정과 같은 원칙(전체 범위를 정확히 반으로) — 90개를
오프셋 순 정렬해 앞 45개(off 60~500, "최근"에 해당)/뒤 45개(off
510~950, "이전"에 해당)로 분할(다중히트 재검증 스크립트와 동일 분할
방식 재사용).

【규칙6/7/8/9】 유동성(`harness.passes_liquidity_filter`), 유의성
(`harness.ev_gap_zscore`), KR 단일시장(규칙8 해당없음 — 원측정도 KR
전용), 90개 체크포인트 + `harness.fetch_universe_data(kr_days=1900)`
확장 fetch(harness.py 옵션 재사용, README 규칙3).

근거 문서: docs/kr_us_strategy_map.md (재검증 결과 절 추가 예정)
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import harness
from scanner import (
    analyze_breakout, BREAKOUT_CONFIG,
    analyze_boxbreak, BOXBREAK_CONFIG,
    analyze_turnaround, TURN_CONFIG,
)

OFFSETS = harness.checkpoints(60, 950, 10)      # 90개(규칙9)
_mid = len(OFFSETS) // 2
RECENT_HALF = set(OFFSETS[:_mid])               # off 60~500 (45개) — 원측정 "최근"에 대응
EARLIER_HALF = set(OFFSETS[_mid:])              # off 510~950 (45개) — 원측정 "이전"에 대응
FAMILY = [
    ("돌파", analyze_breakout, BREAKOUT_CONFIG),
    ("박스돌파", analyze_boxbreak, BOXBREAK_CONFIG),
    ("추세전환", analyze_turnaround, TURN_CONFIG),
]
ADOPT_GAP_THRESHOLD = -0.15
ADOPT_Z_THRESHOLD = 1.96


def log(msg):
    print(msg, flush=True)


def precompute_rs(data, kospi_close, kosdaq_close):
    t0 = time.time()
    tickers = list(data.keys())
    rs_cache = {}
    for i, off in enumerate(OFFSETS):
        b_kospi = harness.bench_score_at(kospi_close, off)
        b_kosdaq = harness.bench_score_at(kosdaq_close, off)
        trunc_cache = {}
        for t in tickers:
            df = data[t]
            if len(df) - off < 200:
                continue
            trunc_cache[t] = harness.truncate_at(df, off)
        rs_ranks, rs_moms = harness.compute_rs_at_checkpoint(trunc_cache, b_kospi, b_kosdaq)
        rs_cache[off] = (rs_ranks, rs_moms)
        if (i + 1) % 20 == 0 or i == len(OFFSETS) - 1:
            log(f"[rs-precompute] {i+1}/{len(OFFSETS)} elapsed={time.time()-t0:.0f}s")
    return rs_cache


def collect(data, rs_cache):
    t0 = time.time()
    records = []
    for oi, off in enumerate(OFFSETS):
        rs_ranks, rs_moms = rs_cache[off]
        half = "recent" if off in RECENT_HALF else "earlier"
        for label, analyze_fn, cfg in FAMILY:
            for t, df in data.items():
                if len(df) - off < cfg["min_bars"]:
                    continue
                hist = harness.truncate_at(df, off)
                try:
                    hit = analyze_fn(hist, rs_rank=rs_ranks.get(t), rs_mom=rs_moms.get(t),
                                      cfg=cfg, is_kr=True)
                except Exception:
                    hit = None
                if hit is None or not harness.passes_liquidity_filter(hit, True):
                    continue
                rsi = hit.get("rsi")
                if rsi is None:
                    continue
                future = harness.future_after(df, off)
                outcome = harness.race(hit["close"], hit["stop"], future)
                records.append({"label": label, "off": off, "ticker": t, "half": half,
                                 "rsi": rsi, "outcome": outcome})
        if (oi + 1) % 10 == 0 or oi == len(OFFSETS) - 1:
            log(f"[collect] {oi+1}/{len(OFFSETS)} 누적={len(records)} elapsed={time.time()-t0:.0f}s")
    return records


def ev_line(outcomes, tag_):
    ev = harness.ev_summary(outcomes)
    if ev["ev_R"] is not None:
        log(f"    {tag_}: n={ev['n_hits']} (nv={ev['nv']}) EV={ev['ev_R']:.3f}R "
            f"손절률={ev['stop_rate']*100:.1f}% 목표도달률={ev['target_rate']*100:.1f}%")
    else:
        log(f"    {tag_}: n={ev['n_hits']} EV=N/A(표본부족)")
    return ev


def gap_check(records, tag_):
    lo = [r["outcome"] for r in records if r["rsi"] < 50]
    hi = [r["outcome"] for r in records if r["rsi"] >= 50]
    ev_lo = ev_line(lo, f"{tag_} RSI<50")
    ev_hi = ev_line(hi, f"{tag_} RSI>=50")
    if ev_lo["ev_R"] is None or ev_hi["ev_R"] is None:
        log(f"    {tag_}: 표본부족 — 검정 불가")
        return None
    gap = ev_lo["ev_R"] - ev_hi["ev_R"]
    z, sig = harness.ev_gap_zscore(ev_hi, ev_lo)
    z_s = f"{z:.2f}" if z is not None else "N/A"
    log(f"    격차(RSI<50 - RSI>=50)={gap:+.3f}R  z={z_s}  {'유의' if sig else '유의하지 않음'}")
    reproduced = gap <= ADOPT_GAP_THRESHOLD and sig
    log(f"    → {'재현됨(가설 방향, 기준 충족)' if reproduced else '재현 안 됨(기준 미달 또는 역방향)'}")
    return {"gap": gap, "z": z, "sig": sig, "reproduced": reproduced}


def main():
    t0 = time.time()
    data, kr_u, us_u = harness.fetch_universe_data(markets=("kr",), kr_days=1900)
    bench = harness.fetch_kr_benchmarks(days=1900)
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None
    log(f"[main] 유니버스 fetch 완료 {len(data)}종목 elapsed={time.time()-t0:.0f}s")

    rs_cache = precompute_rs(data, kospi_close, kosdaq_close)

    log("\n" + "=" * 70)
    log("KR 돌파 계열 히트 수집 (RSI 부착)")
    log("=" * 70)
    records = collect(data, rs_cache)
    log(f"\n총 {len(records)}건 수집")

    log("\n" + "=" * 70)
    log("【전체 풀링】 RSI<50 vs RSI>=50 (90개 체크포인트 전체)")
    log("=" * 70)
    pooled = gap_check(records, "전체")
    log("\n  [원측정(20개 체크포인트, 반분 각 10개) 대비] "
        "이전 절반(off160~250) gap=-0.507R z=-3.15 / 최근 절반(off60~150) gap=-0.645R z=-2.97")

    log("\n" + "=" * 70)
    log("【시간 반분 재현】 45개씩 두 절반 독립 검정")
    log("=" * 70)
    half_results = {}
    for half_name in ("recent", "earlier"):
        log(f"\n  -- {half_name} (45개 체크포인트) --")
        h_records = [r for r in records if r["half"] == half_name]
        half_results[half_name] = gap_check(h_records, half_name)

    log("\n" + "=" * 70)
    log("【최종 판정 — 사전등록 기준(원측정과 동일) 기계적 적용】")
    log("=" * 70)
    criteria_met = (
        half_results.get("recent") is not None and half_results["recent"]["reproduced"]
        and half_results.get("earlier") is not None and half_results["earlier"]["reproduced"]
    )
    if criteria_met:
        log("    → 채택 유지: RSI<50 저모멘텀 필터.")
    else:
        log("    → 채택 철회: 시간반분 재현 실패.")
    log(f"    반분 결과: {half_results}")
    log(f"    풀링(참고, 사전등록 기준 아님): {pooled}")

    log(f"\n[main] 전체 완료, elapsed={time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
