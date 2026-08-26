"""
매집봉 필터 — 다른 시점 재현 확인 (2026-08-26, 사용자 지시)

`docs/maejip_candle_filter_kr.md` 판정: has_maejip 유/무 EV 격차 +0.245R,
z=1.94(사전기준 |z|≥1.96 바로 아래)로 기각하되 "재현 확인 후보"로 남김.
이 스크립트가 그 재현 확인이다.

원측정과의 차이: 원측정은 `harness.checkpoints(60, 250, 10)`(20개 체크
포인트)을 전부 풀링해 하나의 큰 표본(n=636)으로 잰 것이라, 특정 시점
조합에서만 나타나는 우연일 가능성을 배제 못 한다. 이번엔 그 풀링을 깨고
**단일 offset 3개(20/40/60거래일 전)를 각각 독립적으로** 재측정해 효과가
여러 개별 시점에서 반복되는지 본다.

【사전 판정 기준 — 실행 전 확정, 사용자 지시】
- offset 3개(20/40/60) 전부 gap(has_maejip=True EV − False EV)이 **양수**
  이고, "원측정과 같은 자릿수"로 사전 정의한 범위 **0.1R ≤ gap < 1.0R**
  (원측정 +0.245R와 같은 10^-1 자릿수)에 들면 → "경계선이지만 재현되는
  신호"로 승격. 하드 게이트가 아니라 카드 표시용 🕯️ 뱃지(매집봉 없음
  경고)로만 반영 + docs에 "배제 관점의 필터"로 기록.
- 부호가 하나라도 뒤집히거나(음수), 자릿수가 붕괴(< 0.1R로 사실상
  무신호, 또는 ≥ 1.0R로 비현실적 과대)하면 → 기각 확정, 재논쟁 방지
  노트로 격상.
- 개별 offset은 원측정(20개 체크포인트 풀링, n=636) 대비 표본이 1/20
  수준으로 작을 것으로 예상되어, z검정 단독 유의성은 이번 승격/기각
  판정 기준에 넣지 않는다(사전 명시) — z는 참고용으로만 병기한다.

harness.py 재사용(README 규칙3), scanner.py/app.py 무수정. mcap_band·
maejip_support·recency 하위 측정은 이번 재현 확인 범위 밖(원측정에서
이미 신호 없음으로 확인됨 — has_maejip 하나만 재검증).

실행: 리포 루트에서
`python3 scripts/measurements/2026-08-26_maejip_offset_replication_kr.py`
(KR 유니버스 fetch ~2.5분, mcap 스크래핑 없어 원측정보다 빠름)
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import harness
from scanner import analyze, CONFIG

# 원측정(checkpoints(60,250,10) 풀링)과 달리 단일 시점 3개를 독립 측정
OFFSETS = [20, 40, 60]
MIN_BARS_FLOOR = CONFIG["min_bars"]

# docs/maejip_candle_filter_kr.md와 동일 정의(원측정 스크립트 그대로 유지)
MAEJIP_CLOSE_PCT = 0.15
MAEJIP_HIGH_PCT = 0.20
MAEJIP_LIMIT_PCT = 0.29

GAP_SAME_ORDER_LOW = 0.1   # 원측정 +0.245R와 같은 자릿수 하한(사전 정의)
GAP_SAME_ORDER_HIGH = 1.0  # 상한(이 이상이면 비현실적 과대로 별개 취급)


def scan_maejip(hist):
    """hist: 신호일까지 truncate된 DataFrame. 가용 데이터 전체에서 매집봉을
    찾아 가장 최근 것이 있는지만 필요(원측정과 동일 로직, has_maejip만 씀)."""
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


def collect_hits_at_offset(data, bench, off):
    """단일 offset에서 KR 눌림목 히트 수집 + has_maejip 부착.
    원 스크립트의 collect_pullback_hits_kr을 단일 offset 버전으로 축소
    (여러 offset을 풀링하지 않는 것이 이번 재현 확인의 핵심)."""
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None

    b_kospi = harness.bench_score_at(kospi_close, off)
    b_kosdaq = harness.bench_score_at(kosdaq_close, off)

    trunc_cache = {}
    for t, df in data.items():
        if len(df) - off < MIN_BARS_FLOOR:
            continue
        trunc_cache[t] = harness.truncate_at(df, off)
    rs_ranks, rs_moms = harness.compute_rs_at_checkpoint(trunc_cache, b_kospi, b_kosdaq)

    hits = []
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
        hits.append({"ticker": t, "has_maejip": has_maejip, "outcome": outcome})
    return hits


def ev_of(hits):
    return harness.ev_summary([h["outcome"] for h in hits])


def judge_offset(off, hits):
    with_m = [h for h in hits if h["has_maejip"]]
    without_m = [h for h in hits if not h["has_maejip"]]
    ev_with = ev_of(with_m)
    ev_without = ev_of(without_m)
    gap, z = None, None
    if ev_with["nv"] and ev_without["nv"] and ev_with["ev_R"] is not None and ev_without["ev_R"] is not None:
        gap = ev_with["ev_R"] - ev_without["ev_R"]
        z, _ = harness.ev_gap_zscore(ev_without, ev_with)
    return {
        "off": off,
        "n_with": len(with_m), "nv_with": ev_with["nv"], "ev_with": ev_with["ev_R"],
        "n_without": len(without_m), "nv_without": ev_without["nv"], "ev_without": ev_without["ev_R"],
        "gap_R": gap, "z": z,
    }


def run(data, bench):
    results = []
    for off in OFFSETS:
        hits = collect_hits_at_offset(data, bench, off)
        j = judge_offset(off, hits)
        results.append(j)
        print(
            f"[offset={off}] with(n={j['n_with']},nv={j['nv_with']})=EV{j['ev_with']} "
            f"without(n={j['n_without']},nv={j['nv_without']})=EV{j['ev_without']} "
            f"gap={j['gap_R']} z={j['z']}", flush=True,
        )

    gaps = [r["gap_R"] for r in results]
    all_positive = all(g is not None and g > 0 for g in gaps)
    all_same_order = all(g is not None and GAP_SAME_ORDER_LOW <= g < GAP_SAME_ORDER_HIGH for g in gaps)
    if all_positive and all_same_order:
        verdict = "재현됨 — 배제관점 필터(🕯️ 뱃지) 승격 후보"
    else:
        verdict = "기각 확정 — 재논쟁 방지"
    print(f"\n[최종판정] {verdict}")
    print(f"gaps={gaps}")
    return results, verdict


if __name__ == "__main__":
    data, kr_u, us_u = harness.fetch_universe_data(markets=("kr",))
    bench = harness.fetch_kr_benchmarks()
    run(data, bench)
