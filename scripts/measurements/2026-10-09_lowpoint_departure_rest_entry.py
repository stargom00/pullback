"""저점 주봉 히트 → 출발(+5%) 다음 거래일 진입 EV (관찰 페이지 정의 재구성).

사전등록: docs/lowpoint_departure_rest_entry.md §1(원문)·§1-구현(실행 전 해석 — 사용자 확인 전 기본값).
이 스크립트는 그 문서를 그대로 구현한다 — 조건·문턱·판정식을 결과 보고 바꾸지 않는다(격자탐색 금지).

재구성(관찰 페이지와 같은 함수 — 사본 금지):
  히트     lowpoint.resample_bars(주봉 W-FRI) · rsi_wilder_sma · lowpoint_signal — 각 주봉 마감 시점까지의 봉만(RSI는 과거
           봉만 쓰는 순차 계산이라 전체 한 번 계산 = 그 시점까지 자른 계산 — 표본 대조 assert로 확인). 운영과 같은 원본
           (naver 통합 일봉 종가, 정제 없음). 봉 수 < lowpoint.MIN_BARS["week"](17)인 주는 판정 안 함(운영 evaluate와 같음).
  기준가   그 주 마지막 거래일(bar_date) 종가 = 운영 관찰 레코드 base_price(close0)·base_date
  출발     lowpoint_watch.reach_info — 기준일 다음 거래일부터 일봉 고가 ≥ 기준가×1.05(정확히 1.05배 포함).
           **데이터 한계**: 운영은 KR 정규장 분봉 고가(v5.333)·확정 봉만(v5.339)인데 과거 분봉이 없어 통합 일봉 고가를 쓴다.
  무효선   lowpoint_watch.stage_info — 기준일~출발 전날 종가 최고(기준일 포함, 출발일 제외)
  출발일 모양  lowpoint_watch.departure_shape(거래량 배수·윗꼬리·종가 위치·ATR 배수) — 출발 고가 = 그날 일봉 고가
  유형(기록만)  lowpoint_watch.setup_type(출발일까지 확정 봉, mkt="KR") → setup_class(바닥형/추세 눌림/하락 추세/판정 불가)
가설·군(사전등록 원문):
  H1  A = 출발 다음 거래일 종가 진입, 손절 = 무효선, harness.race 2R(60봉, 같은 날 손절·목표면 손절).
      C = 같은 주봉 코호트에서 그 날짜까지 출발하지 않은 종목, 같은 날짜 종가 진입, 손절 = 그 종목 기준가 × (1 − A군 평균 위험폭%).
  H2  A를 출발 ATR 배수 중앙값(전체 A에서 한 번)으로 상·하 반 → |z| ≥ 2.24(Bonferroni 2가설).
판정: H1 — A EV ≥ 0.15R · A−C z ≥ 1.96 · 시기 반분 양쪽(A EV ≥ 0.15R·A > C) · A·C nv ≥ 100. H2 — |z(하 vs 상)| ≥ 2.24 · 각 반 nv ≥ 100.
필터: harness 표준 저유동성 컷(passes_liquidity_filter)을 A·C 진입봉에 똑같이(그 봉까지 정제한 df로 판정 — MA99 측정과 같음).

실행 시각: harness.check_run_window(("kr",)) — KR 종가 확정(20:10 KST) 이후 ~ 다음 KR 거래일 08:00 KST 전, KR 휴장일 종일.
실행: MEAS_CACHE=<경로.pkl> python3 scripts/measurements/2026-10-09_lowpoint_departure_rest_entry.py
      (파일명 날짜는 실행일로 바꾼다 — README 규칙2. 결과 JSON은 커밋하지 않는다 — 규칙5)
"""
import json
import os
import pickle
import random
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, time as dtime, timezone, timedelta
from statistics import median

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts", "screens"))

import harness  # noqa: E402
import lowpoint as lp  # noqa: E402
import lowpoint_watch as w  # noqa: E402
from scanner import price_frozen_check, volume_info  # noqa: E402

KST = timezone(timedelta(hours=9))

# ── 사전등록 원문의 값 ────────────────────────────────────────────────
TF = "week"                     # 원문 "과거 각 주봉 마감 시점"
MAX_BARS = 60                   # harness.race 기본값
EV_MIN = 0.15
Z_MIN = 1.96
Z_MIN_H2 = 2.24                 # 원문 "Bonferroni(2가설) z ≥ 2.24"
N_MIN = 100

