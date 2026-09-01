"""
KR 돌파 계열 다중 히트(🔱) 보너스 규칙9 재검증 — 90개 체크포인트 (2026-09-01)

배경: `docs/kr_us_strategy_map.md` "20개 창 채택 결론 재검증 대기 목록"
우선순위3. 원측정(`2026-08-31_kr_breakout_family_multi_hit_ev.py`,
checkpoints(60,250,10)=20개)은 "≥2개 히트" 풀링 EV가 "1개 히트"보다
+0.279R 높고 z=2.82(유의), 시간반분 양쪽 다 +0.15R 이상 재현돼 채택됐다
(v5.114 카드 배지 + 정렬 가점, v5.115 표본작음 툴팁 경고 추가). 3개
히트(돌파+박스돌파+추세전환 전부) 코호트는 원측정에서 n=33으로 이미
작았다 — 이 스크립트는 정확히 같은 방법론을 90개 체크포인트로 재현해
표본이 어떻게 바뀌는지, 채택 판정이 유지되는지 확인한다.

【방법론 — 원측정과 완전 동일, 체크포인트 수 + fetch 깊이만 확대】
`analyze_breakout`/`analyze_boxbreak`/`analyze_turnaround` 각자의
프로덕션 CONFIG, 저유동성 필터, (off,ticker) 단위 동시히트 태깅 —
전부 원 스크립트 로직 그대로(재구현 안 함). 체크포인트만
`checkpoints(60,950,10)`=90개로 확대, `harness.fetch_universe_data`도
`kr_days=1900`로 확장(우선순위1 재검증에서 발견한 fetch-깊이 버그의
해결책 재사용 — harness.py에 옵션으로 승격됨, README 규칙3). KR
단일 시장이라 US 확장 fetch는 불필요(원측정과 동일하게 markets=("kr",)).

시간 반분 정의도 원측정과 같은 원칙(전체 범위를 정확히 반으로) —
90개 체크포인트를 오프셋 순으로 정렬해 앞 45개(off 60~500, "recent")/
뒤 45개(off 510~950, "earlier")로 분할(원측정 60~150/160~250과 동일한
비율 분할, 그냥 4.5배 확장).

근거 문서: docs/kr_us_strategy_map.md (재검증 결과 절 추가 예정)
"""
import os
import sys
import time
from itertools import combinations

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
RECENT_HALF = set(OFFSETS[:_mid])               # off 60~500 (45개)
EARLIER_HALF = set(OFFSETS[_mid:])              # off 510~950 (45개)
FAMILY = [
    ("돌파", analyze_breakout, BREAKOUT_CONFIG),
    ("박스돌파", analyze_boxbreak, BOXBREAK_CONFIG),
    ("추세전환", analyze_turnaround, TURN_CONFIG),
]
LABELS = [f[0] for f in FAMILY]


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


def collect_family(data, rs_cache):
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
        if (oi + 1) % 10 == 0 or oi == len(OFFSETS) - 1:
            log(f"[collect] {oi+1}/{len(OFFSETS)} 누적={len(records)} elapsed={time.time()-t0:.0f}s")
    return records


def build_hit_map(records):
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
        log(f"    {tag_}: n={ev['n_hits']} (nv={ev['nv']}) EV={ev['ev_R']:.3f}R "
            f"손절률={ev['stop_rate']*100:.1f}% 목표도달률={ev['target_rate']*100:.1f}%")
    else:
        log(f"    {tag_}: n={ev['n_hits']} EV=N/A(표본부족)")
    return ev


def jaccard(a, b):
    if not a and not b:
        return None
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else None


