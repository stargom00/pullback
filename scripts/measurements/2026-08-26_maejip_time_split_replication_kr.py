"""
매집봉 필터 — 원측정 코호트 시간 분할 재현 확인 (2026-08-26, 사용자 지시)

지난 재현 시도(`2026-08-26_maejip_offset_replication_kr.py`, offset
20/40/60 개별 측정)는 모든 offset에서 n_without=1로 사실상 검정력이
없었다(`docs/maejip_candle_filter_kr.md` "표본 규모에 대한 중요한
캐비어트" 절 참고) — 사전 기준대로 기각은 확정했지만 절차적 기각이었지
실질적 반증은 아니었다.

이번엔 원측정과 **동일한 방법**(`harness.checkpoints(60, 250, 10)`,
20개 체크포인트 풀링, n≈636)으로 코호트를 그대로 재수집한 뒤, **시간
순으로 전반부/후반부로 반분**해 각 절반 안에서 has_maejip 유/무 EV
격차를 측정한다. 이렇게 하면 절반 표본이 각각 원측정의 절반 규모
(n≈300대)를 유지해 지난번의 n=1 문제를 피하면서, "효과가 전체 측정
기간에 걸쳐 안정적으로 존재하는가"를 검증할 수 있다.

【사전 판정 기준 — 실행 전 확정, 사용자 지시】
- 전반부·후반부 **둘 다** gap(has_maejip=True EV − False EV)이 양수
  **AND** 각각 **+0.1R 이상**이면 → "재현됨"으로 승격: 하드 게이트가
  아니라 카드 표시용 🕯️ 뱃지(매집봉 없음 경고)를 도입한다.
- 한쪽이라도 음수이거나 ~0(0.1R 미만)이면 → **최종 기각**. "3개월 후
  표본이 더 쌓이면 재측정 후보"로만 문서에 남기고 종결한다.

【시간 분할 방법】
원측정과 동일하게 20개 체크포인트로 KR 눌림목 히트를 전부 재수집
(signal_date 포함) → 전체 히트를 signal_date 오름차순 정렬 → 정확히
반으로 나눠 앞쪽 절반(더 과거 날짜)을 "전반부", 뒤쪽 절반(더 최근
날짜)을 "후반부"로 라벨링한다.

harness.py 재사용(README 규칙3), scanner.py/app.py 무수정. mcap 부착·
maejip_support·recency 하위 측정은 이번 범위 밖(has_maejip만 재검증).

실행: 리포 루트에서
`python3 scripts/measurements/2026-08-26_maejip_time_split_replication_kr.py`
(원측정과 동일한 20체크포인트 풀링이라 소요시간도 비슷 — mcap 스크래핑
없어 원측정보다는 빠름)
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import harness
from scanner import analyze, CONFIG

OFFSETS = harness.checkpoints(60, 250, 10)  # 원측정과 동일 — 코호트를 그대로 재수집
MIN_BARS_FLOOR = CONFIG["min_bars"]

# docs/maejip_candle_filter_kr.md와 동일 정의(원측정 스크립트 그대로 유지)
MAEJIP_CLOSE_PCT = 0.15
MAEJIP_HIGH_PCT = 0.20
MAEJIP_LIMIT_PCT = 0.29

GAP_MIN_R = 0.1  # 사전 정의: "재현됨" 승격에 필요한 절반당 최소 격차


def scan_maejip(hist):
    """hist: 신호일까지 truncate된 DataFrame. 가용 데이터 전체에서 매집봉이
    있는지만 필요(원측정과 동일 로직, has_maejip만 씀)."""
    close = hist["Close"]
    high = hist["High"]
    n = len(hist)
    found = False
    for i in range(1, n):
        prev_close = float(close.iloc[i - 1])
        if prev_close <= 0:
            continue
        c = float(close.iloc[i])
        h = float(high.iloc[i])
        close_chg = c / prev_close - 1
        high_chg = h / prev_close - 1
        if close_chg >= MAEJIP_CLOSE_PCT or high_chg >= MAEJIP_HIGH_PCT or close_chg >= MAEJIP_LIMIT_PCT:
            found = True
    return found


def collect_pullback_hits_kr(data, bench):
    """원측정(2026-08-26_maejip_candle_filter_kr.py)의 collect_pullback_hits_kr
    과 동일 — 20개 체크포인트를 풀링해 코호트를 재수집한다. mcap 부착만
    생략(이번 측정에 불필요)."""
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None
    tickers = list(data.keys())

    hits = []
    import time
    t0 = time.time()
    for oi, off in enumerate(OFFSETS):
        b_kospi = harness.bench_score_at(kospi_close, off)
        b_kosdaq = harness.bench_score_at(kosdaq_close, off)

        trunc_cache = {}
        for t in tickers:
            df = data[t]
            if len(df) - off < MIN_BARS_FLOOR:
                continue
            trunc_cache[t] = harness.truncate_at(df, off)
        rs_ranks, rs_moms = harness.compute_rs_at_checkpoint(trunc_cache, b_kospi, b_kosdaq)

        for t, hist in trunc_cache.items():
            rr = rs_ranks.get(t)
            rm = rs_moms.get(t)
            try:
                hit = analyze(hist, rs_rank=rr, rs_mom=rm, cfg=CONFIG, is_kr=True)
            except Exception:
                continue
            if hit is None:
                continue
            if not harness.passes_liquidity_filter(hit, is_kr=True):
                continue
            has_maejip = scan_maejip(hist)
            outcome = harness.race(hit.get("close"), hit.get("stop"), harness.future_after(data[t], off))
            hits.append({
                "ticker": t, "off": off, "signal_date": hist.index[-1],
                "has_maejip": has_maejip, "outcome": outcome,
            })
        print(f"[PASS1] off={off} hits_so_far={len(hits)} elapsed={time.time()-t0:.0f}s ({oi+1}/{len(OFFSETS)})", flush=True)
    return hits


def ev_of(hits):
    return harness.ev_summary([h["outcome"] for h in hits])


def judge_half(label, hits):
    with_m = [h for h in hits if h["has_maejip"]]
    without_m = [h for h in hits if not h["has_maejip"]]
    ev_with = ev_of(with_m)
    ev_without = ev_of(without_m)
    gap, z = None, None
    if ev_with["nv"] and ev_without["nv"] and ev_with["ev_R"] is not None and ev_without["ev_R"] is not None:
        gap = ev_with["ev_R"] - ev_without["ev_R"]
        z, _ = harness.ev_gap_zscore(ev_without, ev_with)
    return {
        "label": label,
        "n_with": len(with_m), "nv_with": ev_with["nv"], "ev_with": ev_with["ev_R"],
        "n_without": len(without_m), "nv_without": ev_without["nv"], "ev_without": ev_without["ev_R"],
        "gap_R": gap, "z": z,
    }


def run(data, bench):
    hits = collect_pullback_hits_kr(data, bench)
    daily_avg = round(len(hits) / len(OFFSETS), 1)
    print(f"[SUMMARY] KR 눌림목 히트 {len(hits)}건(일평균 {daily_avg}), "
          f"유니크 종목 {len({h['ticker'] for h in hits})}개", flush=True)

    hits_sorted = sorted(hits, key=lambda h: h["signal_date"])
    mid = len(hits_sorted) // 2
    first_half = hits_sorted[:mid]
    second_half = hits_sorted[mid:]
    print(f"[분할] 전반부(과거) n={len(first_half)} 기간="
          f"{first_half[0]['signal_date'].date()}~{first_half[-1]['signal_date'].date()}", flush=True)
    print(f"[분할] 후반부(최근) n={len(second_half)} 기간="
          f"{second_half[0]['signal_date'].date()}~{second_half[-1]['signal_date'].date()}", flush=True)

    j1 = judge_half("전반부", first_half)
    j2 = judge_half("후반부", second_half)
    for j in (j1, j2):
        print(
            f"[{j['label']}] with(n={j['n_with']},nv={j['nv_with']})=EV{j['ev_with']} "
            f"without(n={j['n_without']},nv={j['nv_without']})=EV{j['ev_without']} "
            f"gap={j['gap_R']} z={j['z']}", flush=True,
        )

    gaps = [j1["gap_R"], j2["gap_R"]]
    both_positive = all(g is not None and g > 0 for g in gaps)
    both_above_min = all(g is not None and g >= GAP_MIN_R for g in gaps)
    if both_positive and both_above_min:
        verdict = "재현됨 — 🕯️ 뱃지 도입"
    else:
        verdict = "최종 기각"
    print(f"\n[최종판정] {verdict} — gaps={gaps}")
    return j1, j2, verdict


if __name__ == "__main__":
    data, kr_u, us_u = harness.fetch_universe_data(markets=("kr",))
    bench = harness.fetch_kr_benchmarks()
    run(data, bench)