# ── §1-구현에서 정한 값(사용자 확인 전 기본값 — 문서 §1-구현 표) ─────────────
KR_DAYS = lp.KR_DAYS["week"]    # 1900일 — 운영 주봉 스크린과 같은 조회 깊이
MIN_MEDIAN_BARS = 600           # fetch 깊이 하드 체크(중앙값 봉 수가 이 미만이면 실패) — 신규상장 섞인 전 종목이라 MA99(1000)보다 낮게
SELF_CHECK_N = 300              # 벡터 히트 탐지 vs lowpoint.evaluate(잘라서) 대조 표본 수(AI 어림값)
SEED = 20261009
RUN_MARKETS = ("kr",)

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   os.path.basename(__file__).replace(".py", ".results.json"))


# ══════════════════════════════════════════════════════════════════════
# 데이터 — 운영 저점 스크린과 같은 유니버스(KIND 상장 − 관리종목, 실행일 기준)·같은 원본(naver 통합 일봉)
# ══════════════════════════════════════════════════════════════════════
def load_data():
    cache = os.environ.get("MEAS_CACHE")
    if cache and os.path.exists(cache):
        print(f"[cache] load {cache}", flush=True)
        return pickle.load(open(cache, "rb"))
    uni, meta = {}, {}
    for board in ("kospi", "kosdaq"):
        u, m = lp.kr_universe(board)
        uni.update(u)
        meta[board] = {k: v for k, v in m.items() if k != "admin_excluded"} | {"admin_excluded_n": len(m.get("admin_excluded") or {})}
    import naver_kr
    from concurrent.futures import ThreadPoolExecutor, as_completed
    data, failed = {}, []

    def one(t):
        try:
            df = naver_kr.fetch_history(t, days=KR_DAYS)
            return t, (None if df is None or df.empty else df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"]))
        except Exception:
            return t, None
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=lp.FETCH_CONCURRENCY) as ex:
        futs = [ex.submit(one, t) for t in uni]
        for i, f in enumerate(as_completed(futs), 1):
            t, df = f.result()
            if df is None or df.empty:
                failed.append(t)
            else:
                data[t] = df
            if i % 300 == 0:
                print(f"[fetch] {i}/{len(uni)} {time.time() - t0:.0f}s", flush=True)
    blob = {"data": data, "names": uni, "failed": sorted(failed), "universe_meta": meta,
            "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S %Z")}
    if cache:
        pickle.dump(blob, open(cache, "wb"))
    return blob


# ══════════════════════════════════════════════════════════════════════
# 히트 재구성
# ══════════════════════════════════════════════════════════════════════
def weekly_hits(close: pd.Series, now: datetime):
    """주봉 마감 시점마다 운영 evaluate와 같은 판정 — [(label, bar_date, c0)]. 진행 중 주는 버린다(drop_in_progress)."""
    bars = lp.drop_in_progress(lp.resample_bars(close, TF), "kr", now)
    if len(bars) < max(lp.MIN_BARS[TF], 3):
        return []
    r = lp.rsi_wilder_sma(bars["close"], lp.RSI_PERIOD)
    c = bars["close"].astype(float)
    out = []
    for i in range(max(lp.MIN_BARS[TF], 3) - 1, len(bars)):
        r1, r2 = float(r.iloc[i - 1]), float(r.iloc[i - 2])
        if r1 != r1 or r2 != r2:
            continue
        if lp.lowpoint_signal(float(c.iloc[i]), float(c.iloc[i - 1]), r1, r2):
            out.append((bars.index[i], bars["bar_date"].iloc[i], float(c.iloc[i])))
    return out


def self_check_hits(data: dict, now: datetime, rng: random.Random, stats: Counter):
    """벡터 탐지 = 운영 lowpoint.evaluate(그 주 마감까지 자른 종가, now=그 주 확정 시각) — 표본 대조, 하나라도 다르면 실패."""
    tickers = sorted(data)
    checked = 0
    for _ in range(SELF_CHECK_N * 5):
        if checked >= SELF_CHECK_N:
            break
        t = rng.choice(tickers)
        close = data[t]["Close"]
        bars = lp.drop_in_progress(lp.resample_bars(close, TF), "kr", now)
        if len(bars) < 20:
            continue
        i = rng.randrange(max(lp.MIN_BARS[TF], 3) - 1, len(bars))
        label = bars.index[i]
        cut = close[close.index <= pd.Timestamp(bars["bar_date"].iloc[i])]
        ev = lp.evaluate(cut, TF, "kr", lp._confirm_dt(label, "kr"))
        fast = {h[0] for h in weekly_hits(cut, lp._confirm_dt(label, "kr"))}
        slow = ev.get("status") == "hit" and ev.get("label") == label
        assert (label in fast) == slow, f"히트 탐지 불일치 {t} {label}: fast={label in fast} evaluate={ev.get('status')}"
        checked += 1
    stats["self_check_hits"] = checked
    assert checked >= SELF_CHECK_N // 2, f"히트 대조 표본 부족 {checked}"


