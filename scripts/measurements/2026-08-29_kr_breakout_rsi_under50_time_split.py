"""
사전 등록 후속 검증 (2026-08-29, 사용자 지시): "KR 돌파 계열에서 신호일
RSI<50은 위험 신호다". 측정 스크립트만 — scanner.py/app.py 미수정. 공통
하네스(harness.py) 재사용, RS/2R레이스/체크포인트/저유동성필터 새로
구현 안 함(README 규칙3).

【배경 — 왜 재검증하나】
`2026-08-29_kr_breakout_family_rsi_ev.py`(docs/kr_breakout_rsi_investigation.md)
에서 사전 등록 안 된 사후 관찰로 "RSI<50 구간 EV -0.089R, 손절률 69.6%"
가 나왔다. 사전 등록 기준 없이 여러 구간을 훑다 눈에 띈 것 하나를
집는 사후 다이빙(post-hoc data dredging)은 우연일 위험이 크다 —
이번엔 가설·기준을 먼저 고정하고, **같은 코호트 재사용은 재확인일
뿐**이므로 시간을 반으로 갈라 독립적으로 재현되는지 본다
(`docs/maejip_candle_filter_kr.md` "코호트 시간 분할 재현 확인" 절과
동일 설계 — has_maejip이 원측정 z=1.94(경계선)였다가 시간 분할
재현에서 전반부 gap=-0.034R(역방향)·후반부 gap=+0.055R(미달)로 둘 다
사전기준 미달해 기각 확정됐던 바로 그 방식).

【사전 등록 가설 — 측정 전 고정】
"RSI<50 그룹 EV가 RSI≥50 그룹보다 유의하게 낮다."

【독립성 확보 — 시간 반분】
`docs/pullback_ev_kr_us_regime_investigation.md` 6절 이후 이 세션 전체가
써온 표준 반분(`checkpoints(60,250,10)`의 최근 절반=off60~150, 이전
절반=off160~250)을 그대로 재사용 — 새 기준 발명 안 함. 코호트는 원측정과
동일 정의(KR 돌파+박스돌파+추세전환 합산, 각자 프로덕션 CONFIG)로
**재수집**(원측정 실행 결과를 재사용하지 않고 새로 fetch — "같은 표본
재탕"이 아니라는 걸 보장).

【사전 판정 기준 — 측정 전 고정】
- 두 절반(이전/최근) **모두** 격차(RSI<50 EV − RSI≥50 EV)가 음수이고
  각각 **-0.15R 이상**(즉 0.15R 넘게 낮음)이면 → **채택**: KR 돌파
  계열 카드에서 RSI<50이면 "⚠️ 저모멘텀 돌파 — 손절률 높음" 표시
  (게이트 아님, 정보용 배지).
- 한쪽이라도 재현 안 되면(격차가 양수이거나 -0.15R 미만) → 관찰
  기록만 남기고 미반영.

규칙7(z검정)도 참고용으로 병기한다(README 규칙7 — 사전 기준 자체는
사용자가 크기 기준으로 고정했으므로 판정은 그 기준을 따르되, z는
"우연과 구분되는지" 추가 맥락으로 같이 낸다).

실행: 리포 루트에서
  python3 scripts/measurements/2026-08-29_kr_breakout_rsi_under50_time_split.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import time

import harness
from scanner import (
    analyze_breakout, BREAKOUT_CONFIG,
    analyze_boxbreak, BOXBREAK_CONFIG,
    analyze_turnaround, TURN_CONFIG,
)

OFFSETS = harness.checkpoints(60, 250, 10)      # 20개 — 전 측정 공용 표준 스펙
RECENT_HALF = set(range(60, 151, 10))           # 최근 절반(후반부) — 세션 전체 공용 정의
EARLIER_HALF = set(range(160, 251, 10))         # 이전 절반(전반부)
FAMILY = [
    ("돌파", analyze_breakout, BREAKOUT_CONFIG),
    ("박스돌파", analyze_boxbreak, BOXBREAK_CONFIG),
    ("추세전환", analyze_turnaround, TURN_CONFIG),
]


def precompute_rs(data, kospi_close, kosdaq_close):
    t0 = time.time()
    tickers = list(data.keys())
    rs_cache = {}
    for off in OFFSETS:
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
        print(f"[rs-precompute] offset {off} 완료 elapsed={time.time()-t0:.0f}s", flush=True)
    return rs_cache


def collect_family(data, rs_cache):
    """KR 돌파 계열 3개 탭 합산 히트 재수집 — rsi/outcome/half 부착.
    2026-08-29_kr_breakout_family_rsi_ev.py의 collect_family와 동일 방법론,
    이번엔 half(recent/earlier) 태그를 추가로 붙인다."""
    t0 = time.time()
    records = []
    for oi, off in enumerate(OFFSETS):
        rs_ranks, rs_moms = rs_cache[off]
        half = "recent" if off in RECENT_HALF else ("earlier" if off in EARLIER_HALF else None)
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
                rsi_val = hit.get("rsi")
                if rsi_val is None:
                    continue
                future = harness.future_after(df, off)
                outcome = harness.race(hit["close"], hit["stop"], future)
                records.append({"label": label, "off": off, "half": half,
                                 "rsi": rsi_val, "outcome": outcome})
        print(f"[collect] offset {off} 완료 ({oi+1}/{len(OFFSETS)}) 누적={len(records)} "
              f"elapsed={time.time()-t0:.0f}s", flush=True)
    return records


def ev_line(outcomes, tag):
    ev = harness.ev_summary(outcomes)
    if ev["ev_R"] is not None:
        print(f"    {tag}: n={ev['n_hits']} (nv={ev['nv']}) EV={ev['ev_R']:.3f}R "
              f"손절률={ev['stop_rate']*100:.1f}% 목표도달률={ev['target_rate']*100:.1f}%")
    else:
        print(f"    {tag}: n={ev['n_hits']} EV=N/A(표본부족)")
    return ev


def half_check(records, half_name):
    print(f"\n  -- {half_name} --")
    lt50 = [r["outcome"] for r in records if r["rsi"] < 50]
    ge50 = [r["outcome"] for r in records if r["rsi"] >= 50]
    ev_lt50 = ev_line(lt50, "RSI<50")
    ev_ge50 = ev_line(ge50, "RSI>=50")
    if ev_lt50["ev_R"] is None or ev_ge50["ev_R"] is None:
        print("    표본 부족 — 이 절반은 검정 불가")
        return None
    gap = ev_lt50["ev_R"] - ev_ge50["ev_R"]   # 음수 = RSI<50이 더 낮음(가설 방향)
    z, sig = harness.ev_gap_zscore(ev_lt50, ev_ge50)  # z = (ge50-lt50)/se, 부호는 gap의 반대
    z_s = f"{-z:.2f}" if z is not None else "N/A"     # gap과 부호 맞춰서 표시(참고용)
    print(f"    격차(RSI<50 - RSI>=50)={gap:.3f}R  z={z_s}  {'유의(|z|>=1.96, 참고)' if sig else '유의하지 않음(참고)'}")
    reproduced = gap <= -0.15
    print(f"    → {'재현됨(가설 방향, -0.15R 이상 격차)' if reproduced else '재현 안 됨(기준 미달 또는 역방향)'}")
    return reproduced


if __name__ == "__main__":
    _t0 = time.time()
    data, kr_u, us_u = harness.fetch_universe_data(markets=("kr",))
    bench = harness.fetch_kr_benchmarks()
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None

    rs_cache = precompute_rs(data, kospi_close, kosdaq_close)

    print("\n" + "=" * 70)
    print("KR 돌파 계열 히트 재수집 (원측정과 독립적인 새 fetch)")
    print("=" * 70)
    records = collect_family(data, rs_cache)
    print(f"\n총 {len(records)}건 수집")

    print("\n" + "=" * 70)
    print("【사전 등록 검정】 시간 반분 재현 확인 — RSI<50 vs RSI>=50")
    print("=" * 70)
    print("  기준: 두 절반 모두 격차(RSI<50 - RSI>=50) <= -0.15R 이면 채택")

    earlier_records = [r for r in records if r["half"] == "earlier"]
    recent_records = [r for r in records if r["half"] == "recent"]
    earlier_ok = half_check(earlier_records, "이전 절반(off160~250)")
    recent_ok = half_check(recent_records, "최근 절반(off60~150)")

    print("\n" + "=" * 70)
    print("【사전 등록 판정】")
    print("=" * 70)
    if earlier_ok is True and recent_ok is True:
        print("  두 절반 모두 재현됨 → 채택: KR 돌파 계열 카드에 "
              "'⚠️ 저모멘텀 돌파 — 손절률 높음'(RSI<50, 정보용 배지) 표시 검토 대상.")
    else:
        print("  한쪽 이상 재현 안 됨 → 관찰 기록만 남기고 미반영 "
              "(원측정의 RSI<50 관찰은 사후 다이빙 위험이 있었다는 뜻).")

    print(f"\n[main] 전체 완료, elapsed={time.time()-_t0:.0f}s", flush=True)
