"""
KR 돌파 계열 다중 히트 EV (2026-08-31, 사용자 지시): 같은 날 돌파/박스돌파/
추세전환 중 2개 이상에 동시 히트한 종목이 단일 히트보다 EV가 높은가.
측정 스크립트만 — scanner.py/app.py 미수정. 공통 하네스(harness.py) 재사용,
RS/2R레이스/체크포인트/저유동성필터 새로 구현 안 함(README 규칙3).

【가설 — 측정 전 고정】
같은 체크포인트(off, 같은 날)에 돌파/박스돌파/추세전환 중 2개 이상 동시
히트한 종목이 1개만 히트한 종목보다 EV가 높다.

【코호트/방법 — 기존 KR 돌파 계열 스크립트와 동일 설계】
`2026-08-29_kr_breakout_family_rsi_ev.py`와 동일하게 analyze_breakout/
analyze_boxbreak/analyze_turnaround + 각자 프로덕션 CONFIG, checkpoints
(60,250,10), 저유동성 필터. 차이는 각 히트 레코드에 ticker를 남겨
(off, ticker) 단위로 그날 몇 개 탭이 동시 히트했는지 집계한다는 점뿐 —
탭별 히트 자체의 수집/필터링/2R레이스 로직은 전혀 새로 구현하지 않는다.

주의: 하나의 (off, ticker)가 2개 탭에 동시 히트하면, 각 탭의 개별 hit
레코드(각자의 entry/stop으로 따로 레이스한 결과)가 그대로 hit_count=2로
태깅되어 둘 다 포함된다 — "그 신호를 탭별로 실제로 받았을 때 결과가
어땠는가"를 측정하는 것이지, 중복 티커를 하나로 합치지 않는다(RSI
스크립트의 버킷 태깅과 동일 관례).

【시간 반분 정의 — 세션 전체 공용 표준, 재구현 아님】
RECENT_HALF = off 60~150 / EARLIER_HALF = off 160~250
(`2026-08-29_us_breakout_rsi_under50_time_split.py`와 동일 정의 재사용).

【사전 판정 기준 — 측정 전 고정】
"≥2개 히트" 풀링 그룹 EV가 "1개 히트" 그룹 EV보다 +0.15R 이상 높고,
z>=1.96(harness.ev_gap_zscore)이며, 시간 반분 양쪽 모두 같은 방향으로
격차 >=0.15R 재현되면 → "다중 히트 우대 채택"(카드 배지 + 정렬 가점
검토 권고, UI 구현은 이 스크립트 범위 밖). 미달이면 → "동시 히트는
추가 정보 없음"으로 기록.

【규칙 준수】
- 규칙6(대조군 유동성매칭): 해당 없음 — 대조군 비교가 아니라 실제
  프로덕션 히트를 동시 히트 개수로 나눠 보는 측정(전부 이미 저유동성
  필터 통과분).
- 규칙7(z검정): harness.ev_gap_zscore로 히트개수 그룹 간 EV 격차
  유의성 확인.
- 규칙8(KR+US 미혼합): 이 측정은 KR 단일 시장만 다뤄 해당 없음.

실행: 리포 루트에서
  python3 scripts/measurements/2026-08-31_kr_breakout_family_multi_hit_ev.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import time
from itertools import combinations

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
LABELS = [f[0] for f in FAMILY]


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
    """KR 돌파 계열 3개 탭 합산 히트 수집 — ticker/half/outcome 부착.
    반환: [{'label','off','ticker','half','outcome'}, ...]."""
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
                future = harness.future_after(df, off)
                outcome = harness.race(hit["close"], hit["stop"], future)
                records.append({"label": label, "off": off, "ticker": t,
                                 "half": half, "outcome": outcome})
        print(f"[collect] offset {off} 완료 ({oi+1}/{len(OFFSETS)}) 누적={len(records)} "
              f"elapsed={time.time()-t0:.0f}s", flush=True)
    return records


def build_hit_map(records):
    """(off, ticker) -> set(label)."""
    m = {}
    for r in records:
        key = (r["off"], r["ticker"])
        m.setdefault(key, set()).add(r["label"])
    return m


def tag(records, hit_map):
    for r in records:
        labels = hit_map[(r["off"], r["ticker"])]
        r["hit_count"] = len(labels)
        r["combo"] = tuple(sorted(labels))
    return records


def ev_line(outcomes, tag_):
    ev = harness.ev_summary(outcomes)
    if ev["ev_R"] is not None:
        print(f"    {tag_}: n={ev['n_hits']} (nv={ev['nv']}) EV={ev['ev_R']:.3f}R "
              f"손절률={ev['stop_rate']*100:.1f}% 목표도달률={ev['target_rate']*100:.1f}%")
    else:
        print(f"    {tag_}: n={ev['n_hits']} EV=N/A(표본부족)")
    return ev


def jaccard(a, b):
    if not a and not b:
        return None
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else None


if __name__ == "__main__":
    _t0 = time.time()
    data, kr_u, us_u = harness.fetch_universe_data(markets=("kr",))
    bench = harness.fetch_kr_benchmarks()
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None

    rs_cache = precompute_rs(data, kospi_close, kosdaq_close)

    print("\n" + "=" * 70)
    print("KR 돌파 계열(돌파+박스돌파+추세전환) 히트 수집 + 동시히트 태깅")
    print("=" * 70)
    records = collect_family(data, rs_cache)
    hit_map = build_hit_map(records)
    records = tag(records, hit_map)
    print(f"\n총 {len(records)}건(탭단위) 수집, (off,ticker) 조합 {len(hit_map)}개")

    print("\n" + "=" * 70)
    print("【측정 1】 동시 히트 개수별(1/2/3) EV·승률·손절률·n, 단조성")
    print("=" * 70)
    ev_by_count = {}
    for c in (1, 2, 3):
        outs = [r["outcome"] for r in records if r["hit_count"] == c]
        ev_by_count[c] = ev_line(outs, f"{c}개 히트")
    mono = None
    if all(ev_by_count[c]["ev_R"] is not None for c in (1, 2, 3)):
        e1, e2, e3 = ev_by_count[1]["ev_R"], ev_by_count[2]["ev_R"], ev_by_count[3]["ev_R"]
        mono = e1 <= e2 <= e3
        print(f"    단조성(EV 1개<=2개<=3개): {'예' if mono else '아니오'} "
              f"({e1:.3f}R / {e2:.3f}R / {e3:.3f}R)")
    print("  인접 z (참고):")
    for a, b in ((1, 2), (2, 3), (1, 3)):
        ea, eb = ev_by_count[a], ev_by_count[b]
        if ea["ev_R"] is not None and eb["ev_R"] is not None:
            z, sig = harness.ev_gap_zscore(ea, eb)
            z_s = f"{z:.2f}" if z is not None else "N/A"
            print(f"    {a}개 -> {b}개: 격차={eb['ev_R']-ea['ev_R']:.3f}R z={z_s} "
                  f"{'유의' if sig else '유의하지 않음'}")

    print("\n" + "=" * 70)
    print("【측정 1-pooled】 사전판정용 — 1개 vs 2개+ 풀링")
    print("=" * 70)
    out_1 = [r["outcome"] for r in records if r["hit_count"] == 1]
    out_2plus = [r["outcome"] for r in records if r["hit_count"] >= 2]
    ev_1 = ev_line(out_1, "1개 히트")
    ev_2plus = ev_line(out_2plus, "2개+ 히트(풀링)")
    pooled_gap = pooled_z = pooled_sig = None
    if ev_1["ev_R"] is not None and ev_2plus["ev_R"] is not None:
        pooled_gap = ev_2plus["ev_R"] - ev_1["ev_R"]
        pooled_z, pooled_sig = harness.ev_gap_zscore(ev_1, ev_2plus)
        z_s = f"{pooled_z:.2f}" if pooled_z is not None else "N/A"
        print(f"    격차(2개+ - 1개)={pooled_gap:.3f}R  z={z_s}  "
              f"{'유의(|z|>=1.96)' if pooled_sig else '유의하지 않음'}")

    print("\n" + "=" * 70)
    print("【측정 2】 조합별 분해 — 2개 히트의 정확한 쌍, 3개 히트 조합")
    print("=" * 70)
    pairs = list(combinations(sorted(LABELS), 2))
    for p in pairs:
        outs = [r["outcome"] for r in records if r["hit_count"] == 2 and r["combo"] == p]
        ev_line(outs, f"2개: {p[0]}+{p[1]}")
    outs3 = [r["outcome"] for r in records if r["hit_count"] == 3]
    ev_line(outs3, "3개: 돌파+박스돌파+추세전환(전체)")

    print("\n" + "=" * 70)
    print("【측정 3】 중복성 진단 — 세 탭 히트 집합 자카드 유사도")
    print("=" * 70)
    hit_sets = {lb: set() for lb in LABELS}
    for key, labels in hit_map.items():
        for lb in labels:
            hit_sets[lb].add(key)
    for lb in LABELS:
        print(f"    {lb}: (off,ticker) {len(hit_sets[lb])}건")
    for a, b in pairs:
        j = jaccard(hit_sets[a], hit_sets[b])
        j_s = f"{j:.3f}" if j is not None else "N/A"
        print(f"    자카드({a}, {b}) = {j_s}")
    inter3 = hit_sets[LABELS[0]] & hit_sets[LABELS[1]] & hit_sets[LABELS[2]]
    union3 = hit_sets[LABELS[0]] | hit_sets[LABELS[1]] | hit_sets[LABELS[2]]
    j3 = len(inter3) / len(union3) if union3 else None
    j3_s = f"{j3:.3f}" if j3 is not None else "N/A"
    print(f"    자카드(3탭 동시) = {j3_s} (교집합 {len(inter3)}건 / 합집합 {len(union3)}건)")

    print("\n" + "=" * 70)
    print("【측정 4】 시간 반분 재현 — 1개 vs 2개+ 격차, RECENT/EARLIER 각각")
    print("=" * 70)
    half_results = {}
    for half_name in ("recent", "earlier"):
        print(f"\n  -- {half_name} --")
        h_records = [r for r in records if r["half"] == half_name]
        h1 = [r["outcome"] for r in h_records if r["hit_count"] == 1]
        h2 = [r["outcome"] for r in h_records if r["hit_count"] >= 2]
        ev_h1 = ev_line(h1, "1개 히트")
        ev_h2 = ev_line(h2, "2개+ 히트")
        if ev_h1["ev_R"] is None or ev_h2["ev_R"] is None:
            print("    표본 부족 — 이 절반은 검정 불가")
            half_results[half_name] = None
            continue
        gap = ev_h2["ev_R"] - ev_h1["ev_R"]
        z, sig = harness.ev_gap_zscore(ev_h1, ev_h2)
        z_s = f"{z:.2f}" if z is not None else "N/A"
        print(f"    격차(2개+ - 1개)={gap:.3f}R  z={z_s}  {'유의(|z|>=1.96, 참고)' if sig else '유의하지 않음(참고)'}")
        reproduced = gap >= 0.15
        print(f"    → {'재현됨(가설 방향, +0.15R 이상)' if reproduced else '재현 안 됨(기준 미달 또는 역방향)'}")
        half_results[half_name] = reproduced

    print("\n" + "=" * 70)
    print("【최종 판정 — 사전등록 기준 기계적 적용】")
    print("=" * 70)
    criteria_met = (
        pooled_gap is not None and pooled_gap >= 0.15
        and pooled_sig
        and half_results.get("recent") is True
        and half_results.get("earlier") is True
    )
    if criteria_met:
        print("    → 채택: 다중 히트 우대. 카드 다중 히트 배지 표시 + 정렬 가점 검토 권고.")
    else:
        print("    → 미채택: 동시 히트는 추가 정보 없음(기록, 재논쟁 방지용).")
    print(f"    풀링 격차={pooled_gap if pooled_gap is not None else 'N/A'} "
          f"z={pooled_z if pooled_z is not None else 'N/A'} "
          f"유의={pooled_sig} 반분(recent/earlier)={half_results}")

    print(f"\n[main] 전체 완료, elapsed={time.time()-_t0:.0f}s", flush=True)
