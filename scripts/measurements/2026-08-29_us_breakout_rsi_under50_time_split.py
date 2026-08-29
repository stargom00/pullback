"""
US 확장 검증 — 사전 등록 (2026-08-29, 사용자 지시): "US 돌파 계열에서도
신호일 RSI<50 그룹 EV가 유의하게 낮다". 측정 스크립트만 — scanner.py/
app.py 미수정. 공통 하네스(harness.py) 재사용, RS/2R레이스/체크포인트/
저유동성필터 새로 구현 안 함(README 규칙3).

【배경】
`2026-08-29_kr_breakout_rsi_under50_time_split.py`(docs/kr_breakout_rsi_
investigation.md)에서 KR 돌파 계열의 RSI<50 저모멘텀 경고가 시간 반분
독립 재현으로 채택돼 v5.94에서 배지로 구현됐다(단 US는 미검증이라
market==='KR'로 명시적으로 배제). 이 스크립트는 같은 방법을 US 돌파
계열에 그대로 적용해 US 확장 여부를 판정한다.

【가설 — 측정 전 고정】
US 돌파 계열(돌파+박스돌파+추세전환)에서도 신호일 RSI<50 그룹 EV가
RSI≥50 그룹보다 유의하게 낮다.

【방법 — KR 때와 동일 설계】
US 돌파+박스돌파+추세전환 히트를 각자 프로덕션 CONFIG로 수집,
`checkpoints(60,250,10)` 20개 지점, 저유동성 필터 포함. RSI<50 vs ≥50
이분 + 시간 반분(이전 절반 off160~250 / 최근 절반 off60~150 — 세션
전체 공용 표준) 재현 확인. RSI 값은 hit["rsi"](analyze_breakout/
analyze_boxbreak/analyze_turnaround이 이미 계산해 내려주는 필드) 그대로
읽는다 — 재구현 없음.

【사전 판정 기준 — 측정 전 고정】
두 절반 모두 격차(RSI<50 EV − RSI≥50 EV) <= -0.15R 이면 → US에도
배지 확장. 한쪽이라도 미달이면 → "KR 전용 필터"로 docs 기록(현재
v5.94 구현이 이미 US를 배제하고 있으므로 이 경우 코드 변경 없음).

【규칙 준수】
- 규칙6: 해당 없음(대조군 비교 아님).
- 규칙7: harness.ev_gap_zscore 참고 병기.
- 규칙8: 이 측정은 US 단일 시장만 다뤄 해당 없음(KR과 섞지 않음 —
  KR 결과는 위 근거 문서에서 이미 별도로 확정돼 있음).

US RS 계산: `harness.compute_rs_at_checkpoint(trunc_cache, 0.0, 0.0)` —
KR 벤치마크 자리에 더미(0.0) 전달(US만 fetch해 KR 티커가 없으므로
무의미한 인자, `2026-08-26_pullback_us_ev_time_split.py`와 동일 관례).

실행: 리포 루트에서
  python3 scripts/measurements/2026-08-29_us_breakout_rsi_under50_time_split.py
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


def precompute_rs(data):
    """US 단독 — compute_rs_at_checkpoint(trunc_cache, 0.0, 0.0)로 KR
    벤치마크 인자 더미 전달(2026-08-26_pullback_us_ev_time_split.py와
    동일 관례, KR 티커가 없어 무의미한 값)."""
    t0 = time.time()
    tickers = list(data.keys())
    rs_cache = {}
    for off in OFFSETS:
        trunc_cache = {}
        for t in tickers:
            df = data[t]
            if len(df) - off < 200:
                continue
            trunc_cache[t] = harness.truncate_at(df, off)
        rs_ranks, rs_moms = harness.compute_rs_at_checkpoint(trunc_cache, 0.0, 0.0)
        rs_cache[off] = (rs_ranks, rs_moms)
        print(f"[rs-precompute] offset {off} 완료 elapsed={time.time()-t0:.0f}s", flush=True)
    return rs_cache


def collect_family(data, rs_cache):
    """US 돌파 계열 3개 탭 합산 히트 수집 — rsi/outcome/half 부착."""
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
                                      cfg=cfg, is_kr=False)
                except Exception:
                    hit = None
                if hit is None or not harness.passes_liquidity_filter(hit, False):
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
    data, kr_u, us_u = harness.fetch_universe_data(markets=("us",))

    rs_cache = precompute_rs(data)

    print("\n" + "=" * 70)
    print("US 돌파 계열(돌파+박스돌파+추세전환) 히트 + RSI/half 수집")
    print("=" * 70)
    records = collect_family(data, rs_cache)
    print(f"\n총 {len(records)}건 수집")

    print("\n" + "=" * 70)
    print("【사전 등록 검정】 시간 반분 재현 확인 — RSI<50 vs RSI>=50 (US)")
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
        print("  두 절반 모두 재현됨 → 채택: US 돌파 계열에도 "
              "'⚠️ 저모멘텀 돌파' 배지 확장 검토 대상.")
    else:
        print("  한쪽 이상 재현 안 됨 → 'KR 전용 필터' 확정 기록. "
              "US는 현재 구현(isLowMomentumBreakout의 market==='KR' 조건)을 유지, 코드 변경 없음.")

    print(f"\n[main] 전체 완료, elapsed={time.time()-_t0:.0f}s", flush=True)
