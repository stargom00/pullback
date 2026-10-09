"""저점 주봉 히트 → 기준양봉 출발 다음 거래일 진입 EV.

사전등록: docs/bull_candle_departure_entry.md §1(원문)·§1-구현. 직전 측정(docs/lowpoint_departure_rest_entry.md, 2026-10-09 기각)과
**출발 정의만** 다르다 — 히트 재구성·기준가·무효선·다음 날 진입·손절·청산·C군·유니버스·기간·제외 규칙은 직전 스크립트의 함수를
그대로 import해서 쓴다(사본 금지 — 직전 스크립트는 실행 후라 수정하지 않는다).

출발(기준양봉) — 레포에 "기준양봉" 수치 정의가 없어 사용자 지시 고정값을 쓴다(문서 §1-정의):
  기준일 다음 거래일부터, 그 봉까지 정제한 일봉(harness.CleanView)에서 **종가 ≥ 직전 유효봉 종가 × 1.15 그리고 종가 > 시가**인
  첫 봉. 종가 = naver 일봉 종가.
판정: A EV ≥ 0.15R · A−C z ≥ 2.24(직전 측정과 Bonferroni 2개) · 시기 반분 양쪽(A EV ≥ 0.15R·A > C) · A·C nv ≥ 100.
실행 시각: harness.check_run_window(["KR"]). 실행은 사용자 지시 후(종가베팅 채택 재현 검증이 끝난 뒤).
실행: MEAS_CACHE=<경로.pkl> python3 scripts/measurements/2026-10-09_bull_candle_departure_entry.py
      (파일명 날짜는 실행일로 바꾼다 — README 규칙2. 결과 JSON은 커밋하지 않는다 — 규칙5)
"""
import importlib.util
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, time as dtime, timedelta, timezone
from statistics import median

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("prev_lowpoint_departure",
                                               os.path.join(HERE, "2026-10-09_lowpoint_departure_rest_entry.py"))
prev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prev)          # 직전 측정의 함수(히트·레이스·저유동성·데이터) — 같은 구현을 쓴다

harness, lp, w = prev.harness, prev.lp, prev.w
KST = timezone(timedelta(hours=9))

# ── 사전등록 원문의 값 ────────────────────────────────────────────────
BULL_CLOSE_RATIO = 1.15          # "확정 봉 종가 ≥ 전일 종가 × 1.15" — 사용자 지시 고정값(레포에 기준양봉 정의 없음)
EV_MIN = 0.15
Z_MIN = 2.24                     # "A−C z ≥ 2.24(직전 측정과 Bonferroni 2개)"
N_MIN = 100
RUN_MARKETS = ["KR"]             # 원문 "harness.check_run_window(["KR"])"

OUT = os.path.join(HERE, os.path.basename(__file__).replace(".py", ".results.json"))


def is_bull_candle(prev_close: float, o: float, c: float) -> bool:
    """기준양봉 — 종가 ≥ 전일 종가 × 1.15(부동소수 오차만 허용 — 정확히 1.15배 포함) 그리고 종가 > 시가."""
    return prev_close > 0 and c >= prev_close * BULL_CLOSE_RATIO * (1 - 1e-12) and c > o


def bull_departure(view: "harness.CleanView", base_date: str) -> "int | None":
    """기준일 다음 거래일부터 첫 기준양봉의 원본 인덱스. 각 봉은 그 봉까지 정제한 df에서 판정(무효봉은 건너뜀),
    전일 종가 = 정제 df의 직전 봉 종가. 없으면 None. 기한 없음(직전 측정과 같음)."""
    idx = view.raw.index
    for k in range(int(idx.searchsorted(pd.Timestamp(base_date), side="right")), len(idx)):
        if not view.valid(k):
            continue
        p = view.prefix(k)
        if len(p) < 2:
            continue
        if is_bull_candle(float(p["Close"].iloc[-2]), float(p["Open"].iloc[-1]), float(p["Close"].iloc[-1])):
            return k
    return None


