"""
US 눌림목 즉시진입 — ATR% 구간별 EV (2026-09-08, 사용자 지시 — 실행).
사전 등록: docs/kr_us_strategy_map.md "사전 등록 — US 눌림목 ATR% 구간별
EV" 절. **그 절의 정의·구간·판정식을 그대로 따르고, 등록 후 어떤 조건도
바꾸지 않는다.**

【정의】(등록 그대로)
  ATR% = ATR14 / 확인일 종가 × 100 — scanner.py `badge_fields()`의
  `atr_pct` 필드와 동일 정의(신규 계산식 없음, hit["atr_pct"] 그대로 사용).
【구간】(등록 그대로) <2% / 2~4% / 4~6% / 6%+.
【가설】(등록 그대로) 방향 미지정(양측 검정).
【판정】(등록 그대로) 4구간 중 최고 EV 구간 − 최저 EV 구간 ≥ +0.15R,
  z≥1.96(harness.ev_gap_zscore), 시기 반분(초반/후반) 둘 다 재현,
  각 구간 n≥100.

대상: US 눌림목(analyze(), CONFIG, is_kr=False)의 RS≥cfg["rs_min"] 게이트를
통과한 전체 히트 — `_cache["us:pullback"]`이 실제로 담는 것과 동일 정의.

【이번 실행 지시사항 반영】
  - 90개 체크포인트(checkpoints(60,950,10)), harness.py 재사용, US 5년 fetch.
  - 룩어헤드 assert — truncate 일치 검증 + future가 hist보다 반드시 미래인지
    매 히트마다 assert.
  - 히트별 원자료(ticker/signal_date/atr_pct/구간/entry/stop/outcome/r)를
    JSON에 저장.
  - 결과 표: 구간 × (n / EV / z / 시기반분 초·후 / stop_rate / 손절폭
    중앙값 %·ATR배수). z는 구간 자체의 EV가 0과 유의하게 다른지
    (harness.one_sample_zscore) — 등록된 판정식의 "최고-최저 격차 z"는
    별도로 "판정" 절에서 계산한다(서로 다른 검정, 혼동 방지).

실행: 리포 루트에서
  python3 scripts/measurements/2026-09-08_us_pullback_atr_pct_bucket_ev.py
(US 5년치 fetch + 90 체크포인트 — 장시간 예상)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import json
import time

import harness
import scanner
from scanner import CONFIG, analyze

OFFSETS = harness.checkpoints(60, 950, 10)   # 90개 — README 규칙9 표준
US_FETCH_PERIOD = "5y"
EV_GAP_THRESHOLD = 0.15
MIN_N_FOR_JUDGMENT = 100

BUCKET_EDGES = [
    ("<2%", 0.0, 2.0),
    ("2~4%", 2.0, 4.0),
    ("4~6%", 4.0, 6.0),
    ("6%+", 6.0, float("inf")),
]


def bucket_of(atr_pct):
    for label, lo, hi in BUCKET_EDGES:
        if lo <= atr_pct < hi:
            return label
    return None


def precompute_rs(data):
    """US 전용 — KR 벤치마크 차감 불필요(harness.compute_rs_at_checkpoint가
    US는 원 raw score 그대로 씀, 검증된 가정 — 2026-09-08 2nd-sort-candidates
    스크립트와 동일 재사용)."""
    t0 = time.time()
    tickers = list(data.keys())
    rs_cache = {}
    for oi, off in enumerate(OFFSETS):
        trunc_cache = {}
        for t in tickers:
            df = data[t]
            if len(df) - off < CONFIG["min_bars"]:
                continue
            trunc_cache[t] = harness.truncate_at(df, off)
        rs_ranks, rs_moms = harness.compute_rs_at_checkpoint(trunc_cache, 0.0, 0.0)
        rs_cache[off] = rs_ranks
        if (oi + 1) % 10 == 0 or oi == len(OFFSETS) - 1:
            print(f"[rs-precompute] {oi+1}/{len(OFFSETS)} offset={off} elapsed={time.time()-t0:.0f}s", flush=True)
    return rs_cache


def collect_hits(data, rs_cache):
    t0 = time.time()
    rows = []
    for oi, off in enumerate(OFFSETS):
        rs_ranks = rs_cache[off]
        for t, df in data.items():
            if len(df) - off < CONFIG["min_bars"]:
                continue
            hist = harness.truncate_at(df, off)
            assert len(hist) == len(df) - off, "truncate 불일치 — 룩어헤드 위험(hist가 미래를 포함할 수 있음)"
            rs = rs_ranks.get(t)
            if rs is None or rs < CONFIG["rs_min"]:
                continue  # 게이트 자체가 RS>=rs_min — 사전에 걸러 계산량 절약
            try:
                hit = analyze(hist, rs_rank=rs, rs_mom=None, cfg=CONFIG, is_kr=False)
            except Exception:
                continue
            if hit is None or not harness.passes_liquidity_filter(hit, is_kr=False):
                continue
            entry = hit.get("close")
            stop = hit.get("stop")
            atr_pct = hit.get("atr_pct")
            if entry is None or stop is None or atr_pct is None or entry <= stop:
                continue
            atr14 = scanner.atr(hist["High"], hist["Low"], hist["Close"])
            if atr14 is None or atr14 <= 0:
                continue
            bucket = bucket_of(atr_pct)
            if bucket is None:
                continue

            future = harness.future_after(df, off)
            assert len(future) == 0 or future.index[0] > hist.index[-1], \
                "룩어헤드: future 첫 봉이 hist 마지막 봉보다 과거임"
            outcome = harness.race(entry, stop, future)

            rows.append({
                "ticker": t, "signal_date": str(hist.index[-1].date()), "offset": off,
                "atr_pct": atr_pct, "bucket": bucket,
                "entry": round(entry, 4), "stop": round(stop, 4),
                "stop_width_pct": round((entry - stop) / entry * 100, 4),
                "stop_width_atr": round((entry - stop) / atr14, 4),
                "outcome": outcome[0], "r": outcome[1],
            })
        if (oi + 1) % 10 == 0 or oi == len(OFFSETS) - 1:
            print(f"[collect] {oi+1}/{len(OFFSETS)} off={off} n_rows={len(rows)} elapsed={time.time()-t0:.0f}s", flush=True)
    return rows


def median(vals):
    vals = sorted(vals)
    n = len(vals)
    if n == 0:
        return None
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2


def bucket_report(rows, label):
    outcomes = [(r["outcome"], r["r"]) for r in rows]
    ev = harness.ev_summary(outcomes)
    z0, sig0 = harness.one_sample_zscore(ev) if ev["nv"] else (None, False)

    half = len(OFFSETS) // 2
    offs_early = set(OFFSETS[:half])    # 최근 시점(작은 offset)
    offs_late = set(OFFSETS[half:])     # 과거 시점(큰 offset)
    rows_early = [r for r in rows if r["offset"] in offs_early]
    rows_late = [r for r in rows if r["offset"] in offs_late]
    ev_early = harness.ev_summary([(r["outcome"], r["r"]) for r in rows_early])
    ev_late = harness.ev_summary([(r["outcome"], r["r"]) for r in rows_late])

    sw_pct = median([r["stop_width_pct"] for r in rows]) if rows else None
    sw_atr = median([r["stop_width_atr"] for r in rows]) if rows else None
    return {
        "label": label, "n": len(rows), "nv": ev["nv"], "ev_R": ev["ev_R"],
        "z_vs_zero": z0,
        "ev_R_early": ev_early["ev_R"], "nv_early": ev_early["nv"],
        "ev_R_late": ev_late["ev_R"], "nv_late": ev_late["nv"],
        "stop_rate": ev["stop_rate"], "target_rate": ev["target_rate"],
        "stop_width_pct_median": sw_pct, "stop_width_atr_median": sw_atr,
    }


def fmt(v, fmt_spec):
    return format(v, fmt_spec) if v is not None else "N/A"


if __name__ == "__main__":
    _t0 = time.time()
    print("=" * 70)
    print(f"US 눌림목 즉시진입 — ATR% 구간별 EV. US 전용 5년 fetch, checkpoints={len(OFFSETS)}개")
    print("=" * 70)
    data, kr_u, us_u = harness.fetch_universe_data(
        markets=("us",), us_period=US_FETCH_PERIOD, validate_offsets=OFFSETS)

    print("\n" + "=" * 70)
    print("RS 사전계산")
    print("=" * 70)
    rs_cache = precompute_rs(data)

    print("\n" + "=" * 70)
    print(f"US 눌림목 히트 수집(RS>={CONFIG['rs_min']} 게이트)")
    print("=" * 70)
    rows = collect_hits(data, rs_cache)
    print(f"\n[collect] 전체 히트 수집 완료: n={len(rows)}")

    by_bucket = {label: [r for r in rows if r["bucket"] == label] for label, _, _ in BUCKET_EDGES}

    print("\n" + "=" * 70)
    print("구간별 결과 (전체 90개 체크포인트)")
    print("=" * 70)
    reports = {}
    for label, _, _ in BUCKET_EDGES:
        rep = bucket_report(by_bucket[label], label)
        reports[label] = rep
        if rep["ev_R"] is not None:
            print(f"  {label:6s} n={rep['n']:5d}(nv={rep['nv']:5d}) EV={rep['ev_R']:+.3f}R "
                  f"z={fmt(rep['z_vs_zero'], '+.2f')} "
                  f"초반={fmt(rep['ev_R_early'], '+.3f')}R(n={rep['nv_early']}) "
                  f"후반={fmt(rep['ev_R_late'], '+.3f')}R(n={rep['nv_late']}) "
                  f"손절률={rep['stop_rate']:.1%} "
                  f"손절폭중앙값={rep['stop_width_pct_median']:.2f}%/{rep['stop_width_atr_median']:.2f}ATR")
        else:
            print(f"  {label:6s} n={rep['n']:5d} EV=N/A(표본부족)")

    # ── 판정 — 등록된 식 그대로: 최고구간-최저구간 격차, z, 시기반분 재현 ──
    print("\n" + "=" * 70)
    print("판정 (등록: 최고구간-최저구간 >= +0.15R & z>=1.96 & 양쪽반분재현 & 각구간 n>=100)")
    print("=" * 70)
    valid = {k: v for k, v in reports.items() if v["ev_R"] is not None and v["nv"] >= MIN_N_FOR_JUDGMENT}
    if len(valid) < 2:
        print(f"  유효 구간(n>={MIN_N_FOR_JUDGMENT}) 2개 미만 — 판정불가")
        adopted = False
        best_label = worst_label = gap = z = sig = None
        rep_early = rep_late = None
    else:
        best_label = max(valid, key=lambda k: valid[k]["ev_R"])
        worst_label = min(valid, key=lambda k: valid[k]["ev_R"])
        gap = valid[best_label]["ev_R"] - valid[worst_label]["ev_R"]
        ev_best = harness.ev_summary([(r["outcome"], r["r"]) for r in by_bucket[best_label]])
        ev_worst = harness.ev_summary([(r["outcome"], r["r"]) for r in by_bucket[worst_label]])
        z, sig = harness.ev_gap_zscore(ev_worst, ev_best)
        print(f"  최고구간={best_label}({valid[best_label]['ev_R']:+.3f}R)  "
              f"최저구간={worst_label}({valid[worst_label]['ev_R']:+.3f}R)  "
              f"격차={gap:+.3f}R  z={z}  {'유의' if sig else '비유의'}")

        half = len(OFFSETS) // 2
        offs_early = set(OFFSETS[:half])
        offs_late = set(OFFSETS[half:])

        def half_check(offs, name):
            rb = [r for r in by_bucket[best_label] if r["offset"] in offs]
            rw = [r for r in by_bucket[worst_label] if r["offset"] in offs]
            ev_b = harness.ev_summary([(r["outcome"], r["r"]) for r in rb])
            ev_w = harness.ev_summary([(r["outcome"], r["r"]) for r in rw])
            if ev_b["ev_R"] is None or ev_w["ev_R"] is None:
                print(f"  [{name}] 표본 부족 — 판정불가 (n_best={ev_b['nv']}, n_worst={ev_w['nv']})")
                return None
            gap_h = ev_b["ev_R"] - ev_w["ev_R"]
            z_h, sig_h = harness.ev_gap_zscore(ev_w, ev_b)
            same_dir = (gap_h > 0) == (gap > 0)
            print(f"  [{name}] {best_label}={ev_b['ev_R']:+.3f}R(n={ev_b['nv']}) "
                  f"{worst_label}={ev_w['ev_R']:+.3f}R(n={ev_w['nv']})  격차={gap_h:+.3f}R  z={z_h}  "
                  f"{'재현' if (sig_h and same_dir) else '미재현'}")
            return sig_h and same_dir

        rep_early = half_check(offs_early, "초반(최근시점)")
        rep_late = half_check(offs_late, "후반(과거시점)")
        reproduced = bool(rep_early) and bool(rep_late)
        adopted = (gap >= EV_GAP_THRESHOLD) and bool(sig) and reproduced
        print(f"  => 사전판정: {'채택' if adopted else '미채택'}")

    print(f"\n[main] 전체 완료, elapsed={time.time()-_t0:.0f}s", flush=True)

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "2026-09-08_us_pullback_atr_pct_bucket_ev.results.json")
    with open(out_path, "w") as f:
        json.dump({
            "n_hits_total": len(rows), "bucket_reports": reports,
            "best_label": best_label, "worst_label": worst_label,
            "gap": gap, "z": z, "reproduced_early": rep_early, "reproduced_late": rep_late,
            "adopted": adopted,
            "rows": rows,
        }, f, default=str, indent=2, ensure_ascii=False)
    print(f"[main] 결과 JSON(히트별 원자료 포함): {out_path}")
