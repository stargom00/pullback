"""
US 눌림목 즉시진입 — 2차 정렬 후보 3종 EV 상관 측정 (2026-09-08, 사용자 지시).

배경: 🔴 즉시 행동의 US 눌림목 히트가 손절폭(진입가 대비 %)만으로 정렬되는데,
그날그날 손절폭이 서로 거의 같아(예: -2.7~-3.3%) 변별이 안 되는 경우가 있었다
(entrySignal RS 배지는 근거 미검증으로 v5.213에서 제거 — docs/kr_us_strategy_map.md
"entrySignal RS 배지 제거" 절 참고). 2차 정렬 후보 3종이 실제 EV와 상관 있는지
직접 측정한다:
  1) RS (80~84 / 85~89 / 90~94 / 95+, US 눌림목 게이트가 이미 RS>=80을 요구하므로
     이 4구간이 전체를 덮는다)
  2) 피벗 대비 거리 — entry(=신호일 종가)/pivot 비율의 3분위(표본 자체의 관측
     분포로 3등분, 고정 임계값 아님)
  3) 20일선 이격 — (종가-20일 SMA)/20일 SMA %, 3분위(위와 동일 방식)

측정 대상은 US 눌림목(analyze(), CONFIG, is_kr=False)의 RS>=80 게이트를 통과한
전체 히트 — `_cache["us:pullback"]`이 실제로 담는 것과 동일 정의(app.py
get_calendar() ②-b 블록, run_scan("us","pullback")). KR은 대상 아님(사용자 지시
"US 눌림목 즉시진입 히트를 대상으로").

방법론(README 규칙9 표준 재사용, 새로 만들지 않음):
  - harness.py 재사용 — fetch/RS계산/저유동성필터/2R레이스/EV z검정 전부 하네스
    함수 그대로(scripts/measurements/README.md 규칙3).
  - checkpoints(60,950,10) = 90개, US만 대상이라 us_period="5y" 확장 fetch
    (kr_days는 불필요 — markets=("us",)).
  - RS_3m/RS_delta는 눌림목 게이트 변형(E)에 실제로 쓰이는 인자라
    2026-08-31_kr_pullback_final_largesample_check.py의 precompute 방식을
    그대로 재사용(그 스크립트를 새로 만들지 않고 US 전용으로 축소 재구현 —
    KR RS/벤치마크 차감 로직은 US엔 필요 없어 생략, harness.compute_rs_at_checkpoint
    자체는 그대로 호출).
  - 시기 반분: OFFSETS를 정확히 반으로 나눠(초반=최근 45개 offset, 후반=과거
    45개 offset) 각각 독립적으로 같은 버킷 비교를 재현.

사전 등록 판정 기준(사용자 지시, 실행 전 확정):
  각 후보의 "베스트-워스트" 구간 EV 격차가 +0.15R 이상이고, harness.ev_gap_zscore
  기준 |z|>=1.96, 그리고 초반/후반 반분에서 둘 다 같은 방향으로 |z|>=1.96(양쪽
  다 재현)이면 그 후보를 2차 정렬 기준으로 채택. 3개 후보 전부 미달이면
  손절폭 단일 정렬을 유지한다(app.py/static/index.html 변경 없음 — 이 스크립트는
  측정 전용).

실행: 리포 루트에서
  python3 scripts/measurements/2026-09-08_us_pullback_immediate_2nd_sort_candidates.py
(US 5년치 fetch + 90 체크포인트 — 장시간 예상)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import time

import pandas as pd

import harness
from scanner import CONFIG, analyze, to_rs_rank

OFFSETS = harness.checkpoints(60, 950, 10)   # 90개 — README 규칙9 표준
US_FETCH_PERIOD = "5y"
RS_DELTA_LOOKBACK = 20
RS_MIN_BARS = 200
MA_WINDOW = 20

RS_BUCKETS = [(80, 85), (85, 90), (90, 95), (95, 101)]  # [lo, hi) — 95+는 101로 상한 없앰


def rs3_ranks_us(trunc_cache):
    us3 = {}
    for t, hist in trunc_cache.items():
        r3 = harness.ret_pct(hist["Close"], 63)
        if r3 is not None:
            us3[t] = r3
    return to_rs_rank(us3)


def precompute_rs(data):
    """US 전용 — KR 벤치마크 차감 불필요(harness.compute_rs_at_checkpoint가
    US는 원 raw score 그대로 씀, docs 검증된 가정)."""
    t0 = time.time()
    tickers = list(data.keys())
    extra_offsets = sorted(set(OFFSETS) | {o + RS_DELTA_LOOKBACK for o in OFFSETS})
    rs_cache, r3_cache = {}, {}
    for oi, off in enumerate(extra_offsets):
        trunc_cache = {}
        for t in tickers:
            df = data[t]
            if len(df) - off < RS_MIN_BARS:
                continue
            trunc_cache[t] = harness.truncate_at(df, off)
        rs_ranks, rs_moms = harness.compute_rs_at_checkpoint(trunc_cache, 0.0, 0.0)
        rs_cache[off] = (rs_ranks, rs_moms)
        r3_cache[off] = rs3_ranks_us(trunc_cache)
        if (oi + 1) % 10 == 0 or oi == len(extra_offsets) - 1:
            print(f"[rs-precompute] {oi+1}/{len(extra_offsets)} offset={off} elapsed={time.time()-t0:.0f}s", flush=True)
    return rs_cache, r3_cache


def collect_hits(data, rs_cache, r3_cache):
    """오프셋별로 US 눌림목 히트를 모으고, 각 히트에 covariate 3종(rs/
    pivot_ratio/ma20_dist_pct)과 (outcome, r) 페어를 같이 저장. offset도
    같이 저장해 시기 반분에 쓴다."""
    t0 = time.time()
    rows = []
    for oi, off in enumerate(OFFSETS):
        rs_ranks, rs_moms = rs_cache[off]
        r3_ranks = r3_cache[off]
        rs_20ago, _ = rs_cache.get(off + RS_DELTA_LOOKBACK, ({}, {}))
        for t, df in data.items():
            if len(df) - off < CONFIG["min_bars"]:
                continue
            hist = harness.truncate_at(df, off)
            rs = rs_ranks.get(t)
            if rs is None or rs < 80:
                continue  # 게이트 자체가 RS>=80 — 사전에 걸러 계산량 절약
            rm = rs_moms.get(t)
            rs3m = r3_ranks.get(t)
            rs_delta = (rs - rs_20ago.get(t)) if t in rs_20ago else None
            try:
                hit = analyze(hist, rs_rank=rs, rs_mom=rm, cfg=CONFIG, is_kr=False,
                              rs_3m=rs3m, rs_delta=rs_delta)
            except Exception:
                continue
            if hit is None or not harness.passes_liquidity_filter(hit, is_kr=False):
                continue
            close = hit.get("close")
            pivot = hit.get("pivot")
            pivot_ratio = (close / pivot) if (close and pivot and pivot > 0) else None
            ma20 = float(hist["Close"].tail(MA_WINDOW).mean()) if len(hist) >= MA_WINDOW else None
            ma20_dist_pct = ((close - ma20) / ma20 * 100) if (close and ma20 and ma20 > 0) else None
            future = harness.future_after(df, off)
            outcome = harness.race(hit["close"], hit["stop"], future)
            rows.append({
                "offset": off, "rs": hit.get("rs"),
                "pivot_ratio": pivot_ratio, "ma20_dist_pct": ma20_dist_pct,
                "outcome": outcome,
            })
        if (oi + 1) % 10 == 0 or oi == len(OFFSETS) - 1:
            print(f"[collect] {oi+1}/{len(OFFSETS)} off={off} n_rows={len(rows)} elapsed={time.time()-t0:.0f}s", flush=True)
    return rows


def bucket_by_rs(rows):
    buckets = {f"{lo}-{hi-1 if hi <= 100 else '100'}": [] for lo, hi in RS_BUCKETS}
    labels = list(buckets.keys())
    for r in rows:
        rs = r["rs"]
        if rs is None:
            continue
        for (lo, hi), label in zip(RS_BUCKETS, labels):
            if lo <= rs < hi:
                buckets[label].append(r["outcome"])
                break
    return buckets


def bucket_by_tercile(rows, field):
    vals = pd.Series([r[field] for r in rows if r[field] is not None])
    if len(vals) < 30:
        return None, None
    q1, q2 = vals.quantile([1 / 3, 2 / 3])
    buckets = {"하위1/3": [], "중위1/3": [], "상위1/3": []}
    for r in rows:
        v = r[field]
        if v is None:
            continue
        if v <= q1:
            buckets["하위1/3"].append(r["outcome"])
        elif v <= q2:
            buckets["중위1/3"].append(r["outcome"])
        else:
            buckets["상위1/3"].append(r["outcome"])
    return buckets, (float(q1), float(q2))


def report_buckets(name, buckets):
    print(f"\n  [{name}]")
    evs = {}
    for label, outcomes in buckets.items():
        ev = harness.ev_summary(outcomes)
        evs[label] = ev
        if ev["ev_R"] is not None:
            print(f"    {label:10s} n={ev['n_hits']:5d} (nv={ev['nv']:5d}) EV={ev['ev_R']:+.3f}R "
                  f"손절률={ev['stop_rate']:.1%} 도달률={ev['target_rate']:.1%}")
        else:
            print(f"    {label:10s} n={ev['n_hits']:5d} EV=N/A(표본부족)")
    return evs


def best_worst_gap(evs):
    valid = {k: v for k, v in evs.items() if v["ev_R"] is not None and v["nv"]}
    if len(valid) < 2:
        return None, None, None, None
    best_label = max(valid, key=lambda k: valid[k]["ev_R"])
    worst_label = min(valid, key=lambda k: valid[k]["ev_R"])
    if best_label == worst_label:
        return None, None, None, None
    gap = valid[best_label]["ev_R"] - valid[worst_label]["ev_R"]
    z, sig = harness.ev_gap_zscore(valid[worst_label], valid[best_label])
    return best_label, worst_label, gap, (z, sig)


def evaluate_candidate(name, rows_full, bucket_fn):
    print("\n" + "=" * 70)
    print(f"후보: {name} — 전체({len(OFFSETS)}개 체크포인트)")
    print("=" * 70)
    buckets_full = bucket_fn(rows_full)
    if isinstance(buckets_full, tuple):
        buckets_full, cutpoints = buckets_full
        if buckets_full is None:
            print("  표본 부족 — 판정불가")
            return {"adopted": False, "reason": "표본부족"}
        print(f"  3분위 경계값: {cutpoints}")
    evs_full = report_buckets(f"{name} 전체", buckets_full)
    best, worst, gap, zsig = best_worst_gap(evs_full)
    if gap is None:
        print("  버킷 2개 미만 유효 — 판정불가")
        return {"adopted": False, "reason": "버킷부족"}
    z, sig = zsig
    print(f"  베스트({best}) - 워스트({worst}) = {gap:+.3f}R  z={z}  {'유의' if sig else '비유의'}")

    half = len(OFFSETS) // 2
    offsets_early = set(OFFSETS[:half])     # 최근 시점(작은 offset)
    offsets_late = set(OFFSETS[half:])      # 과거 시점(큰 offset)
    rows_early = [r for r in rows_full if r["offset"] in offsets_early]
    rows_late = [r for r in rows_full if r["offset"] in offsets_late]

    def half_check(rows_half, half_name):
        b = bucket_fn(rows_half)
        if isinstance(b, tuple):
            b = b[0]
        if b is None or best not in b or worst not in b:
            print(f"  [{half_name}] 표본 부족 — 판정불가")
            return None
        ev_b, ev_w = harness.ev_summary(b[best]), harness.ev_summary(b[worst])
        if ev_b["ev_R"] is None or ev_w["ev_R"] is None:
            print(f"  [{half_name}] 유효표본 부족 — 판정불가")
            return None
        gap_h = ev_b["ev_R"] - ev_w["ev_R"]
        z_h, sig_h = harness.ev_gap_zscore(ev_w, ev_b)
        same_dir = (gap_h > 0) == (gap > 0)
        print(f"  [{half_name}] {best}={ev_b['ev_R']:+.3f}R(n={ev_b['nv']}) "
              f"{worst}={ev_w['ev_R']:+.3f}R(n={ev_w['nv']})  격차={gap_h:+.3f}R  z={z_h}  "
              f"{'재현' if (sig_h and same_dir) else '미재현'}")
        return sig_h and same_dir

    rep_early = half_check(rows_early, "초반(최근시점)")
    rep_late = half_check(rows_late, "후반(과거시점)")
    reproduced = bool(rep_early) and bool(rep_late)

    adopted = (gap >= 0.15) and sig and reproduced
    print(f"  => 사전판정(격차>=0.15R & z>=1.96 & 양쪽반분재현): {'채택' if adopted else '미채택'}")
    return {
        "adopted": adopted, "best": best, "worst": worst, "gap": gap, "z": z,
        "sig": sig, "reproduced_early": rep_early, "reproduced_late": rep_late,
    }


if __name__ == "__main__":
    _t0 = time.time()
    print("=" * 70)
    print(f"US 눌림목 즉시진입 — 2차 정렬 후보 측정. US 전용 5년 fetch, checkpoints={len(OFFSETS)}개")
    print("=" * 70)
    data, kr_u, us_u = harness.fetch_universe_data(
        markets=("us",), us_period=US_FETCH_PERIOD, validate_offsets=OFFSETS)

    print("\n" + "=" * 70)
    print("RS 사전계산")
    print("=" * 70)
    rs_cache, r3_cache = precompute_rs(data)

    print("\n" + "=" * 70)
    print("US 눌림목 히트 수집(RS>=80 게이트, covariate 3종 부착)")
    print("=" * 70)
    rows = collect_hits(data, rs_cache, r3_cache)
    print(f"\n[collect] 전체 히트 수집 완료: n={len(rows)}")

    results = {}
    results["rs"] = evaluate_candidate("RS(80-84/85-89/90-94/95+)", rows, bucket_by_rs)
    results["pivot_ratio"] = evaluate_candidate(
        "피벗 대비 거리(entry/pivot 3분위)", rows,
        lambda rs_: bucket_by_tercile(rs_, "pivot_ratio"))
    results["ma20_dist_pct"] = evaluate_candidate(
        "20일선 이격(3분위)", rows,
        lambda rs_: bucket_by_tercile(rs_, "ma20_dist_pct"))

    print("\n" + "=" * 70)
    print("최종 판정")
    print("=" * 70)
    adopted_any = [k for k, v in results.items() if v.get("adopted")]
    if adopted_any:
        print(f"  채택된 2차 정렬 후보: {adopted_any}")
    else:
        print("  3개 후보 전부 미채택 — 손절폭 단일 정렬 유지")

    print(f"\n[main] 전체 완료, elapsed={time.time()-_t0:.0f}s", flush=True)

    import json
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "2026-09-08_us_pullback_immediate_2nd_sort_candidates.results.json")
    with open(out_path, "w") as f:
        json.dump({"n_hits_total": len(rows), "results": results}, f, default=str, indent=2, ensure_ascii=False)
    print(f"[main] 결과 JSON: {out_path}")
