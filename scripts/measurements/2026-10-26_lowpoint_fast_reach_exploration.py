"""저점 주봉 히트 → 20거래일 안 +5% 도달 vs 손실 — 신호 시점 특징 탐색 (KR+US).

사전등록: docs/lowpoint_fast_reach_exploration.md §1(원문)·§1-구현(실행 전 해석). **탐색(판정 없음)** — 특징별 도달률·손실률을
기술만 한다. 여기서 튀는 특징은 별도 확인 측정을 사전등록한 뒤에만 필터·배지가 될 수 있다(§3).

재구성(운영·10-09 측정과 같은 함수 — 사본 금지):
  히트     lowpoint.resample_bars(주봉 W-FRI) · rsi_wilder_sma · lowpoint_signal — 각 주봉 마감 시점까지의 봉만. 시장별 300건을
           lowpoint.evaluate(잘라서)와 대조(assert). 봉 수 < 17인 주는 판정 안 함. 정지 종목(그 주 마지막 거래일 < 최빈값) 제외.
  진입     양봉 주 마감 **다음 유효 거래일 시가**(투결혼 조건 3). 시가 ≥ 기준가×1.05면 이미 목표 → already_reached_at_open 집계.
  레이스   진입일 포함 20거래일. 고가 ≥ 진입×1.05 먼저 = reach / 저가 ≤ 진입×0.90 먼저 = loss10 / 둘 다 없음 = neither.
           같은 날 둘 다면 손실 우선(harness.race 관례). 따로: −20% 터치, 도달 봉 수, MFE/MAE, 20일째 종가 수익률.
  필터     harness.passes_liquidity_filter(진입봉까지 정제한 df) — README 규칙6.
  특징     F1~F18 — 전부 양봉 주 마감 시점까지의 데이터만(미래 금지). 정의는 문서 I8~I16.
집계: 시장별 기준율 + 연도별, 연속 특징은 시장별 사분위, 이산 특징은 값별. 판정식 없음.

실행 시각: harness.check_run_window(("kr","us")) — KST 20:10~22:30.
실행: MEAS_CACHE=<경로.pkl> python3 scripts/measurements/2026-10-26_lowpoint_fast_reach_exploration.py
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
from statistics import median, mean

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

# ── 사전등록 원문·§1-구현의 값 ─────────────────────────────────────────
TF = "week"
RACE_BARS = 20                  # 원문 "20거래일" — 진입일 포함
REACH_PCT = 5.0                 # 사용자 지시(+5%, lowpoint_eval.SHORT_GOAL_PCT와 같은 값)
LOSS_PCT = 10.0                 # 1차 손실선 — AI 어림값(문서 출처 고지)
LOSS2_PCT = 20.0                # 사용자 피해 사례 −20% — 터치 여부만 기록
SURGE_WEEK_PCT = 15.0           # F12 "하락 중 급등 주" — AI 어림값(bull candle 측정의 +15%와 같은 값)
SURGE_LOOKBACK_W = 12
MIN_MEDIAN_BARS = 600
MIN_RACED = 100                 # 시장별 레이스 가능 건 하드 체크
SELF_CHECK_N = 300
SEED = 20261026
RUN_MARKETS = ("kr", "us")
KR_DAYS = lp.KR_DAYS["week"]
US_PERIOD = lp.US_PERIOD["week"]
US_BENCH = "^IXIC"

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   os.path.basename(__file__).replace(".py", ".results.json"))


# ══════════════════════════════════════════════════════════════════════
# 데이터 — 운영 저점 스크린과 같은 유니버스·같은 원본
# ══════════════════════════════════════════════════════════════════════
def _kr_frames(tickers):
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
        futs = [ex.submit(one, t) for t in tickers]
        for i, f in enumerate(as_completed(futs), 1):
            t, df = f.result()
            (failed.append(t) if df is None or df.empty else data.__setitem__(t, df))
            if i % 300 == 0:
                print(f"[fetch kr] {i}/{len(tickers)} {time.time() - t0:.0f}s", flush=True)
    return data, failed


def _us_frames(tickers, batch=100):
    data = {}
    t0 = time.time()
    for i in range(0, len(tickers), batch):
        got = harness._fetch_us_batch(tickers[i:i + batch], period=US_PERIOD, auto_adjust=lp.US_AUTO_ADJUST)
        for t, df in got.items():
            df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
            if not df.empty:
                data[t] = df
        print(f"[fetch us] {min(i + batch, len(tickers))}/{len(tickers)} {time.time() - t0:.0f}s", flush=True)
    return data, [t for t in tickers if t not in data]


def load_data():
    cache = os.environ.get("MEAS_CACHE")
    if cache and os.path.exists(cache):
        print(f"[cache] load {cache}", flush=True)
        return pickle.load(open(cache, "rb"))
    blob = {"kr": {}, "us": {}, "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S %Z")}
    uni, board_of, meta = {}, {}, {}
    for board in ("kospi", "kosdaq"):
        u, m = lp.kr_universe(board)
        uni.update(u)
        board_of.update({t: board for t in u})
        meta[board] = {k: v for k, v in m.items() if k != "admin_excluded"} | {"admin_excluded_n": len(m.get("admin_excluded") or {})}
    data, failed = _kr_frames(list(uni))
    blob["kr"] = {"data": data, "names": uni, "board": board_of, "failed": sorted(failed), "universe_meta": meta}
    uni_us, meta_us = lp.us_universe()
    data, failed = _us_frames(list(uni_us))
    blob["us"] = {"data": data, "names": uni_us, "failed": sorted(failed), "universe_meta": meta_us}
    bench = harness.fetch_kr_benchmarks(days=KR_DAYS)
    ixic = harness._fetch_us_batch([US_BENCH], period=US_PERIOD, auto_adjust=True).get(US_BENCH)
    blob["bench"] = {"kospi": bench["kospi"], "kosdaq": bench["kosdaq"], "us": ixic}
    if cache:
        pickle.dump(blob, open(cache, "wb"))
    return blob


# ══════════════════════════════════════════════════════════════════════
# 히트 재구성 (10-09 측정과 같음 — mkt 인자만 추가)
# ══════════════════════════════════════════════════════════════════════
def weekly_bars(close: pd.Series, mkt: str, now: datetime):
    return lp.drop_in_progress(lp.resample_bars(close, TF), mkt, now)


def weekly_hits(close: pd.Series, mkt: str, now: datetime):
    """[(label, bar_date, c0, i)] — i = 주봉 인덱스(특징 계산용)."""
    bars = weekly_bars(close, mkt, now)
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
            out.append((bars.index[i], bars["bar_date"].iloc[i], float(c.iloc[i]), i))
    return out


def self_check_hits(data: dict, mkt: str, now: datetime, rng: random.Random, stats: Counter):
    tickers = sorted(data)
    checked = 0
    for _ in range(SELF_CHECK_N * 5):
        if checked >= SELF_CHECK_N:
            break
        t = rng.choice(tickers)
        close = data[t]["Close"]
        bars = weekly_bars(close, mkt, now)
        if len(bars) < 20:
            continue
        i = rng.randrange(max(lp.MIN_BARS[TF], 3) - 1, len(bars))
        label = bars.index[i]
        cut = close[close.index <= pd.Timestamp(bars["bar_date"].iloc[i])]
        ev = lp.evaluate(cut, TF, mkt, lp._confirm_dt(label, mkt))
        fast = {h[0] for h in weekly_hits(cut, mkt, lp._confirm_dt(label, mkt))}
        slow = ev.get("status") == "hit" and ev.get("label") == label
        assert (label in fast) == slow, f"히트 탐지 불일치 {mkt} {t} {label}: fast={label in fast} evaluate={ev.get('status')}"
        checked += 1
    stats[f"{mkt}_self_check_hits"] = checked
    assert checked >= SELF_CHECK_N // 2, f"{mkt} 히트 대조 표본 부족 {checked}"


# ══════════════════════════════════════════════════════════════════════
# 특징 — 전부 신호 주 마감(bar_date)까지의 데이터만
# ══════════════════════════════════════════════════════════════════════
def _pct(a, b):
    return None if a is None or b is None or b == 0 else (a / b - 1) * 100


def features(df: pd.DataFrame, wk: pd.DataFrame, rsi: pd.Series, i: int, bar_date, mkt: str, bench_close, now_iso: str) -> dict:
    """df = 일봉(정제 없음, 운영과 같음), wk = 주봉(close·bar_date), rsi = 주봉 RSI, i = 신호 주 인덱스."""
    bd = pd.Timestamp(bar_date)
    d = df[df.index <= bd]
    # 신호 주 일봉 구간 = 직전 주 bar_date 다음날 ~ bar_date
    prev_bd = pd.Timestamp(wk["bar_date"].iloc[i - 1])
    wdays = d[d.index > prev_bd]
    c0, c1 = float(wk["close"].iloc[i]), float(wk["close"].iloc[i - 1])
    f = {}
    if len(wdays):
        o0, h0, l0 = float(wdays["Open"].iloc[0]), float(wdays["High"].max()), float(wdays["Low"].min())
        f["F1_body_pct"] = _pct(c0, o0) if o0 > 0 else None
        f["F2_close_pos"] = (c0 - l0) / (h0 - l0) if h0 > l0 else None
        f["F3_lower_wick_pct"] = (min(o0, c0) - l0) / l0 * 100 if l0 > 0 else None
        tv_week = float((wdays["Close"] * wdays["Volume"]).sum())
    else:
        tv_week = None
    # F4 거래대금 배수 — 직전 4주 각각의 거래대금(주봉 구간별), 4주 전부 있어야
    tvs = []
    for k in range(1, 5):
        if i - k < 1:
            break
        a, b = pd.Timestamp(wk["bar_date"].iloc[i - k - 1]), pd.Timestamp(wk["bar_date"].iloc[i - k])
        seg = d[(d.index > a) & (d.index <= b)]
        if seg.empty:
            break
        tvs.append(float((seg["Close"] * seg["Volume"]).sum()))
    f["F4_turnover_ratio"] = (tv_week / (sum(tvs) / 4)) if tv_week is not None and len(tvs) == 4 and sum(tvs) > 0 else None
    # F5 RSI<30 연속 주 수(양봉 주 직전부터), F6 반등폭
    n = 0
    k = i - 1
    while k >= 0 and float(rsi.iloc[k]) == float(rsi.iloc[k]) and float(rsi.iloc[k]) < lp.RSI_LEVEL:
        n += 1
        k -= 1
    f["F5_weeks_below30"] = n
    f["F6_rsi_rebound"] = float(rsi.iloc[i]) - float(rsi.iloc[i - 1])
    # F7~F10 위치
    y = d[d.index > bd - pd.Timedelta(weeks=52)]
    f["F7_vs_52w_low_pct"] = _pct(c0, float(y["Low"].min())) if len(y) else None
    f["F8_off_52w_high_pct"] = _pct(c0, float(y["High"].max())) if len(y) else None
    wc = wk["close"].astype(float)
    f["F9_vs_ma20w_pct"] = _pct(c0, float(wc.iloc[max(0, i - 19):i + 1].mean())) if i >= 19 else None
    f["F10_vs_ma60w_pct"] = _pct(c0, float(wc.iloc[i - 59:i + 1].mean())) if i >= 59 else None
    # F11 직전 4주 낙폭, F12 하락 중 급등 주
    f["F11_prior4w_pct"] = _pct(c1, float(wc.iloc[i - 5])) if i >= 5 else None
    if i >= 2:
        lo_k = max(1, i - SURGE_LOOKBACK_W)
        wret = (wc.iloc[lo_k:i] / wc.iloc[lo_k - 1:i - 1].values - 1) * 100
        f["F12_surge_in_decline"] = int(bool((wret >= SURGE_WEEK_PCT).any()))
    else:
        f["F12_surge_in_decline"] = None
    # F13 20일 평균 거래대금, F14 상장역사 proxy
    t20 = d.tail(20)
    f["F13_avg_turnover_20d"] = float((t20["Close"] * t20["Volume"]).mean()) if len(t20) == 20 else None
    f["F14_history_days"] = int(len(d))
    f["F14_capped"] = bool(len(d) >= (KR_DAYS * 0.65 if mkt == "kr" else 1200))   # 조회 캡 근처 = 실제 상장역사 더 김(AI 어림값)
    # F16 월봉 RSI<30 동시 — 신호 주 마감 시점에 확정된 마지막 월봉
    mb = lp.drop_in_progress(lp.resample_bars(d["Close"], "month"), mkt, lp._confirm_dt(wk.index[i], mkt))
    if len(mb) >= lp.MIN_BARS["month"]:
        mr = lp.rsi_wilder_sma(mb["close"], lp.RSI_PERIOD)
        v = float(mr.iloc[-1])
        f["F16_month_rsi_below30"] = int(v < lp.RSI_LEVEL) if v == v else None
    else:
        f["F16_month_rsi_below30"] = None
    # F17 시장 20일 수익률
    if bench_close is not None:
        b = bench_close[bench_close.index <= bd]
        f["F17_market_20d_pct"] = _pct(float(b.iloc[-1]), float(b.iloc[-21])) if len(b) >= 21 else None
    else:
        f["F17_market_20d_pct"] = None
    # F18 유형
    try:
        st = w.setup_type(df, str(bd.date()), "KR" if mkt == "kr" else "US", now_iso)
        f["F18_setup_class"] = st.get("setup_class") or "unknown"
    except Exception:
        f["F18_setup_class"] = "unknown"
    return f


# ══════════════════════════════════════════════════════════════════════
# 진입·레이스
# ══════════════════════════════════════════════════════════════════════
def liquid(p: pd.DataFrame, is_kr: bool) -> bool:
    c, h, lo, v = p["Close"], p["High"], p["Low"], p["Volume"]
    hit = {**volume_info(float(c.iloc[-1]), v), **price_frozen_check(c, h, lo, v)}
    return harness.passes_liquidity_filter(hit, is_kr)


def race20(entry: float, fut: pd.DataFrame) -> dict:
    """진입일 포함 RACE_BARS봉. fut.iloc[0] = 진입일 봉. 반환 outcome: reach|loss10|neither|insufficient."""
    if len(fut) < RACE_BARS:
        return {"outcome": "insufficient"}
    up, dn, dn2 = entry * (1 + REACH_PCT / 100), entry * (1 - LOSS_PCT / 100), entry * (1 - LOSS2_PCT / 100)
    out, days = "neither", None
    touched20 = False
    mfe = mae = 0.0
    for k in range(RACE_BARS):
        hi, lo = float(fut["High"].iloc[k]), float(fut["Low"].iloc[k])
        mfe, mae = max(mfe, (hi / entry - 1) * 100), min(mae, (lo / entry - 1) * 100)
        if lo <= dn2:
            touched20 = True
        if out == "neither":
            if lo <= dn:                       # 같은 날 둘 다면 손실 우선
                out, days = "loss10", k + 1
            elif hi >= up:
                out, days = "reach", k + 1
    return {"outcome": out, "days": days, "touched20": touched20, "mfe_pct": mfe, "mae_pct": mae,
            "ret20_pct": (float(fut["Close"].iloc[RACE_BARS - 1]) / entry - 1) * 100}


def next_valid(view, after_ts) -> "int | None":
    idx = view.raw.index
    for k in range(int(idx.searchsorted(pd.Timestamp(after_ts), side="right")), len(idx)):
        if view.valid(k):
            return k
    return None


# ══════════════════════════════════════════════════════════════════════
# 집계 — 판정 없음
# ══════════════════════════════════════════════════════════════════════
def rates(rows: list) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    c = Counter(r["race"]["outcome"] for r in rows)
    reached = [r["race"]["days"] for r in rows if r["race"]["outcome"] == "reach"]
    ret = [r["race"]["ret20_pct"] for r in rows]
    return {"n": n, "reach_rate": c["reach"] / n, "loss10_rate": c["loss10"] / n, "neither_rate": c["neither"] / n,
            "touch20_rate": sum(1 for r in rows if r["race"]["touched20"]) / n,
            "days_to_reach_median": median(reached) if reached else None,
            "ret20_median": median(ret), "ret20_mean": mean(ret),
            "mae_median": median(r["race"]["mae_pct"] for r in rows), "mfe_median": median(r["race"]["mfe_pct"] for r in rows)}


def _prop_z(p1, n1, p2, n2):
    if not n1 or not n2:
        return None
    p = (p1 * n1 + p2 * n2) / (n1 + n2)
    se = (p * (1 - p) * (1 / n1 + 1 / n2)) ** 0.5
    return None if se == 0 else (p2 - p1) / se


CONTINUOUS = ["F1_body_pct", "F2_close_pos", "F3_lower_wick_pct", "F4_turnover_ratio", "F5_weeks_below30", "F6_rsi_rebound",
              "F7_vs_52w_low_pct", "F8_off_52w_high_pct", "F9_vs_ma20w_pct", "F10_vs_ma60w_pct", "F11_prior4w_pct",
              "F13_avg_turnover_20d", "F14_history_days", "F17_market_20d_pct"]
DISCRETE = ["F12_surge_in_decline", "F16_month_rsi_below30", "F18_setup_class", "F15_market", "F14_capped"]


def by_feature(rows: list) -> dict:
    out = {}
    for f in CONTINUOUS:
        have = [r for r in rows if r["feat"].get(f) is not None]
        if len(have) < 8:
            out[f] = {"n": len(have), "note": "표본 부족"}
            continue
        vals = sorted(r["feat"][f] for r in have)
        qs = [vals[len(vals) * k // 4] for k in (1, 2, 3)]
        bins = defaultdict(list)
        for r in have:
            v = r["feat"][f]
            q = 1 if v < qs[0] else 2 if v < qs[1] else 3 if v < qs[2] else 4
            bins[q].append(r)
        q1, q4 = bins.get(1, []), bins.get(4, [])
        r1, r4 = rates(q1), rates(q4)
        z_mw, _ = harness.mannwhitney_zscore(pd.Series([r["race"]["ret20_pct"] for r in q1]),
                                             pd.Series([r["race"]["ret20_pct"] for r in q4])) if q1 and q4 else (None, False)
        out[f] = {"cuts": qs, "quartiles": {q: rates(bins.get(q, [])) for q in (1, 2, 3, 4)},
                  "q4_minus_q1": {"reach_rate": (r4.get("reach_rate") or 0) - (r1.get("reach_rate") or 0),
                                  "loss10_rate": (r4.get("loss10_rate") or 0) - (r1.get("loss10_rate") or 0),
                                  "z_reach_prop": _prop_z(r1.get("reach_rate", 0), r1["n"], r4.get("reach_rate", 0), r4["n"]),
                                  "z_ret20_mw": z_mw}}
    for f in DISCRETE:
        bins = defaultdict(list)
        for r in rows:
            bins[str(r["feat"].get(f))].append(r)
        out[f] = {k: rates(v) for k, v in sorted(bins.items())}
    return out


# ══════════════════════════════════════════════════════════════════════
def main():
    run_window = harness.check_run_window(RUN_MARKETS)
    t0 = time.time()
    now = datetime.now(KST)
    now_iso = now.isoformat()
    blob = load_data()
    rng = random.Random(SEED)
    stats = Counter()
    result = {"run_window": run_window, "fetched_at": blob.get("fetched_at"), "markets": {},
              "params": {"TF": TF, "RACE_BARS": RACE_BARS, "REACH_PCT": REACH_PCT, "LOSS_PCT": LOSS_PCT, "LOSS2_PCT": LOSS2_PCT,
                         "SURGE_WEEK_PCT": SURGE_WEEK_PCT, "KR_DAYS": KR_DAYS, "US_PERIOD": US_PERIOD, "SEED": SEED}}
    all_rows = []
    for mkt in RUN_MARKETS:
        part = blob[mkt]
        data = part["data"]
        is_kr = mkt == "kr"
        stamp = harness.run_stamp(data)
        bars = sorted(len(df) for df in data.values())
        med = bars[len(bars) // 2] if bars else 0
        assert med >= MIN_MEDIAN_BARS, f"{mkt} fetch 깊이 부족: 중앙값 {med}봉 < {MIN_MEDIAN_BARS}"
        self_check_hits(data, mkt, now, rng, stats)

        # 1) 히트 + 정지 종목 제외
        hits_by_label, week_mode = defaultdict(list), {}
        wk_cache, rsi_cache = {}, {}
        for t, df in data.items():
            wk = weekly_bars(df["Close"], mkt, now)
            wk_cache[t] = wk
            for lbl, bd in wk["bar_date"].items():
                week_mode.setdefault(lbl, Counter())[pd.Timestamp(bd)] += 1
            hs = weekly_hits(df["Close"], mkt, now)
            if hs:
                rsi_cache[t] = lp.rsi_wilder_sma(wk["close"], lp.RSI_PERIOD)
            for label, bar_date, c0, i in hs:
                hits_by_label[label].append({"ticker": t, "label": label, "base_date": pd.Timestamp(bar_date), "base_price": c0, "i": i})
        hits = []
        for label, hs in hits_by_label.items():
            mode = week_mode[label].most_common(1)[0][0]
            for h in hs:
                if h["base_date"] < mode:
                    stats[f"{mkt}_hit_stale"] += 1
                    continue
                hits.append(h)
        stats[f"{mkt}_hits"] = len(hits)
        assert hits, f"{mkt} 히트 0건"
        print(f"[{mkt}] hits={len(hits)} stale={stats[f'{mkt}_hit_stale']} {time.time() - t0:.0f}s", flush=True)

        # 2) 진입(다음 유효 거래일 시가)·레이스·특징
        views, rows = {}, []
        bench = blob["bench"]
        for h in sorted(hits, key=lambda x: (x["label"], x["ticker"])):
            t = h["ticker"]
            df = data[t]
            view = views.get(t) or views.setdefault(t, harness.CleanView(df))
            k = next_valid(view, h["base_date"])
            if k is None:
                stats[f"{mkt}_no_next_bar"] += 1
                continue
            p = view.prefix(k)
            if not liquid(p, is_kr):
                stats[f"{mkt}_illiquid"] += 1
                continue
            entry = float(p["Open"].iloc[-1])
            if not entry > 0:
                stats[f"{mkt}_bad_open"] += 1
                continue
            if entry >= h["base_price"] * (1 + REACH_PCT / 100):
                stats[f"{mkt}_already_reached_at_open"] += 1
                continue
            fut = view.future(k - 1) if k > 0 else view.future(-1)   # 진입일 봉부터
            assert len(fut) and fut.index[0] == p.index[-1], "race window must start at entry bar"
            rc = race20(entry, fut)
            if rc["outcome"] == "insufficient":
                stats[f"{mkt}_insufficient"] += 1
                continue
            bc = bench["us"]["Close"] if (not is_kr and bench.get("us") is not None) else \
                 (bench.get(part["board"].get(t, "kospi")) if is_kr else None)
            if isinstance(bc, pd.DataFrame):
                bc = bc["Close"]
            feat = features(df, wk_cache[t], rsi_cache[t], h["i"], h["base_date"], mkt, bc, now_iso)
            feat["F15_market"] = (part["board"].get(t) or "kr").upper() if is_kr else "US"
            rows.append({"market": mkt, "ticker": t, "label": str(h["label"].date()), "base_date": str(h["base_date"].date()),
                         "base_price": h["base_price"], "entry_date": str(p.index[-1].date()), "entry": entry,
                         "gap_pct": _pct(entry, h["base_price"]), "race": rc, "feat": feat})
        stats[f"{mkt}_raced"] = len(rows)
        assert len(rows) >= MIN_RACED, f"{mkt} 레이스 가능 건 {len(rows)} < {MIN_RACED}"
        by_year = defaultdict(list)
        for r in rows:
            by_year[r["entry_date"][:4]].append(r)
        result["markets"][mkt] = {
            "run_stamp": stamp, "universe": len(part.get("names") or {}), "fetched": len(data), "failed": len(part.get("failed") or []),
            "universe_meta": part.get("universe_meta"), "bars_median": med,
            "base": rates(rows), "by_year": {y: rates(v) for y, v in sorted(by_year.items())},
            "by_feature": by_feature(rows),
        }
        all_rows.extend(rows)
        print(f"[{mkt}] raced={len(rows)} base={json.dumps(result['markets'][mkt]['base'], default=str)} {time.time() - t0:.0f}s", flush=True)

    result["stats"] = dict(stats)
    result["elapsed_s"] = round(time.time() - t0)
    result["rows"] = all_rows
    json.dump(result, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({"stats": result["stats"], **{m: {"base": v["base"], "by_year": v["by_year"]} for m, v in result["markets"].items()}},
                     ensure_ascii=False, indent=1, default=str), flush=True)
    for m, v in result["markets"].items():
        print(f"\n[{m}] 특징별 (사분위 1→4 reach_rate / loss10_rate, n)")
        for f, b in v["by_feature"].items():
            if "quartiles" in b:
                qs = b["quartiles"]
                print(f"  {f:24s} " + " | ".join(f"Q{q} {qs[q].get('reach_rate', 0):.2f}/{qs[q].get('loss10_rate', 0):.2f} n{qs[q]['n']}" for q in (1, 2, 3, 4))
                      + f"  Δreach {b['q4_minus_q1']['reach_rate']:+.2f} z {b['q4_minus_q1']['z_reach_prop']}")
            else:
                print(f"  {f:24s} " + " | ".join(f"{k} {x.get('reach_rate', 0):.2f}/{x.get('loss10_rate', 0):.2f} n{x['n']}" for k, x in b.items()))
    print(f"[done] {OUT}", flush=True)


if __name__ == "__main__":
    main()
