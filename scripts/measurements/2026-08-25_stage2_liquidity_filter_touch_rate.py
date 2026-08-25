"""
Stage2 유동성 필터 적용 후 재측정 (2026-08-25, 8/17 세션 미완 항목 ④).

배경: `all_tabs_common_yardstick_investigation.md`의 Script B(2026-08-08,
유동성 필터 없음, 스크립트 원본 소실)는 Stage2 일평균 74.9건, 대조군 대비
+15%터치율 우위 +3.4pp(64.2% vs 60.8%)로 기록했다 — "과대추정 주의"라는
자체 경고가 달려 있었다. 이어진 Script E(같은 날, 마찬가지로 소실)는
`_run_scan_stage2`의 실제 파이프라인(유동성컷+RS재백분위)을 적용해
16.7건/일로 급감, 우위는 +2.3pp로 더 약해지고 하락위험은 대조군보다
4.2pp 더 나빠 "검증실패" 판정을 내렸다 — 지금 앱의 Stage2 "검증실패"
배지가 이 결론에 근거한다.

문제: Script B/E 둘 다 원본이 저장소에 없어(scripts/measurements/README.md
감사표, v5.68 이전 관례) 그 "검증실패" 결론 자체가 재현 불가 상태였다.
이 스크립트는 harness 기반의 재현 가능한 방법론으로 Script E를 다시
수행해 결론이 재현되는지 확인한다. **scanner.py는 전혀 수정하지 않는다**
(요청 사항) — analyze_stage2()/STAGE2_CONFIG/rs_score_stage2를 그대로
import해서 쓰기만 한다.

방법론 (Script B/D/F와 동일 — "60봉 내 목표% 터치율 + 시점매칭 대조군"):
  - 진입 = 신호일(체크포인트) 종가, 무조건(2R 레이스처럼 확인 대기 없음).
  - 60봉 이내 +15%/-15% 각각 독립적으로 터치했는지(동시 터치 가능 —
    "먼저 온 쪽에서 멈추는" 2R 레이스와 다름, Script B/D/F 원문 "최대상승/
    하락 분포 + N봉내 ±15% 터치율" 표현 그대로).
  - 대조군: 같은 체크포인트·같은 시장(KR, Stage2 자체가 한국전용)에서
    무작위 추출 — Stage2 필터(유동성/RS/템플릿) 전혀 안 거친 종목들.
    표본수는 그 체크포인트 히트 수와 동일(페어 비교), seed 고정(42)으로
    재현 가능.
  - 체크포인트: off=60..250, 10간격, 20지점 — all_tabs 문서 표준 방법론
    그대로(harness.checkpoints 기본값). Script B/E 원문에 체크포인트
    스펙이 안 남아있어(스크립트 소실) 표준값을 채택 — 다르게 쟀을 가능성은
    있으나 이 프로젝트 전체가 이 스펙을 기본으로 쓰고 있어 비교 가능성을
    우선했다.

app.py `_run_scan_stage2`(3288행~) 파이프라인을 코드 대조로 그대로 재현:
  1) 유동성컷 — KR 20일 평균거래대금 >= STAGE2_LIQUIDITY_MIN_EOK(20억원)
  2) RS 백분위 — rs_score_stage2()로 유동성 생존자 안에서만 산출 후
     to_rs_rank() 백분위, STAGE2_RS_PCTILE_MIN(70) 이상만
  3) analyze_stage2() — 템플릿+거래량수축+MA수렴+티어링

실행: 리포 루트에서
`python3 scripts/measurements/2026-08-25_stage2_liquidity_filter_touch_rate.py`
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

# app.py 3284~3285행 그대로 (import는 안 함 — app.py는 FastAPI 앱 기동
# 부작용이 커서 상수만 리터럴로 복사. 값이 바뀌면 이 스크립트도 갱신 필요
# — CLAUDE.md "CONFIG 값 cfg[...] 참조" 원칙과 같은 이유로 여기 남김).
STAGE2_LIQUIDITY_MIN_EOK = 20
STAGE2_RS_PCTILE_MIN = 70

OFFSETS = harness.checkpoints(60, 250, 10)
MIN_BARS_FLOOR = STAGE2_CONFIG["min_bars"]  # 262
UP_PCT = 0.15
DOWN_PCT = 0.15
MAX_BARS = 60
RANDOM_SEED = 42


def touch_rates(entry, future_df, max_bars=MAX_BARS, up_pct=UP_PCT, down_pct=DOWN_PCT):
    """진입가 대비 max_bars봉 내 +up_pct/-down_pct 각각 독립 터치 여부.
    2R 레이스(harness.race)처럼 먼저 온 쪽에서 안 멈춤 — Script B/D/F의
    '최대상승/하락 분포 + N봉내 ±X% 터치율'과 동일 정의. 우측 절단(가용
    봉수가 max_bars 미만인데 터치도 안 함)은 그 방향만 판정 보류
    (harness.race의 insufficient와 같은 취급)."""
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

        # ── app.py _run_scan_stage2 파이프라인 그대로 재현 ──
        # 1) 유동성컷 (20일 평균거래대금 >= 20억원)
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

        # 2) RS 백분위 (유동성 생존자 안에서만 산출)
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

        # ── 대조군: 같은 체크포인트 KR 전체(필터 미적용)에서 히트 수만큼 무작위 ──
        pool = list(trunc_cache.keys())
        sample_n = min(len(hit_tickers), len(pool))
        for t in rng.sample(pool, sample_n) if sample_n else []:
            hist = trunc_cache[t]
            close = float(hist["Close"].iloc[-1])
            future = harness.future_after(data[t], off)
            tr = touch_rates(close, future)
            if tr is not None:
                control_records.append(tr)

        print(f"[STAGE2] off={off} hits={len(hit_tickers)} liquid={len(liquid)} "
              f"rs_survivors={len(rs_survivors)} elapsed={time.time()-t0:.0f}s "
              f"({oi+1}/{len(OFFSETS)})", flush=True)

    actual = rate_summary(actual_records)
    control = rate_summary(control_records)
    daily_avg = round(sum(daily_hits) / len(daily_hits), 1) if daily_hits else None

    report = {
        "daily_avg_hits": daily_avg,
        "actual": actual,
        "control": control,
        "up_edge_pp": round((actual["up_touch_rate"] - control["up_touch_rate"]) * 100, 1)
            if actual["up_touch_rate"] is not None and control["up_touch_rate"] is not None else None,
        "down_diff_pp": round((actual["down_touch_rate"] - control["down_touch_rate"]) * 100, 1)
            if actual["down_touch_rate"] is not None and control["down_touch_rate"] is not None else None,
        "reference_script_b_no_liquidity_filter": {
            "daily_avg_hits": 74.9, "up_touch_rate": 0.642, "control_up_touch_rate": 0.608,
        },
        "reference_script_e_2026_08_08": {
            "daily_avg_hits": 16.7, "up_touch_rate": 0.751, "control_up_touch_rate": 0.728,
            "down_touch_rate": 0.551, "control_down_touch_rate": 0.509,
        },
    }
    print(f"[STAGE2 SUMMARY] 일평균히트={daily_avg}, 실제 상승터치={actual['up_touch_rate']}"
          f"(n={actual['n']}), 대조군 상승터치={control['up_touch_rate']}(n={control['n']}), "
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
                        "2026-08-25_stage2_liquidity_filter_touch_rate.results.json")
    run(data, out_path=out)
