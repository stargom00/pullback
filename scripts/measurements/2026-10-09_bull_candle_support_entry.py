"""저점 주봉 히트 → 장대양봉 → 눌림 → 지지 확인봉 → 다음 날 시가 진입 EV.

사전등록: docs/bull_candle_support_entry.md §1(원문)·§1-구현. 같은 데이터로 세 번째 가설(직전 두 측정 기각) — Bonferroni 3(z ≥ 2.39).
히트 재구성·기준가·정지 주 제외·유니버스·기간·저유동성·데이터는 직전 스크립트(저점 출발, 기준양봉 출발)의 함수를 import해서 쓴다
(사본 금지 — 둘 다 실행 후라 수정하지 않는다). 장대양봉 = 기준양봉 측정의 `bull_departure`(종가 ≥ 전일 종가×1.15 그리고 종가 > 시가).

확인봉(원문): 장대양봉 다음 날부터 20거래일 안, 눌림(종가 < 장대양봉 종가) 1일 이후, 그때까지 모든 종가 ≥ 지지선((시가+종가)/2),
양봉이면서 종가 > 전일 고가인 첫 봉. 종가 < 지지선이 먼저 나오거나 20거래일 안에 확인봉이 없으면 미진입.
진입 = 확인봉 다음 유효봉 시가. 손절 = 장대양봉 시가. 진입가 ≤ 장대양봉 시가면 제외.
판정: A EV ≥ 0.15R · A−C z ≥ 2.39 · 시기 반분 양쪽(A ≥ 0.15R·A > C) · A·C nv ≥ 100.
실행 시각: harness.check_run_window(["KR"]). 실행은 해석 기본값 사용자 확인 뒤.
실행: MEAS_CACHE=<경로.pkl> python3 scripts/measurements/2026-10-09_bull_candle_support_entry.py  (결과 JSON 커밋 안 함)
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


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


bc = _load("bull_candle_departure", "2026-10-09_bull_candle_departure_entry.py")   # 장대양봉 정의
prev = bc.prev                                                                       # 히트·레이스·저유동성·데이터(저점 출발)
harness, lp, w = prev.harness, prev.lp, prev.w
KST = timezone(timedelta(hours=9))

# ── 사전등록 원문의 값 ────────────────────────────────────────────────
CONFIRM_WINDOW = 20              # "장대양봉 다음 날부터 20거래일 안에서"
EV_MIN = 0.15
Z_MIN = 2.39                     # "같은 데이터로 세 번째 가설이므로 Bonferroni 3을 적용한다(z ≥ 2.39)"
N_MIN = 100
RUN_MARKETS = ["KR"]

OUT = os.path.join(HERE, os.path.basename(__file__).replace(".py", ".results.json"))


def find_confirm(view: "harness.CleanView", k_bull: int):
    """장대양봉(원본 인덱스 k_bull) 뒤 확인봉 탐색. 반환 (사유, 확인봉 원본 인덱스 | None, 장대양봉 다음 날부터 센 유효 거래일 수 | None).
    사유: "confirmed" · "support_broken"(종가 < 지지선이 확인봉보다 먼저) · "no_confirm_in_window" · "window_incomplete"(데이터 끝).
    각 봉은 그 봉까지 정제한 df에서 판정(무효봉은 건너뛰고 거래일로 세지 않는다). 전일 고가 = 정제 df의 직전 봉 고가."""
    bull = view.prefix(k_bull)
    b_open, b_close = float(bull["Open"].iloc[-1]), float(bull["Close"].iloc[-1])
    support = (b_open + b_close) / 2
    pulled = False
    day = 0
    for k in range(k_bull + 1, len(view.raw)):
        if not view.valid(k):
            continue
        day += 1
        if day > CONFIRM_WINDOW:
            break
        p = view.prefix(k)
        o, c = float(p["Open"].iloc[-1]), float(p["Close"].iloc[-1])
        if c < support:
            return "support_broken", None, day
        if pulled and c > o and c > float(p["High"].iloc[-2]):
            return "confirmed", k, day
        if c < b_close:
            pulled = True                 # 눌림 — 같은 봉은 확인봉이 될 수 없다(눌림 1일 "이후")
    if day < CONFIRM_WINDOW:
        return "window_incomplete", None, None   # 데이터 끝 — 20거래일을 다 못 봤다(미진입과 구분해 기록)
    return "no_confirm_in_window", None, None


def race_open(view: "harness.CleanView", j_prev: int, stop: float, stats: Counter, group: str):
    """j_prev(확인봉·또는 C의 전일) 다음 유효봉 시가 진입 → 진입봉 포함 harness.race(저가 ≤ 손절 −1R·고가 ≥ 2R·60봉).
    저유동성은 j_prev까지(진입 시각에 알 수 있는 데이터)로 본다."""
    p = view.prefix(j_prev)
    if not prev.liquid(p):
        stats[f"{group}_illiquid"] += 1
        return None
    fut = view.future(j_prev)
    if len(fut) == 0:
        stats[f"{group}_no_next_bar"] += 1
        return None
    assert fut.index[0] > p.index[-1], "future lookahead"
    entry = float(fut["Open"].iloc[0])
    if not entry > stop:
        stats[f"{group}_entry_not_above_stop"] += 1
        return None
    outcome, r = harness.race(entry, stop, fut, prev.MAX_BARS)
    return {"date": str(fut.index[0].date()), "entry": entry, "stop": stop,
            "risk_pct": (entry - stop) / entry * 100, "outcome": outcome, "r": r}


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
    prev.self_check_hits(data, now, rng, stats)                     # 히트 재구성 300건 대조 — 같은 함수

    # 1) 히트 재구성(직전 두 측정과 같은 함수·같은 정지 주 제외)
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

    # 2) 첫 장대양봉 → 확인봉 → 다음 날 시가 진입
    views, events = {}, []
    for h in sorted(hits, key=lambda x: (x["label"], x["ticker"])):
        df = data[h["ticker"]]
        view = views.get(h["ticker"]) or views.setdefault(h["ticker"], harness.CleanView(df))
        k_bull = bc.bull_departure(view, str(h["base_date"].date()))
        h["bull_date"] = str(df.index[k_bull].date()) if k_bull is not None else None
        h["entry_date"] = None
        if k_bull is None:
            stats["no_bull_candle"] += 1
            continue
        reason, k_conf, day = find_confirm(view, k_bull)
        stats[reason] += 1
        if k_conf is None:
            continue
        bull = view.prefix(k_bull)
        b_open = float(bull["Open"].iloc[-1])
        conf_date = str(df.index[k_conf].date())
        setup = w.setup_type(df, conf_date, "KR", datetime.combine(pd.Timestamp(conf_date).date(), dtime(23, 59), tzinfo=KST).isoformat())
        a = race_open(view, k_conf, b_open, stats, "a")
        ev = {"ticker": h["ticker"], "label": str(h["label"].date()), "base_date": str(h["base_date"].date()),
              "base_price": h["base_price"], "bull_date": h["bull_date"], "bull_open": b_open,
              "bull_close": float(bull["Close"].iloc[-1]), "confirm_date": conf_date, "days_bull_to_confirm": day,
              "setup_class": setup.get("setup_class"), "A": a}
        if a:
            h["entry_date"] = a["date"]
        events.append(ev)
    a_rows = [e["A"] for e in events if e["A"]]
    assert a_rows, "A군 0건"
    avg_risk = sum(r["risk_pct"] for r in a_rows) / len(a_rows)
    print(f"[A] confirmed={len(events)} raced={len(a_rows)} avg_risk={avg_risk:.2f}% {time.time() - t0:.0f}s", flush=True)

    # 3) C군 — 같은 코호트에서 A 진입일까지 장대양봉이 없던 종목, 같은 날 시가 진입, 손절 = 기준가 × (1 − A 평균 위험폭%)
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
            if h["bull_date"] and pd.Timestamp(h["bull_date"]) <= d:
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
            jp = j - 1
            while jp >= 0 and not view.valid(jp):
                jp -= 1
            if jp < 0:
                stats["c_no_prev_bar"] += 1
                continue
            res = race_open(view, jp, h["base_price"] * (1 - avg_risk / 100), stats, "c")
            if res:
                assert res["date"] == e["A"]["date"]
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
    days = sorted(e["days_bull_to_confirm"] for e in events)
    no_entry = {k: stats[k] for k in ("no_bull_candle", "support_broken", "no_confirm_in_window", "window_incomplete",
                                      "a_illiquid", "a_no_next_bar", "a_entry_not_above_stop")}
    result = {
        "run_stamp": stamp, "run_window": run_window, "fetched_at": blob.get("fetched_at"),
        "universe": len(blob.get("names") or {}), "fetched": len(data), "failed": len(blob.get("failed") or []),
        "bars_median": med, "elapsed_s": round(time.time() - t0),
        "params": {"BULL_CLOSE_RATIO": bc.BULL_CLOSE_RATIO, "CONFIRM_WINDOW": CONFIRM_WINDOW, "EV_MIN": EV_MIN, "Z_MIN": Z_MIN,
                   "N_MIN": N_MIN, "MAX_BARS": prev.MAX_BARS, "KR_DAYS": prev.KR_DAYS, "SEED": prev.SEED},
        "stats": dict(stats), "a_avg_risk_pct": avg_risk,
        "A": sum_a, "C": sum_c, "z_A_vs_C": z, "half_split_date": split, "halves": halves, "checks": checks,
        "adopted": all(checks.values()),
        "record_only_by_setup_class": by_class(),
        "record_only_days_bull_to_confirm": ({"n": len(days), "median": median(days), "p25": days[len(days) // 4],
                                              "p75": days[len(days) * 3 // 4], "p90": days[int(len(days) * 0.9)], "max": days[-1]}
                                             if days else None),
        "record_only_no_entry_reasons": no_entry,
        "events": events, "controls": controls,
    }
    json.dump(result, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: result[k] for k in ("stats", "a_avg_risk_pct", "A", "C", "z_A_vs_C", "halves", "checks", "adopted",
                                             "record_only_by_setup_class", "record_only_days_bull_to_confirm",
                                             "record_only_no_entry_reasons")},
                     ensure_ascii=False, indent=1, default=str), flush=True)
    print(f"[done] {OUT}", flush=True)


if __name__ == "__main__":
    main()