def main():
    t0 = time.time()
    data, kr_u, us_u = harness.fetch_universe_data(markets=("kr",), kr_days=1900)
    bench = harness.fetch_kr_benchmarks(days=1900)
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None
    log(f"[main] 유니버스 fetch 완료 {len(data)}종목 elapsed={time.time()-t0:.0f}s")

    rs_cache = precompute_rs(data, kospi_close, kosdaq_close)

    log("\n" + "=" * 70)
    log("KR 돌파 계열(돌파+박스돌파+추세전환) 히트 수집 + 동시히트 태깅")
    log("=" * 70)
    records = collect_family(data, rs_cache)
    hit_map = build_hit_map(records)
    records = tag(records, hit_map)
    log(f"\n총 {len(records)}건(탭단위) 수집, (off,ticker) 조합 {len(hit_map)}개")

    log("\n" + "=" * 70)
    log("【측정 1】 동시 히트 개수별(1/2/3) EV·승률·손절률·n, 단조성")
    log("=" * 70)
    ev_by_count = {}
    for c in (1, 2, 3):
        outs = [r["outcome"] for r in records if r["hit_count"] == c]
        ev_by_count[c] = ev_line(outs, f"{c}개 히트")
    if all(ev_by_count[c]["ev_R"] is not None for c in (1, 2, 3)):
        e1, e2, e3 = ev_by_count[1]["ev_R"], ev_by_count[2]["ev_R"], ev_by_count[3]["ev_R"]
        mono = e1 <= e2 <= e3
        log(f"    단조성(EV 1개<=2개<=3개): {'예' if mono else '아니오'} "
            f"({e1:.3f}R / {e2:.3f}R / {e3:.3f}R)")

    log("\n" + "=" * 70)
    log("【측정 1-pooled】 사전판정용 — 1개 vs 2개+ 풀링")
    log("=" * 70)
    out_1 = [r["outcome"] for r in records if r["hit_count"] == 1]
    out_2plus = [r["outcome"] for r in records if r["hit_count"] >= 2]
    ev_1 = ev_line(out_1, "1개 히트")
    ev_2plus = ev_line(out_2plus, "2개+ 히트(풀링)")
    pooled_gap = pooled_z = pooled_sig = None
    if ev_1["ev_R"] is not None and ev_2plus["ev_R"] is not None:
        pooled_gap = ev_2plus["ev_R"] - ev_1["ev_R"]
        pooled_z, pooled_sig = harness.ev_gap_zscore(ev_1, ev_2plus)
        z_s = f"{pooled_z:.2f}" if pooled_z is not None else "N/A"
        log(f"    격차(2개+ - 1개)={pooled_gap:.3f}R  z={z_s}  "
            f"{'유의(|z|>=1.96)' if pooled_sig else '유의하지 않음'}")
    log("\n  [원측정(20개 체크포인트) 대비] 1개 n=511 EV=0.252R / 2개+ n=395 EV=0.532R "
        "/ 격차=+0.279R z=2.82(유의)")

    log("\n" + "=" * 70)
    log("【측정 2】 조합별 분해 — 2개 히트의 정확한 쌍, 3개 히트 조합")
    log("=" * 70)
    pairs = list(combinations(sorted(LABELS), 2))
    for p in pairs:
        outs = [r["outcome"] for r in records if r["hit_count"] == 2 and r["combo"] == p]
        ev_line(outs, f"2개: {p[0]}+{p[1]}")
    outs3 = [r["outcome"] for r in records if r["hit_count"] == 3]
    ev_line(outs3, "3개: 돌파+박스돌파+추세전환(전체)")
    log("  [원측정 대비] 돌파+박스돌파 n=342 EV=0.462R / 돌파+추세전환 n=8 EV=0.500R / "
        "박스돌파+추세전환 n=12 EV=0.833R / 3개 n=33 EV=1.152R")

    log("\n" + "=" * 70)
    log("【측정 3】 중복성 진단 — 세 탭 히트 집합 자카드 유사도")
    log("=" * 70)
    hit_sets = {lb: set() for lb in LABELS}
    for key, labels in hit_map.items():
        for lb in labels:
            hit_sets[lb].add(key)
    for lb in LABELS:
        log(f"    {lb}: (off,ticker) {len(hit_sets[lb])}건")
    for a, b in pairs:
        j = jaccard(hit_sets[a], hit_sets[b])
        j_s = f"{j:.3f}" if j is not None else "N/A"
        log(f"    자카드({a}, {b}) = {j_s}")
    inter3 = hit_sets[LABELS[0]] & hit_sets[LABELS[1]] & hit_sets[LABELS[2]]
    union3 = hit_sets[LABELS[0]] | hit_sets[LABELS[1]] | hit_sets[LABELS[2]]
    j3 = len(inter3) / len(union3) if union3 else None
    j3_s = f"{j3:.3f}" if j3 is not None else "N/A"
    log(f"    자카드(3탭 동시) = {j3_s} (교집합 {len(inter3)}건 / 합집합 {len(union3)}건)")

    log("\n" + "=" * 70)
    log("【측정 4】 시간 반분 재현 — 1개 vs 2개+ 격차, RECENT/EARLIER 각각")
    log("=" * 70)
    half_results = {}
    for half_name in ("recent", "earlier"):
        log(f"\n  -- {half_name} --")
        h_records = [r for r in records if r["half"] == half_name]
        h1 = [r["outcome"] for r in h_records if r["hit_count"] == 1]
        h2 = [r["outcome"] for r in h_records if r["hit_count"] >= 2]
        ev_h1 = ev_line(h1, "1개 히트")
        ev_h2 = ev_line(h2, "2개+ 히트")
        if ev_h1["ev_R"] is None or ev_h2["ev_R"] is None:
            log("    표본 부족 — 이 절반은 검정 불가")
            half_results[half_name] = None
            continue
        gap = ev_h2["ev_R"] - ev_h1["ev_R"]
        z, sig = harness.ev_gap_zscore(ev_h1, ev_h2)
        z_s = f"{z:.2f}" if z is not None else "N/A"
        log(f"    격차(2개+ - 1개)={gap:.3f}R  z={z_s}  {'유의(|z|>=1.96, 참고)' if sig else '유의하지 않음(참고)'}")
        reproduced = gap >= 0.15
        log(f"    → {'재현됨(가설 방향, +0.15R 이상)' if reproduced else '재현 안 됨(기준 미달 또는 역방향)'}")
        half_results[half_name] = reproduced

    log("\n" + "=" * 70)
    log("【최종 판정 — 사전등록 기준 기계적 적용】")
    log("=" * 70)
    criteria_met = (
        pooled_gap is not None and pooled_gap >= 0.15
        and pooled_sig
        and half_results.get("recent") is True
        and half_results.get("earlier") is True
    )
    if criteria_met:
        log("    → 채택 유지: 다중 히트 우대.")
    else:
        log("    → 채택 철회: 동시 히트는 추가 정보 없음(또는 재현 실패).")
    log(f"    풀링 격차={pooled_gap if pooled_gap is not None else 'N/A'} "
        f"z={pooled_z if pooled_z is not None else 'N/A'} "
        f"유의={pooled_sig} 반분(recent/earlier)={half_results}")

    log(f"\n[main] 전체 완료, elapsed={time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
