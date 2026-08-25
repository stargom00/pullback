"""
Stage2 유동성매칭 대조군 — 5시드 견고성 확인 (2026-08-25, 8/17 캠페인
마무리 ①).

배경: `2026-08-25_stage2_liquidity_matched_control.py`(seed=42 1회)가
상승우위 +4.9pp/하락차이 +0.7pp를 냈다. 표본 1회 추출로는 이게 우연한
시드값의 산물인지 안정적인 결과인지 알 수 없다 — 이 스크립트는 seed
5개로 반복해 분포(평균/표준편차/범위)를 확인한다.

효율화: 유동성컷→RS백분위→Stage2템플릿(=`actual` 쪽)은 시드와 무관하게
결정적(deterministic)이라 체크포인트당 1번만 계산해서 캐싱하고, 시드마다
다시 하는 건 "그 체크포인트의 유동성 생존자 풀에서 무작위 추출"뿐이다 —
데이터 재fetch·파이프라인 재계산 없이 5배 반복.

**scanner.py는 전혀 수정하지 않는다** — 이전 두 Stage2 스크립트와 동일한
import만 사용.

실행: 리포 루트에서
`python3 scripts/measurements/2026-08-25_stage2_liquidity_matched_control_multiseed.py`
(KR 유니버스만 fetch, 5분 내외, 네트워크 필요 — 파이프라인 자체는
5초 미만이라 시드 5개를 더 돌려도 전체 소요시간에 거의 안 얹힘)
"""
import sys
import os
import json
import random
import time
import statistics

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import harness
from scanner import analyze_stage2, STAGE2_CONFIG, rs_score_stage2, to_rs_rank

STAGE2_LIQUIDITY_MIN_EOK = 20   # app.py 3284행과 동일 (리터럴 사본)
STAGE2_RS_PCTILE_MIN = 70       # app.py 3285행과 동일

OFFSETS = harness.checkpoints(60, 250, 10)
MIN_BARS_FLOOR = STAGE2_CONFIG["min_bars"]
UP_PCT = 0.15
DOWN_PCT = 0.15
MAX_BARS = 60
SEEDS = [42, 1, 7, 123, 2026]   # 42는 기존 측정과 동일(재현 확인용) + 4개 추가


def touch_rates(entry, future_df, max_bars=MAX_BARS, up_pct=UP_PCT, down_pct=DOWN_PCT):
    """이전 두 Stage2 스크립트와 동일 정의 — 복사."""
    if entry is None or entry <= 0 or future_df is None:
        return None
    avail = min(max_bars, len(future_df))
    if avail == 0:
        return None
    up_target = entry * (1 + up_pct)
    down_target = entry * (1 - down_pct)
    touched_up = touched_down = False
    for i in range(avail):
        hi = float(future_df["High"].iloc[i])
        lo = float(future_df["Low"].iloc[i])
        if hi >= up_target:
            touched_up = True
        if lo <= down_target:
            touched_down = True
    insufficient = avail < max_bars
    return {
        "touched_up": touched_up, "touched_down": touched_down,
        "up_valid": touched_up or not insufficient,
        "down_valid": touched_down or not insufficient,
    }


def rate_summary(records):
    up_valid = [r for r in records if r["up_valid"]]
    down_valid = [r for r in records if r["down_valid"]]
    up_rate = sum(1 for r in up_valid if r["touched_up"]) / len(up_valid) if up_valid else None
    down_rate = sum(1 for r in down_valid if r["touched_down"]) / len(down_valid) if down_valid else None
    return {"n": len(records), "up_touch_rate": up_rate, "down_touch_rate": down_rate}