# ══════════════════════════════════════════════════════════════════════
# 진입·레이스
# ══════════════════════════════════════════════════════════════════════
def liquid(p: pd.DataFrame) -> bool:
    """harness 표준 저유동성 컷 — MA99 측정과 같은 방식(진입봉까지 정제한 df, scanner 필드 함수 그대로)."""
    c, h, lo, v = p["Close"], p["High"], p["Low"], p["Volume"]
    hit = {**volume_info(float(c.iloc[-1]), v), **price_frozen_check(c, h, lo, v)}
    return harness.passes_liquidity_filter(hit, True)


def race_at(view: "harness.CleanView", j: int, stop: float, stats: Counter, group: str):
    p = view.prefix(j)
    if not liquid(p):
        stats[f"{group}_illiquid"] += 1
        return None
    entry = float(p["Close"].iloc[-1])
    if not entry > stop:
        stats[f"{group}_entry_not_above_stop"] += 1
        return None
    fut = view.future(j)
    assert len(fut) == 0 or fut.index[0] > p.index[-1], "future lookahead"
    outcome, r = harness.race(entry, stop, fut, MAX_BARS)
    return {"date": str(p.index[-1].date()), "entry": entry, "stop": stop,
            "risk_pct": (entry - stop) / entry * 100, "outcome": outcome, "r": r}


def next_valid(view, after_ts) -> "int | None":
    idx = view.raw.index
    for k in range(int(idx.searchsorted(pd.Timestamp(after_ts), side="right")), len(idx)):
        if view.valid(k):
            return k
    return None


def summarize(rows):
    return harness.ev_summary([(r["outcome"], r["r"]) for r in rows])


