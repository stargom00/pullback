"""
KR 돌파 계열 — 신호일 RSI가 EV에 영향을 주는가 (2026-08-29, 사용자 지시).
측정 스크립트만 — scanner.py/app.py 미수정. 공통 하네스(harness.py) 재사용,
RS/2R레이스/체크포인트/저유동성필터 새로 구현 안 함(README 규칙3).

【배경】
사용자가 KR 돌파 신호 3종목이 전부 RSI 70 근처인 걸 보고 "과열이라 한
번 더 눌릴 확률"을 물음. "RSI 70+=위험"이라는 직관이 맞는지, 아니면
추세추종에서 높은 RSI는 강한 돌파의 증상일 뿐인지 데이터로 판정한다.

【코호트】
KR 돌파 계열(돌파+박스돌파+추세전환 합산) — docs/kr_us_strategy_map.md
사전 등록 검정에서 KR 채택 근거가 된 바로 그 코호트를 재사용
(`2026-08-29_breakout_vs_pullback_family_kr_us.py`의 collect_simple_tab과
동일 방법론: analyze_breakout/analyze_boxbreak/analyze_turnaround +
각자의 프로덕션 CONFIG, checkpoints(60,250,10), 저유동성 필터). 이
스크립트는 KR 유니버스만 fetch(US 불필요 — RS는 시장별로 독립 계산되므로
KR만 있어도 결과 동일, 2026-08-29 다른 스크립트들에서 이미 확인된 가정).

【RSI 값의 출처 — 재구현 금지】
`hit["rsi"]`는 analyze_breakout/analyze_boxbreak/analyze_turnaround이
이미 내부에서 `scanner.rsi(close, 14)`로 계산해 결과 딕셔너리에 넣어주는
필드를 그대로 읽는다(scanner.py: analyze_breakout 2618행 부근, boxbreak
2799행 부근, turnaround 2213행 부근 — 세 함수 다 `"rsi": round(cur_rsi, 1)`
존재 확인). RSI를 새로 계산하지 않는다.

【급등 연속일수 — 이 측정 전용 신규 파생지표, 참고교차용】
scanner.py에 없는 지표라 재구현 대상이 아니다(신규 분석축). 정의: 신호일
종가 기준으로 거슬러 올라가며 "전일 대비 상승 마감"이 연속되는 일수
(예: 오늘·어제·그제 다 전일比 상승이면 3일). 단순·투명한 정의를 그대로
쓴다 — 복잡한 모멘텀 지표 대신 "정말로 연속 추격 매수했는가"만 본다.

【규칙 준수】
- 규칙6(대조군 유동성매칭): 해당 없음 — 대조군 비교가 아니라 실제
  프로덕션 히트를 RSI 구간별로 나눠 보는 측정(전부 이미 저유동성
  필터 통과분).
- 규칙7(z검정): harness.ev_gap_zscore로 RSI 70+ vs <70 EV 격차 유의성 확인.
  4분위 인접구간 z도 참고용으로 같이 낸다.
- 규칙8(KR+US 미혼합): 이 측정은 KR 단일 시장만 다뤄 해당 없음(혼합 없음).

실행: 리포 루트에서
  python3 scripts/measurements/2026-08-29_kr_breakout_family_rsi_ev.py
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
FAMILY = [
    ("돌파", analyze_breakout, BREAKOUT_CONFIG),
    ("박스돌파", analyze_boxbreak, BOXBREAK_CONFIG),
    ("추세전환", analyze_turnaround, TURN_CONFIG),
]


def consec_up_days(close):
    """신호일(마지막 봉) 기준 연속 상승마감 일수 — 이 측정 전용 신규 지표
    (모듈 docstring 참고, scanner.py 재구현 아님)."""
    n = len(close)
    days = 0
    for i in range(n - 1, 0, -1):
        if float(close.iloc[i]) > float(close.iloc[i - 1]):
            days += 1
        else:
            break
    return days


def rsi_bucket(rsi_val):
    if rsi_val is None:
        return None
    if rsi_val < 50:
        return "<50"
    if rsi_val < 60:
        return "50-60"
    if rsi_val < 70:
        return "60-70"
    return "70+"


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
    """KR 돌파 계열 3개 탭 합산 히트 수집 — 각 히트에 rsi/consec_up/outcome
    부착. 반환: [{'label','off','rsi','consec_up','outcome'}, ...]."""
    t0 = time.time()
    records = []
    for oi, off in enumerate(OFFSETS):
        rs_ranks, rs_moms = rs_cache[off]
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
                cu = consec_up_days(hist["Close"])
                future = harness.future_after(df, off)
                outcome = harness.race(hit["close"], hit["stop"], future)
                records.append({"label": label, "off": off, "rsi": rsi_val,
                                 "consec_up": cu, "outcome": outcome})
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


if __name__ == "__main__":
    _t0 = time.time()
    data, kr_u, us_u = harness.fetch_universe_data(markets=("kr",))
    bench = harness.fetch_kr_benchmarks()
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None

    rs_cache = precompute_rs(data, kospi_close, kosdaq_close)

    print("\n" + "=" * 70)
    print("KR 돌파 계열(돌파+박스돌파+추세전환) 히트 + RSI/연속상승일 수집")
    print("=" * 70)
    records = collect_family(data, rs_cache)
    records = [r for r in records if r["rsi"] is not None]
    print(f"\n총 {len(records)}건 수집 (rsi 결측 제외)")

    print("\n" + "=" * 70)
    print("【측정 1】 RSI 70+ vs <70 이분 — EV/승률/손절률/n, z검정")
    print("=" * 70)
    rsi70_outcomes = [r["outcome"] for r in records if r["rsi"] >= 70]
    under70_outcomes = [r["outcome"] for r in records if r["rsi"] < 70]
    ev_70 = ev_line(rsi70_outcomes, "RSI 70+")
    ev_u70 = ev_line(under70_outcomes, "RSI <70")
    if ev_70["ev_R"] is not None and ev_u70["ev_R"] is not None:
        gap = ev_u70["ev_R"] - ev_70["ev_R"]  # 양수 = RSI70+가 더 나쁨
        z, sig = harness.ev_gap_zscore(ev_70, ev_u70)  # gap = u70 - 70
        z_s = f"{z:.2f}" if z is not None else "N/A"
        print(f"    격차(<70 - 70+)={gap:.3f}R  z={z_s}  {'유의(|z|>=1.96)' if sig else '유의하지 않음'}")
        adopt = (z is not None) and (gap > 0) and (z >= 1.96)
        print(f"    → {'기준 충족: KR 돌파에서 RSI 70+ 주의 카드 표시 후보' if adopt else '기준 미달: RSI는 돌파 판정과 무관(값 표시만 유지)'}")
    else:
        print("    표본 부족 — 검정 불가")

    print("\n" + "=" * 70)
    print("【측정 2】 RSI 4분위 EV — 단조성 확인")
    print("=" * 70)
    buckets = ["<50", "50-60", "60-70", "70+"]
    bucket_ev = {}
    for b in buckets:
        outs = [r["outcome"] for r in records if rsi_bucket(r["rsi"]) == b]
        bucket_ev[b] = ev_line(outs, b)
    print("  인접 구간 z (참고):")
    for i in range(len(buckets) - 1):
        a, b = bucket_ev[buckets[i]], bucket_ev[buckets[i + 1]]
        if a["ev_R"] is not None and b["ev_R"] is not None:
            z, sig = harness.ev_gap_zscore(a, b)
            z_s = f"{z:.2f}" if z is not None else "N/A"
            print(f"    {buckets[i]} → {buckets[i+1]}: 격차={b['ev_R']-a['ev_R']:.3f}R z={z_s}")
    lo, hi = bucket_ev["<50"], bucket_ev["70+"]
    if lo["ev_R"] is not None and hi["ev_R"] is not None:
        z, sig = harness.ev_gap_zscore(lo, hi)  # gap = hi(70+) - lo(<50), 부호 그대로 유지
        z_s = f"{z:.2f}" if z is not None else "N/A"
        print(f"  양극단(<50 vs 70+) 격차(70+ - <50)={hi['ev_R']-lo['ev_R']:.3f}R z={z_s}")

    print("\n" + "=" * 70)
    print("【측정 3】 참고 교차 — RSI 70+ 내부, 연속상승일수(3일+ 여부) 분해")
    print("=" * 70)
    rsi70_records = [r for r in records if r["rsi"] >= 70]
    chase3 = [r["outcome"] for r in rsi70_records if r["consec_up"] >= 3]
    nochase = [r["outcome"] for r in rsi70_records if r["consec_up"] < 3]
    ev_chase = ev_line(chase3, "RSI70+ & 연속상승 3일+(추격)")
    ev_nochase = ev_line(nochase, "RSI70+ & 연속상승 <3일")
    if ev_chase["ev_R"] is not None and ev_nochase["ev_R"] is not None:
        z, sig = harness.ev_gap_zscore(ev_chase, ev_nochase)  # gap = 비추격 - 추격, 부호 그대로 유지
        gap = ev_nochase["ev_R"] - ev_chase["ev_R"]
        z_s = f"{z:.2f}" if z is not None else "N/A"
        print(f"    격차(비추격 - 추격)={gap:.3f}R  z={z_s}  {'유의' if sig else '유의하지 않음'}")
        print("    → 이 분해로 'RSI 자체가 문제' vs '연속추격이 문제'를 구분(참고용, 사전판정 대상 아님)")
    else:
        print("    표본 부족 — 분해 불가")

    print(f"\n[main] 전체 완료, elapsed={time.time()-_t0:.0f}s", flush=True)