def run(data, out_path=None):
    tickers_kr = [t for t in data if harness.is_kr_ticker(t)]

    # ── 1패스: 체크포인트별로 결정적인 부분(유동성/RS/템플릿/실제 터치기록)을
    # 계산해서 캐싱. 대조군 풀(liquid pool)도 같이 저장해서 시드 루프에서 재사용. ──
    checkpoint_cache = []  # [{off, hit_tickers, liquid_pool: {t: hist}, actual_records}]
    t0 = time.time()
    for oi, off in enumerate(OFFSETS):
        trunc_cache = {}
        for t in tickers_kr:
            df = data[t]
            if len(df) - off < MIN_BARS_FLOOR:
                continue
            trunc_cache[t] = harness.truncate_at(df, off)
        if not trunc_cache:
            checkpoint_cache.append({"off": off, "hit_tickers": [], "liquid_pool": {}, "actual_records": []})
            continue

        liquid = {}
        for t, hist in trunc_cache.items():
            c, v = hist.get("Close"), hist.get("Volume")
            if c is None or v is None or len(c) < 20 or len(v) < 20:
                continue
            try:
                avg_value = float((c.iloc[-20:] * v.iloc[-20:]).mean())
            except Exception:
                continue
            if avg_value >= STAGE2_LIQUIDITY_MIN_EOK * 1e8:
                liquid[t] = hist

        raw_scores = {}
        for t, hist in liquid.items():
            s = rs_score_stage2(hist["Close"])
            if s is not None:
                raw_scores[t] = s
        pctiles = to_rs_rank(raw_scores)
        rs_survivors = {t: liquid[t] for t in liquid if pctiles.get(t, 0) >= STAGE2_RS_PCTILE_MIN}

        hit_tickers = []
        actual_records = []
        for t, hist in rs_survivors.items():
            try:
                r = analyze_stage2(hist, rs_pctile=pctiles.get(t))
            except Exception:
                continue
            if r is None:
                continue
            hit_tickers.append(t)
            close = float(hist["Close"].iloc[-1])
            future = harness.future_after(data[t], off)
            tr = touch_rates(close, future)
            if tr is not None:
                actual_records.append(tr)

        checkpoint_cache.append({
            "off": off, "hit_tickers": hit_tickers, "liquid_pool": liquid,
            "actual_records": actual_records,
        })
        print(f"[PASS1] off={off} hits={len(hit_tickers)} liquid={len(liquid)} "
              f"elapsed={time.time()-t0:.0f}s ({oi+1}/{len(OFFSETS)})", flush=True)

    all_actual_records = [r for cp in checkpoint_cache for r in cp["actual_records"]]
    actual = rate_summary(all_actual_records)
    daily_avg = round(sum(len(cp["hit_tickers"]) for cp in checkpoint_cache) / len(OFFSETS), 1)

    # ── 2패스: 시드별로 대조군만 다시 추출 ──
    per_seed = []
    for seed in SEEDS:
        rng = random.Random(seed)
        control_records = []
        for cp in checkpoint_cache:
            pool = list(cp["liquid_pool"].keys())
            sample_n = min(len(cp["hit_tickers"]), len(pool))
            if sample_n == 0:
                continue
            for t in rng.sample(pool, sample_n):
                hist = cp["liquid_pool"][t]
                close = float(hist["Close"].iloc[-1])
                future = harness.future_after(data[t], cp["off"])
                tr = touch_rates(close, future)
                if tr is not None:
                    control_records.append(tr)
        control = rate_summary(control_records)
        up_edge = (actual["up_touch_rate"] - control["up_touch_rate"]) * 100 \
            if actual["up_touch_rate"] is not None and control["up_touch_rate"] is not None else None
        down_diff = (actual["down_touch_rate"] - control["down_touch_rate"]) * 100 \
            if actual["down_touch_rate"] is not None and control["down_touch_rate"] is not None else None
        per_seed.append({
            "seed": seed, "control_n": control["n"],
            "control_up_touch_rate": round(control["up_touch_rate"], 4) if control["up_touch_rate"] is not None else None,
            "control_down_touch_rate": round(control["down_touch_rate"], 4) if control["down_touch_rate"] is not None else None,
            "up_edge_pp": round(up_edge, 2) if up_edge is not None else None,
            "down_diff_pp": round(down_diff, 2) if down_diff is not None else None,
        })
        print(f"[SEED {seed}] up_edge={per_seed[-1]['up_edge_pp']}pp "
              f"down_diff={per_seed[-1]['down_diff_pp']}pp (control_n={control['n']})", flush=True)

    up_edges = [s["up_edge_pp"] for s in per_seed if s["up_edge_pp"] is not None]
    down_diffs = [s["down_diff_pp"] for s in per_seed if s["down_diff_pp"] is not None]
    robust_positive_up = all(v > 0 for v in up_edges) if up_edges else False

    report = {
        "daily_avg_hits": daily_avg,
        "actual": {"n": actual["n"],
                   "up_touch_rate": round(actual["up_touch_rate"], 4) if actual["up_touch_rate"] is not None else None,
                   "down_touch_rate": round(actual["down_touch_rate"], 4) if actual["down_touch_rate"] is not None else None},
        "per_seed": per_seed,
        "up_edge_pp_stats": {
            "mean": round(statistics.mean(up_edges), 2) if up_edges else None,
            "std": round(statistics.pstdev(up_edges), 2) if len(up_edges) > 1 else 0.0,
            "min": round(min(up_edges), 2) if up_edges else None,
            "max": round(max(up_edges), 2) if up_edges else None,
            "all_positive": robust_positive_up,
        },
        "down_diff_pp_stats": {
            "mean": round(statistics.mean(down_diffs), 2) if down_diffs else None,
            "std": round(statistics.pstdev(down_diffs), 2) if len(down_diffs) > 1 else 0.0,
            "min": round(min(down_diffs), 2) if down_diffs else None,
            "max": round(max(down_diffs), 2) if down_diffs else None,
        },
    }
    print(f"[MULTISEED SUMMARY] up_edge: mean={report['up_edge_pp_stats']['mean']}pp "
          f"std={report['up_edge_pp_stats']['std']} range=[{report['up_edge_pp_stats']['min']},"
          f"{report['up_edge_pp_stats']['max']}] all_positive={robust_positive_up} | "
          f"down_diff: mean={report['down_diff_pp_stats']['mean']}pp "
          f"std={report['down_diff_pp_stats']['std']} "
          f"range=[{report['down_diff_pp_stats']['min']},{report['down_diff_pp_stats']['max']}]",
          flush=True)

    if out_path:
        with open(out_path, "w") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"SAVED report to {out_path}", flush=True)
    return report


if __name__ == "__main__":
    data, kr_u, us_u = harness.fetch_universe_data(markets=("kr",))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "2026-08-25_stage2_liquidity_matched_control_multiseed.results.json")
    run(data, out_path=out)