def main():
    run_window = harness.check_run_window(RUN_MARKETS)
    t0 = time.time()
    now = datetime.now(KST)
    blob = load_data()
    data = blob["data"]
    stamp = harness.run_stamp(data)
    print(f"[stamp] {stamp}", flush=True)
    bars = sorted(len(df) for df in data.values())
    med = bars[len(bars) // 2] if bars else 0
    assert med >= MIN_MEDIAN_BARS, f"fetch 깊이 부족: 중앙값 {med}봉 < {MIN_MEDIAN_BARS}"
    rng = random.Random(SEED)
    stats = Counter()
    self_check_hits(data, now, rng, stats)

    # 1) 히트 재구성 + 주별 정지 종목 제외(운영 stale 규칙 — 그 주 마지막 거래일이 그 주 최빈 마지막 거래일보다 이르면 제외)
    hits_by_label = defaultdict(list)
    for t, df in data.items():
        for label, bar_date, c0 in weekly_hits(df["Close"], now):
            hits_by_label[label].append({"ticker": t, "label": label, "base_date": pd.Timestamp(bar_date), "base_price": c0})
    week_mode = {}
    for t, df in data.items():
        for lbl, bd in lp.resample_bars(df["Close"], TF)["bar_date"].items():
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
    print(f"[hits] {len(hits)} (stale 제외 {stats['hit_stale']}) {time.time() - t0:.0f}s", flush=True)

    # 2) 출발·무효선·모양·유형(관찰 페이지 함수 그대로)
    views = {}
    events = []
    for h in sorted(hits, key=lambda x: (x["label"], x["ticker"])):
        df = data[h["ticker"]]
        base_date = str(h["base_date"].date())
        ri = w.reach_info(h["base_price"], base_date, df)
        h["departure_date"] = ri["reached_date"]
        if not ri["reached"]:
            stats["no_departure"] += 1
            continue
        stats["departed"] += 1
        dep = ri["reached_date"]
        dep_high = float(df.loc[pd.Timestamp(dep), "High"])
        si = w.stage_info(base_date, dep, dep_high, df, dep)        # 무효선은 출발 전 종가만 — through = 출발일
        if si["invalid_line"] is None or si["stage_warning"]:
            stats["no_invalid_line"] += 1
            continue
        shape = w.departure_shape(df, dep, dep, dep_high)
        setup = w.setup_type(df, dep, "KR", datetime.combine(pd.Timestamp(dep).date(), dtime(23, 59), tzinfo=KST).isoformat())
        view = views.get(h["ticker"]) or views.setdefault(h["ticker"], harness.CleanView(df))
        k = next_valid(view, dep)
        if k is None:
            stats["a_no_next_bar"] += 1
            continue
        ev = {"ticker": h["ticker"], "label": str(h["label"].date()), "base_date": base_date, "base_price": h["base_price"],
              "departure_date": dep, "departure_high": dep_high, "invalid_line": si["invalid_line"],
              "days_to_departure": ri["reached_days"], **shape, "setup_class": setup.get("setup_class"),
              "entry_close_vs_invalid": float(df["Close"].iloc[k]) / si["invalid_line"] - 1}
        ev["A"] = race_at(view, k, si["invalid_line"], stats, "a")
        events.append(ev)
    a_rows = [e["A"] for e in events if e["A"]]
    assert a_rows, "A군 0건"
    avg_risk = sum(r["risk_pct"] for r in a_rows) / len(a_rows)
    print(f"[A] events={len(events)} raced={len(a_rows)} avg_risk={avg_risk:.2f}% {time.time() - t0:.0f}s", flush=True)

    # 3) C군 — 같은 코호트에서 그 날짜까지 출발 안 한 종목, 같은 날짜 종가, 손절 = 기준가 × (1 − A 평균 위험폭%)
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
                continue                                              # 그 날짜까지 이미 출발
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
            res = race_at(view, j, h["base_price"] * (1 - avg_risk / 100), stats, "c")
            if res:
                controls.append({"ticker": h["ticker"], "label": e["label"], "for": f"{e['ticker']}@{e['A']['date']}", "C": res})
    c_rows = [c["C"] for c in controls]
    assert c_rows, "C군 0건"

    # 4) 집계·판정
    sum_a, sum_c = summarize(a_rows), summarize(c_rows)
    z_h1, _ = harness.ev_gap_zscore(sum_c, sum_a)
    split = sorted(r["date"] for r in a_rows)[len(a_rows) // 2]
    halves = {"early": {"A": summarize([r for r in a_rows if r["date"] < split]), "C": summarize([r for r in c_rows if r["date"] < split])},
              "late": {"A": summarize([r for r in a_rows if r["date"] >= split]), "C": summarize([r for r in c_rows if r["date"] >= split])}}
    h1 = {"ev_a_ge_min": sum_a["ev_R"] is not None and sum_a["ev_R"] >= EV_MIN,
          "z_ge_min": z_h1 is not None and z_h1 >= Z_MIN,
          "halves_hold": all(v["A"]["ev_R"] is not None and v["C"]["ev_R"] is not None and v["A"]["ev_R"] >= EV_MIN
                             and v["A"]["ev_R"] > v["C"]["ev_R"] for v in halves.values()),
          "n_each_ge_min": sum_a["nv"] >= N_MIN and sum_c["nv"] >= N_MIN}
    with_atr = [e for e in events if e["A"] and e.get("departure_atr_mult") is not None]
    atr_med = median(e["departure_atr_mult"] for e in with_atr) if with_atr else None
    hi = summarize([e["A"] for e in with_atr if e["departure_atr_mult"] >= atr_med]) if with_atr else None
    lo = summarize([e["A"] for e in with_atr if e["departure_atr_mult"] < atr_med]) if with_atr else None
    z_h2, _ = harness.ev_gap_zscore(lo, hi) if with_atr else (None, False)
    h2 = {"abs_z_ge_min": z_h2 is not None and abs(z_h2) >= Z_MIN_H2,
          "n_each_ge_min": bool(hi and lo and hi["nv"] >= N_MIN and lo["nv"] >= N_MIN)}
    stats["a_without_atr_mult"] = sum(1 for e in events if e["A"] and e.get("departure_atr_mult") is None)

    def by_class(rows_events):
        g = defaultdict(list)
        for e in rows_events:
            if e["A"]:
                g[e.get("setup_class") or "unknown"].append(e["A"])
        return {k: summarize(v) for k, v in sorted(g.items())}

    result = {
        "run_stamp": stamp, "run_window": run_window, "fetched_at": blob.get("fetched_at"),
        "universe": len(blob.get("names") or {}), "fetched": len(data), "failed": len(blob.get("failed") or []),
        "universe_meta": blob.get("universe_meta"), "bars_median": med, "elapsed_s": round(time.time() - t0),
        "params": {"TF": TF, "MAX_BARS": MAX_BARS, "EV_MIN": EV_MIN, "Z_MIN": Z_MIN, "Z_MIN_H2": Z_MIN_H2, "N_MIN": N_MIN,
                   "KR_DAYS": KR_DAYS, "REACH_PCT": w.REACH_PCT, "SEED": SEED},
        "stats": dict(stats), "a_avg_risk_pct": avg_risk,
        "H1": {"A": sum_a, "C": sum_c, "z_A_vs_C": z_h1, "half_split_date": split, "halves": halves, "checks": h1,
               "adopted": all(h1.values())},
        "H2": {"atr_mult_median": atr_med, "high": hi, "low": lo, "z_low_vs_high": z_h2, "checks": h2,
               "adopted": all(h2.values())},
        "record_only_by_setup_class": by_class(events),
        "events": events, "controls": controls,
    }
    json.dump(result, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: result[k] for k in ("stats", "a_avg_risk_pct", "H1", "H2", "record_only_by_setup_class")},
                     ensure_ascii=False, indent=1, default=str), flush=True)
    print(f"[done] {OUT}", flush=True)


if __name__ == "__main__":
    main()
