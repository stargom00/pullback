"""
KR 눌림목 월별 EV 변동(+0.69R<->-0.83R)의 성격 규명 (2026-08-31, 사용자 지시):
"국면"(지속성 있음)인지 "노이즈"(자기상관 없음)인지, 매크로 레짐 백테스트와
결합해서 판정. 측정 스크립트만 — scanner.py/app.py 미수정.

【사전 조사 결과 — 매크로 레짐 백테스트 인프라 부재】
사용자가 "진행 중"이라 언급한 "8셀 매크로 레짐 백테스트"를 docs/, git log,
scripts/measurements/ 전체에서 검색했으나 존재하지 않는다. macro_calendar.py는
캘린더 탭용 이벤트(FOMC/CPI 등) "생성기"일 뿐 레짐 분류/백테스트가 아니다.
8-cell/셀 구조를 가진 문서·스크립트가 이 레포에 하나도 없음을 확인
(grep "매크로", "regime", "레짐", "8셀", "cell_id" 전부 무결과 또는 무관한
결과). 이 인프라가 실제로는 아직 안 만들어진 것으로 판단 — Part 2(레짐셀
교차매핑)는 측정 불가로 명시하고, Part 1/3만 완결한다. 판정 로직상 Part 2가
필수 조건이라 사전등록된 "채택" 분기는 이번엔 도달 불가(거짓임이 아니라
측정 불가) — 아래 판정 절 참고.

【Part 1 — lag-1 자기상관】
KR 눌림목 월별 EV(docs/kr_us_strategy_map.md, commit e38f32b에서 이미 계산·
교차검증된 값, off=60~250 파이프라인)을 재사용 — 재수집 안 함(하네스
재사용 원칙과 동일 정신, 이미 두 독립 스크립트가 소수점 3자리까지 일치
확인한 값을 세 번째로 다시 fetch하는 건 낭비). KR 돌파 계열(돌파+박스돌파+
추세전환, 2026-08-31_kr_breakout_family_multi_hit_ev.py와 동일 수집 로직)은
이 스크립트에서 동일 off/월버킷 방식으로 새로 수집(월별 시계열 자체가
기존에 없었음).

【Part 3 — 직전 3개월 롤링 EV의 다음달 예측력】
Part 1의 두 시계열에 대해 각각 (M-2,M-1,M 3개월 평균) vs (M+1 실제) 상관계수.

【사전 판정 기준 — 측정 전 고정, 원문 그대로】
Part1에서 눌림목이 유의한 양의 자기상관 보이고 & Part2에서 유의한(z>=1.96)
셀 조합 있으면 -> "눌림목 계열은 레짐 스위치 필요" 채택(홈 매크로 카드
설계 스케치, 코드 미구현). 둘 다 미달 -> "월별 변동은 분산, 예측 불가"
기록, 12개월 평균 유지.
Part2가 인프라 부재로 측정 불가하므로, Part1이 아무리 강해도 이 사전조건
쌍을 기계적으로 "채택"까지 끌고 갈 수 없다 -> 판정은 "보류(Part2 데이터
없음)"로 기록하고, Part1/3 결과만으로 참고 가능한 잠정 해석을 남긴다.

【규칙 준수】
- 규칙6: 대조군 비교 아님(단일 시계열의 자기상관/예측력 검정) — 해당 없음.
- 규칙7: 자기상관·상관계수 유의성은 표준 근사식(SE~=1/sqrt(N), Bartlett)
  사용, 소표본(월수<=11) 한계를 결과에 명시.
- 규칙8: KR 단일 시장만 다룸(눌림목·돌파계열 모두 KR) — 해당 없음.

실행: 리포 루트에서
  python3 scripts/measurements/2026-08-31_kr_pullback_ev_persistence_regime.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import time
import json
from collections import defaultdict

import numpy as np

import harness
from scanner import (
    analyze_breakout, BREAKOUT_CONFIG,
    analyze_boxbreak, BOXBREAK_CONFIG,
    analyze_turnaround, TURN_CONFIG,
)

OFFSETS = harness.checkpoints(60, 250, 10)
FAMILY = [
    ("돌파", analyze_breakout, BREAKOUT_CONFIG),
    ("박스돌파", analyze_boxbreak, BOXBREAK_CONFIG),
    ("추세전환", analyze_turnaround, TURN_CONFIG),
]

# KR 눌림목 월별 EV — docs/kr_us_strategy_map.md (commit e38f32b) 재사용.
# n=559, off=60~250, 신호일 기준 월버킷. 3분위 스크립트와 소수점 3자리까지
# 교차검증된 값(원문 인용, 재계산 안 함).
PULLBACK_MONTHLY = {
    "2025-08": {"n": 39, "ev": 0.692},
    "2025-09": {"n": 85, "ev": 0.482},
    "2025-10": {"n": 85, "ev": 0.059},
    "2025-11": {"n": 58, "ev": 0.086},
    "2025-12": {"n": 50, "ev": -0.220},
    "2026-01": {"n": 57, "ev": 0.368},
    "2026-02": {"n": 55, "ev": 0.691},
    "2026-03": {"n": 52, "ev": -0.827},
    "2026-04": {"n": 41, "ev": 0.610},
    "2026-05": {"n": 28, "ev": -0.036},
    "2026-06": {"n": 9, "ev": -1.000},
}


def collect_breakout_family_monthly(data, kospi_close, kosdaq_close):
    """KR 돌파 계열(돌파+박스돌파+추세전환) 합산 히트 — 신호일 월버킷.
    2026-08-31_kr_breakout_family_multi_hit_ev.py의 collect_family()와
    동일 수집 로직(레이스/유동성필터/RS 전부 harness 재사용), 월별 outcome
    집계만 신규."""
    t0 = time.time()
    by_month = defaultdict(list)
    n_total = 0
    for oi, off in enumerate(OFFSETS):
        b_kospi = harness.bench_score_at(kospi_close, off)
        b_kosdaq = harness.bench_score_at(kosdaq_close, off)
        trunc_cache = {}
        for t, df in data.items():
            if len(df) - off < 200:
                continue
            trunc_cache[t] = harness.truncate_at(df, off)
        rs_ranks, rs_moms = harness.compute_rs_at_checkpoint(trunc_cache, b_kospi, b_kosdaq)
        for label, analyze_fn, cfg in FAMILY:
            for t, hist in trunc_cache.items():
                if len(hist) < cfg["min_bars"]:
                    continue
                try:
                    hit = analyze_fn(hist, rs_rank=rs_ranks.get(t), rs_mom=rs_moms.get(t),
                                      cfg=cfg, is_kr=True)
                except Exception:
                    hit = None
                if hit is None or not harness.passes_liquidity_filter(hit, True):
                    continue
                df_full = data[t]
                future = harness.future_after(df_full, off)
                outcome = harness.race(hit["close"], hit["stop"], future)
                signal_date = hist.index[-1]
                month = str(signal_date.date())[:7] if hasattr(signal_date, "date") else str(signal_date)[:7]
                by_month[month].append(outcome)
                n_total += 1
        print(f"[breakout-family] offset {off} 완료 ({oi+1}/{len(OFFSETS)}) 누적={n_total} "
              f"elapsed={time.time()-t0:.0f}s", flush=True)
    out = {}
    for m in sorted(by_month):
        ev = harness.ev_summary(by_month[m])
        out[m] = {"n": len(by_month[m]), "ev": ev["ev_R"]}
    return out


def lag1_autocorr(series_by_month):
    """월 순서대로 정렬된 EV 시퀀스의 lag-1 자기상관(Pearson) +
    Bartlett 근사 유의성(SE~=1/sqrt(N), N=페어 수)."""
    months = sorted(series_by_month.keys())
    evs = [series_by_month[m]["ev"] for m in months]
    if len(evs) < 4:
        return {"r": None, "n_pairs": len(evs) - 1, "note": "표본 부족(월<4)"}
    x = np.array(evs[:-1])
    y = np.array(evs[1:])
    r = float(np.corrcoef(x, y)[0, 1])
    n_pairs = len(x)
    se = 1.0 / np.sqrt(n_pairs)
    z = r / se
    sig = abs(z) >= 1.96
    return {"r": round(r, 4), "n_pairs": n_pairs, "se": round(float(se), 4),
            "z": round(float(z), 3), "significant": bool(sig)}


def rolling3_predict_next(series_by_month):
    """(M-2,M-1,M 평균) vs (M+1 실제) 상관계수."""
    months = sorted(series_by_month.keys())
    evs = [series_by_month[m]["ev"] for m in months]
    if len(evs) < 5:
        return {"r": None, "n_pairs": 0, "note": "표본 부족(월<5)"}
    rolling = []
    actual_next = []
    for i in range(2, len(evs) - 1):
        roll = (evs[i - 2] + evs[i - 1] + evs[i]) / 3.0
        rolling.append(roll)
        actual_next.append(evs[i + 1])
    if len(rolling) < 3:
        return {"r": None, "n_pairs": len(rolling), "note": "표본 부족(페어<3)"}
    r = float(np.corrcoef(np.array(rolling), np.array(actual_next))[0, 1])
    n_pairs = len(rolling)
    se = 1.0 / np.sqrt(n_pairs) if n_pairs > 0 else None
    z = r / se if se else None
    sig = (abs(z) >= 1.96) if z is not None else None
    return {"r": round(r, 4), "n_pairs": n_pairs,
            "se": round(se, 4) if se else None,
            "z": round(z, 3) if z is not None else None,
            "significant": sig}


if __name__ == "__main__":
    _t0 = time.time()
    data, kr_u, _ = harness.fetch_universe_data(markets=("kr",))
    bench = harness.fetch_kr_benchmarks()
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None

    print("\n" + "=" * 70)
    print("Part1a: KR 눌림목 월별 EV lag-1 자기상관 (재사용 시계열)")
    print("=" * 70)
    pb_autocorr = lag1_autocorr(PULLBACK_MONTHLY)
    print(f"  눌림목: {pb_autocorr}")

    print("\n" + "=" * 70)
    print("Part1b: KR 돌파 계열 월별 EV 수집 (신규)")
    print("=" * 70)
    breakout_monthly = collect_breakout_family_monthly(data, kospi_close, kosdaq_close)
    for m, v in sorted(breakout_monthly.items()):
        print(f"  {m}: {v}")
    bo_autocorr = lag1_autocorr(breakout_monthly)
    print(f"  돌파계열 lag-1 자기상관: {bo_autocorr}")

    print("\n" + "=" * 70)
    print("Part2: 매크로 레짐 8셀 백테스트 — 인프라 부재, 측정 불가")
    print("=" * 70)
    print("  docs/, scripts/measurements/, git log 전체 검색 결과 8셀 구조 없음.")
    print("  macro_calendar.py는 캘린더 이벤트 생성기이지 레짐 분류기가 아님.")

    print("\n" + "=" * 70)
    print("Part3: 직전 3개월 롤링 EV -> 다음달 예측력")
    print("=" * 70)
    pb_roll = rolling3_predict_next(PULLBACK_MONTHLY)
    bo_roll = rolling3_predict_next(breakout_monthly)
    print(f"  눌림목 롤링3개월 예측: {pb_roll}")
    print(f"  돌파계열 롤링3개월 예측: {bo_roll}")

    print("\n" + "=" * 70)
    print("판정")
    print("=" * 70)
    verdict = "보류(Part2 데이터 없음 — 매크로 레짐 8셀 인프라 미존재)"
    print(f"  {verdict}")
    print(f"  Part1 잠정 해석: 눌림목 자기상관 유의={pb_autocorr.get('significant')}, "
          f"돌파계열 자기상관 유의={bo_autocorr.get('significant')}")

    print(f"\n[main] 전체 완료, elapsed={time.time()-_t0:.0f}s", flush=True)

    with open("/tmp/kr_pullback_ev_persistence_regime_result.json", "w") as f:
        json.dump({
            "pullback_monthly": PULLBACK_MONTHLY,
            "breakout_family_monthly": breakout_monthly,
            "pullback_autocorr": pb_autocorr,
            "breakout_autocorr": bo_autocorr,
            "pullback_rolling3": pb_roll,
            "breakout_rolling3": bo_roll,
            "verdict": verdict,
        }, f, indent=2, default=str)
    print("[main] saved /tmp/kr_pullback_ev_persistence_regime_result.json")
