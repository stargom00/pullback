"""
Stage2 대조군 정정 — 유동성 매칭 대조군으로 재측정 (2026-08-25, 8/17
캠페인 ④-후속).

배경: 직전 스크립트(`2026-08-25_stage2_liquidity_filter_touch_rate.py`)의
상승우위가 +14.4pp로 Script E 원본(+2.3pp)의 6배 이상 나와, 원인 후보로
"대조군 표집 차이"를 지목했다 — 그 스크립트는 대조군을 KR 전체(유동성
필터조차 미적용)에서 완전 무작위로 뽑았는데, Script E는 유동성만 통과한
종목 중에서 뽑았을 가능성이 있다는 가설. 이 스크립트가 그 가설을 직접
검증한다: 대조군 표집 소스만 "KR 전체"에서 "유동성 컷 통과 종목"으로
바꾸고, 나머지 방법론(60봉 ±15% 독립 터치율, off=60~250 10간격)은 완전히
동일하게 유지 — 유일한 변수가 대조군 소스이므로, 결과가 원본(+2.3pp)에
가까워지면 가설 확인, 그대로면 다른 원인.

**scanner.py는 전혀 수정하지 않는다**(요청 사항) — 직전 스크립트와 동일하게
analyze_stage2()/STAGE2_CONFIG/rs_score_stage2를 그대로 import.

실행: 리포 루트에서
`python3 scripts/measurements/2026-08-25_stage2_liquidity_matched_control.py`
(KR 유니버스만 fetch, 5분 내외, 네트워크 필요)
"""
import sys
import os
import json
import random
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import harness
from scanner import analyze_stage2, STAGE2_CONFIG, rs_score_stage2, to_rs_rank

STAGE2_LIQUIDITY_MIN_EOK = 20   # app.py 3284행과 동일 (리터럴 사본 — app.py 참고)
STAGE2_RS_PCTILE_MIN = 70       # app.py 3285행과 동일

OFFSETS = harness.checkpoints(60, 250, 10)
MIN_BARS_FLOOR = STAGE2_CONFIG["min_bars"]
UP_PCT = 0.15
DOWN_PCT = 0.15
MAX_BARS = 60
RANDOM_SEED = 42


def touch_rates(entry, future_df, max_bars=MAX_BARS, up_pct=UP_PCT, down_pct=DOWN_PCT):
    """직전 스크립트와 동일 정의(2026-08-25_stage2_liquidity_filter_touch_rate.py
    참고) — 여기서는 대조군 소스만 바꾸는 게 목적이라 이 함수는 그대로 복사."""
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
    return {
        "n": len(records),
        "up_touch_rate": round(up_rate, 4) if up_rate is not None else None,
        "down_touch_rate": round(down_rate, 4) if down_rate is not None else None,
    }


def run(data, out_path=None):
    rng = random.Random(RANDOM_SEED)
    tickers_kr = [t for t in data if harness.is_kr_ticker(t)]

    actual_records = []
    control_records = []
    daily_hits = []
    t0 = time.time()

    for oi, off in enumerate(OFFSETS):
        trunc_cache = {}
        for t in tickers_kr:
            df = data[t]
            if len(df) - off < MIN_BARS_FLOOR:
                continue
            trunc_cache[t] = harness.truncate_at(df, off)
        if not trunc_cache:
            daily_hits.append(0)
            continue

        # 1) 유동성컷
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

        # 2) RS 백분위
        raw_scores = {}
        for t, hist in liquid.items():
            s = rs_score_stage2(hist["Close"])
            if s is not None:
                raw_scores[t] = s
        pctiles = to_rs_rank(raw_scores)
        rs_survivors = {t: liquid[t] for t in liquid if pctiles.get(t, 0) >= STAGE2_RS_PCTILE_MIN}

        # 3) Stage2 템플릿
        hit_tickers = []
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

        daily_hits.append(len(hit_tickers))

        # ── ④-후속 핵심 변경: 대조군을 "KR 전체"가 아니라 "유동성 컷
        # 통과 종목(liquid)"에서만 뽑음 — RS/템플릿은 여전히 미적용,
        # 유동성만 매칭. 나머지(페어 표본수, seed)는 직전 스크립트와 동일. ──
        pool = list(liquid.keys())
        sample_n = min(len(hit_tickers), len(pool))
        for t in (rng.sample(pool, sample_n) if sample_n else []):
            hist = liquid[t]
            close = float(hist["Close"].iloc[-1])
            future = harness.future_after(data[t], off)
            tr = touch_rates(close, future)
            if tr is not None:
                control_records.append(tr)

        print(f"[STAGE2-CTRL] off={off} hits={len(hit_tickers)} liquid={len(liquid)} "
              f"rs_survivors={len(rs_survivors)} control_n={sample_n} "
              f"elapsed={time.time()-t0:.0f}s ({oi+1}/{len(OFFSETS)})", flush=True)

    actual = rate_summary(actual_records)
    control = rate_summary(control_records)
    daily_avg = round(sum(daily_hits) / len(daily_hits), 1) if daily_hits else None

    report = {
        "daily_avg_hits": daily_avg,
        "actual": actual,
        "control_liquidity_matched": control,
        "up_edge_pp": round((actual["up_touch_rate"] - control["up_touch_rate"]) * 100, 1)
            if actual["up_touch_rate"] is not None and control["up_touch_rate"] is not None else None,
        "down_diff_pp": round((actual["down_touch_rate"] - control["down_touch_rate"]) * 100, 1)
            if actual["down_touch_rate"] is not None and control["down_touch_rate"] is not None else None,
        "reference": {
            "script_e_2026_08_08": {"up_touch_rate": 0.751, "control_up_touch_rate": 0.728, "up_edge_pp": 2.3,
                                     "down_touch_rate": 0.551, "control_down_touch_rate": 0.509, "down_diff_pp": 4.2},
            "prev_random_control_2026_08_25": {"up_touch_rate": 0.7941, "control_up_touch_rate": 0.6503,
                                                "up_edge_pp": 14.4, "down_touch_rate": 0.549,
                                                "control_down_touch_rate": 0.5, "down_diff_pp": 4.9},
        },
    }
    print(f"[STAGE2-CTRL SUMMARY] 일평균히트={daily_avg}, 실제 상승터치={actual['up_touch_rate']}"
          f"(n={actual['n']}), 유동성매칭대조군 상승터치={control['up_touch_rate']}(n={control['n']}), "
          f"상승우위={report['up_edge_pp']}pp | 실제 하락터치={actual['down_touch_rate']}, "
          f"대조군 하락터치={control['down_touch_rate']}, 하락차이={report['down_diff_pp']}pp",
          flush=True)

    if out_path:
        with open(out_path, "w") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"SAVED report to {out_path}", flush=True)
    return report


if __name__ == "__main__":
    data, kr_u, us_u = harness.fetch_universe_data(markets=("kr",))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "2026-08-25_stage2_liquidity_matched_control.results.json")
    run(data, out_path=out)