def main():
    run_window = harness.check_run_window(RUN_MARKETS)
    t0 = time.time()
    now = datetime.now(KST)
    blob = prev.load_data()
    data = blob["data"]
    stamp = harness.run_stamp(data)
    print(f"[stamp] {stamp}", flush=True)
    bars = sorted(len(df) for df in data.values())
    med = bars[len(bars) // 2] if bars else 0
    assert med >= prev.MIN_MEDIAN_BARS, f"fetch 깊이 부족: 중앙값 {med}봉 < {prev.MIN_MEDIAN_BARS}"
    rng = random.Random(prev.SEED)
    stats = Counter()
    prev.self_check_hits(data, now, rng, stats)                     # 히트 재구성 300건 대조 — 직전 측정과 같은 함수

    # 1) 히트 재구성(직전 측정과 같은 함수·같은 정지 주 제외)
    hits_by_label = defaultdict(list)
    for t, df in data.items():
        for label, bar_date, c0 in prev.weekly_hits(df["Close"], now):
            hits_by_label[label].append({"ticker": t, "label": label, "base_date": pd.Timestamp(bar_date), "base_price": c0})
    week_mode = {}
    for t, df in data.items():
        for lbl, bd in lp.resample_bars(df["Close"], prev.TF)["bar_date"].items():
            week_mode.setdefault(lbl, Counter())[pd.Timestamp(bd)] += 1
    hits = []
    for label, hs in hits_by_label.items():
        mode = week_mode[label].most_common(1)[0][0]
        for h in hs:
            if h["base_date"] < mode:
                stats["hit_stale"] += 1
                continue
            hits.append(h)
    stats["hits"] = len(hits)
    assert hits, "히트 0건"
    print(f"[hits] {len(hits)} {time.time() - t0:.0f}s", flush=True)

    # 2) 기준양봉 출발 → 무효선(stage_info)·모양·유형(기록) → A 진입
    views, events = {}, []
    for h in sorted(hits, key=lambda x: (x["label"], x["ticker"])):
        df = data[h["ticker"]]
        view = views.get(h["ticker"]) or views.setdefault(h["ticker"], harness.CleanView(df))
        base_date = str(h["base_date"].date())
        k_dep = bull_departure(view, base_date)
        h["departure_date"] = str(df.index[k_dep].date()) if k_dep is not None else None
        if k_dep is None:
            stats["no_departure"] += 1
            continue
        stats["departed"] += 1
        dep = h["departure_date"]
        dep_high = float(df["High"].iloc[k_dep])
        si = w.stage_info(base_date, dep, dep_high, df, dep)
        if si["invalid_line"] is None or si["stage_warning"]:
            stats["no_invalid_line"] += 1
            continue
        shape = w.departure_shape(df, dep, dep, dep_high)
        setup = w.setup_type(df, dep, "KR", datetime.combine(pd.Timestamp(dep).date(), dtime(23, 59), tzinfo=KST).isoformat())
        k = prev.next_valid(view, dep)
        if k is None:
            stats["a_no_next_bar"] += 1
            continue
        ev = {"ticker": h["ticker"], "label": str(h["label"].date()), "base_date": base_date, "base_price": h["base_price"],
              "departure_date": dep, "departure_close_vs_base": float(df["Close"].iloc[k_dep]) / h["base_price"] - 1,
              "invalid_line": si["invalid_line"], "days_to_departure": (pd.Timestamp(dep) - h["base_date"]).days,
              **shape, "setup_class": setup.get("setup_class")}
        ev["A"] = prev.race_at(view, k, si["invalid_line"], stats, "a")
        events.append(ev)
    a_rows = [e["A"] for e in events if e["A"]]
    assert a_rows, "A군 0건"
    avg_risk = sum(r["risk_pct"] for r in a_rows) / len(a_rows)
    print(f"[A] events={len(events)} raced={len(a_rows)} avg_risk={avg_risk:.2f}% {time.time() - t0:.0f}s", flush=True)

    # 3) C군 — 같은 코호트에서 그 날짜까지 기준양봉 출발이 없던 종목(직전 측정과 같은 구성·같은 손절식)
    cohort = defaultdict(list)
    for h in hits:
        cohort[str(h["label"].date())].append(h)
    seen, controls = set(), []
    for e in events:
        if not e["A"]:
            continue
        d = pd.Timestamp(e["A"]["date"])
        for h in cohort[e["label"]]:
            if h["ticker"] == e["ticker"]:
                continue
            if h["departure_date"] and pd.Timestamp(h["departure_date"]) <= d:
                continue
            key = (h["ticker"], e["label"], str(d.date()))
            if key in seen:
                continue
            seen.add(key)
            df = data[h["ticker"]]
            if d not in df.index:
                stats["c_no_bar_that_day"] += 1
                continue
            view = views.get(h["ticker"]) or views.setdefault(h["ticker"], harness.CleanView(df))
            j = df.index.get_loc(d)
            if not view.valid(j):
                stats["c_invalid_bar"] += 1
                continue
            res = prev.race_at(view, j, h["base_price"] * (1 - avg_risk / 100), stats, "c")
            if res:
                controls.append({"ticker": h["ticker"], "label": e["label"], "for": f"{e['ticker']}@{e['A']['date']}", "C": res})
    c_rows = [c["C"] for c in controls]
    assert c_rows, "C군 0건"

    # 4) 판정
    sum_a, sum_c = prev.summarize(a_rows), prev.summarize(c_rows)
    z, _ = harness.ev_gap_zscore(sum_c, sum_a)
    split = sorted(r["date"] for r in a_rows)[len(a_rows) // 2]
    halves = {"early": {"A": prev.summarize([r for r in a_rows if r["date"] < split]), "C": prev.summarize([r for r in c_rows if r["date"] < split])},
              "late": {"A": prev.summarize([r for r in a_rows if r["date"] >= split]), "C": prev.summarize([r for r in c_rows if r["date"] >= split])}}
    checks = {"ev_a_ge_min": sum_a["ev_R"] is not None and sum_a["ev_R"] >= EV_MIN,
              "z_ge_min": z is not None and z >= Z_MIN,
              "halves_hold": all(v["A"]["ev_R"] is not None and v["C"]["ev_R"] is not None and v["A"]["ev_R"] >= EV_MIN
                                 and v["A"]["ev_R"] > v["C"]["ev_R"] for v in halves.values()),
              "n_each_ge_min": sum_a["nv"] >= N_MIN and sum_c["nv"] >= N_MIN}

    def by_class():
        g = defaultdict(list)
        for e in events:
            if e["A"]:
                g[e.get("setup_class") or "unknown"].append(e["A"])
        return {k: prev.summarize(v) for k, v in sorted(g.items())}
    days = sorted(e["days_to_departure"] for e in events)
    result = {
        "run_stamp": stamp, "run_window": run_window, "fetched_at": blob.get("fetched_at"),
        "universe": len(blob.get("names") or {}), "fetched": len(data), "failed": len(blob.get("failed") or []),
        "bars_median": med, "elapsed_s": round(time.time() - t0),
        "params": {"BULL_CLOSE_RATIO": BULL_CLOSE_RATIO, "EV_MIN": EV_MIN, "Z_MIN": Z_MIN, "N_MIN": N_MIN,
                   "MAX_BARS": prev.MAX_BARS, "KR_DAYS": prev.KR_DAYS, "SEED": prev.SEED},
        "stats": dict(stats), "a_avg_risk_pct": avg_risk,
        "A": sum_a, "C": sum_c, "z_A_vs_C": z, "half_split_date": split, "halves": halves, "checks": checks,
        "adopted": all(checks.values()),
        "record_only_by_setup_class": by_class(),
        "record_only_days_to_departure": ({"n": len(days), "median": median(days), "p25": days[len(days) // 4],
                                           "p75": days[len(days) * 3 // 4], "p90": days[int(len(days) * 0.9)], "max": days[-1]}
                                          if days else None),
        "events": events, "controls": controls,
    }
    json.dump(result, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: result[k] for k in ("stats", "a_avg_risk_pct", "A", "C", "z_A_vs_C", "halves", "checks", "adopted",
                                             "record_only_by_setup_class", "record_only_days_to_departure")},
                     ensure_ascii=False, indent=1, default=str), flush=True)
    print(f"[done] {OUT}", flush=True)


if __name__ == "__main__":
    main()
