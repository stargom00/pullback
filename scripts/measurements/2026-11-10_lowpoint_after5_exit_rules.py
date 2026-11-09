"""저점 주봉 신호 → 다음 첫 시가 매수 → +5% 도달 이후 매도 규칙 비교(R0 전량 +5% vs R1~R4).

사전등록: docs/lowpoint_after5_exit_rules.md §1·§1-구현 — 조건·문턱·판정식을 결과 보고 바꾸지 않는다.
재사용(사본 금지): 10-09 저점 측정 모듈의 load_data(naver 통합 일봉, 1900일)·liquid(저유동성)·next_valid,
                   harness.CleanView(정제·룩어헤드 차단), lowpoint(주봉 리샘플·RSI·신호·evaluate).
실행 시각: harness.check_run_window(("kr",)).
실행: MEAS_CACHE=<경로.pkl> python3 scripts/measurements/2026-11-10_lowpoint_after5_exit_rules.py
      (파일명 날짜는 실행일로 바꾼다 — README 규칙2. 결과 JSON은 커밋하지 않는다 — 규칙5)
"""
import importlib.util
import json
import math
import os
import random
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("lp_1009", os.path.join(HERE, "2026-10-09_lowpoint_departure_rest_entry.py"))
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)
harness, lp = base.harness, base.lp

KST = timezone(timedelta(hours=9))
TF = "week"
# ── 사전등록 값(docs/lowpoint_after5_exit_rules.md §1) ──
GAP_SKIP = 0.05          # 매수 시가 ≥ 기준가×1.05면 제외(채널 조건 3)
TARGET = 0.05            # +5%
HOLD_MAX = 250           # 최대 보유 거래일(AI 어림값)
DEDUP_DAYS = 28          # 같은 종목 4주 안 신호 제외(AI 어림값)
MA_N = 20
Z_MIN = 2.50             # Bonferroni 4비교
N_MIN = 100
RULES = ("R1", "R2", "R3", "R4")
SELF_CHECK_N = 300
SEED = 20261110
OUT = os.path.join(HERE, os.path.basename(__file__).replace(".py", ".results.json"))


def weekly_hits(close: pd.Series, now: datetime):
    """현재 정의(lp.lowpoint_signal, v5.375) — [(label, bar_date, c0)]. 진행 중 주는 버린다."""
    bars = lp.drop_in_progress(lp.resample_bars(close, TF), "kr", now)
    m0 = max(lp.MIN_BARS[TF], 3)
    if len(bars) < m0:
        return []
    r = lp.rsi_wilder_sma(bars["close"], lp.RSI_PERIOD)
    c = bars["close"].astype(float)
    out = []
    for i in range(m0 - 1, len(bars)):
        r1, r2 = float(r.iloc[i - 1]), float(r.iloc[i - 2])
        if r1 != r1 or r2 != r2:
            continue
        if lp.lowpoint_signal(float(c.iloc[i]), float(c.iloc[i - 1]), r1, r2):
            out.append((bars.index[i], bars["bar_date"].iloc[i], float(c.iloc[i])))
    return out


def self_check_hits(data, now, rng, stats):
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


def exit_next_open(path, k):
    """k봉 종가에서 조건 성립 → k+1봉 시가(없으면 k봉 종가)."""
    if k + 1 < len(path):
        return float(path["Open"].iloc[k + 1]), str(path.index[k + 1].date())
    return float(path["Close"].iloc[k]), str(path.index[k].date())


def simulate(buy, path, ma):
    """path = 매수일부터 HOLD_MAX봉(정제). ma = path와 같은 인덱스의 20일선. 반환: dict 또는 None(+5% 미도달)."""
    T = buy * (1 + TARGET)
    hit = next((k for k in range(len(path)) if float(path["High"].iloc[k]) >= T), None)
    last = len(path) - 1
    if hit is None:
        lows = path["Low"].astype(float)
        half = next((k for k in range(len(path)) if lows.iloc[k] <= buy * 0.5), None)
        rec = None if half is None else next((k for k in range(half + 1, len(path)) if float(path["High"].iloc[k]) >= buy * 0.75), None)
        return {"touched": False, "half_hit": half is not None, "half_recovered": rec is not None,
                "end_ret": (float(path["Close"].iloc[last]) / buy - 1) * 100}
    fill = max(float(path["Open"].iloc[hit]), T)
    r0 = (fill / buy - 1) * 100
    c, close_ret = path["Close"].astype(float), None

    def first(cond):
        for k in range(hit, len(path)):
            if cond(k):
                px, d = exit_next_open(path, k)
                return (px / buy - 1) * 100, d
        return (float(c.iloc[last]) / buy - 1) * 100, str(path.index[last].date()) + "(cap)"

    below_ma = lambda k: ma.iloc[k] == ma.iloc[k] and c.iloc[k] < ma.iloc[k]
    r1_rest, d1 = first(lambda k: c.iloc[k] < buy or below_ma(k))
    r2, d2 = first(below_ma)
    # R3: 주봉(W-FRI) — 마감일이 도달일 이후인 주부터 "주 종가 < 직전 주 저가" → 다음 주 첫 시가
    hit_date = path.index[hit]
    wk = path.assign(_d=path.index).resample("W-FRI").agg({"Low": "min", "Close": "last", "_d": "last"}).dropna()
    r3, d3 = None, None
    for i in range(1, len(wk)):
        if wk["_d"].iloc[i] < hit_date:
            continue
        if float(wk["Close"].iloc[i]) < float(wk["Low"].iloc[i - 1]):
            k = path.index.get_loc(wk["_d"].iloc[i])
            px, d3 = exit_next_open(path, k)
            r3 = (px / buy - 1) * 100
            break
    if r3 is None:
        r3, d3 = (float(c.iloc[last]) / buy - 1) * 100, str(path.index[last].date()) + "(cap)"
    return {"touched": True, "touch_date": str(hit_date.date()), "days_to_touch": hit, "R0": r0,
            "R1": 0.5 * r0 + 0.5 * r1_rest, "R2": r2, "R3": r3, "R4": 0.5 * r0 + 0.5 * r3,
            "exit_dates": {"R1_rest": d1, "R2": d2, "R3": d3}}


def zstat(d):
    n = len(d)
    if n < 2:
        return None
    m = sum(d) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in d) / (n - 1))
    return None if sd == 0 else m / (sd / math.sqrt(n))


def pct(v, q):
    s = sorted(v)
    return s[min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))] if s else None


def main():
    run_window = harness.check_run_window(("kr",))
    t0 = time.time()
    now = datetime.now(KST)
    blob = base.load_data()
    data = blob["data"]
    stamp = harness.run_stamp(data)
    bars_n = sorted(len(df) for df in data.values())
    med = bars_n[len(bars_n) // 2] if bars_n else 0
    assert med >= base.MIN_MEDIAN_BARS, f"fetch 깊이 부족: 중앙값 {med}봉"
    rng, stats = random.Random(SEED), Counter()
    self_check_hits(data, now, rng, stats)

    # 히트 + stale 주 제외(10-09와 같은 규칙)
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
            else:
                hits.append(h)
    stats["hits"] = len(hits)
    assert hits, "히트 0건"

    # 중복 제외 → 매수 → 경로
    trades, last_kept, views = [], {}, {}
    for h in sorted(hits, key=lambda x: (x["ticker"], x["base_date"])):
        t = h["ticker"]
        if t in last_kept and (h["base_date"] - last_kept[t]).days < DEDUP_DAYS:
            stats["dedup_skip"] += 1
            continue
        last_kept[t] = h["base_date"]
        df = data[t]
        view = views.get(t) or views.setdefault(t, harness.CleanView(df))
        if h["base_date"] not in df.index:
            stats["no_base_bar"] += 1
            continue
        jb = df.index.get_loc(h["base_date"])
        if not view.valid(jb):
            stats["base_invalid"] += 1
            continue
        pre = view.prefix(jb)
        if not base.liquid(pre):
            stats["illiquid"] += 1
            continue
        fut = view.future(jb)
        assert len(fut) == 0 or fut.index[0] > pre.index[-1], "future lookahead"
        if len(fut) < HOLD_MAX:
            stats["insufficient_hold_window"] += 1
            continue
        path = fut.iloc[:HOLD_MAX]
        buy = float(path["Open"].iloc[0])
        if not buy > 0:
            stats["bad_open"] += 1
            continue
        if buy >= h["base_price"] * (1 + GAP_SKIP):
            stats["gap_skip"] += 1
            continue
        closes = pd.concat([pre["Close"], path["Close"]]).astype(float)
        ma = closes.rolling(MA_N).mean().loc[path.index]
        sim = simulate(buy, path, ma)
        trades.append({"ticker": t, "label": str(h["label"].date()), "base_date": str(h["base_date"].date()),
                       "base_price": h["base_price"], "buy_date": str(path.index[0].date()), "buy": buy, **sim})
    touched = [x for x in trades if x["touched"]]
    untouched = [x for x in trades if not x["touched"]]
    stats["trades"], stats["touched"], stats["untouched"] = len(trades), len(touched), len(untouched)
    assert len(touched) >= 2, "+5% 도달 표본 부족"

    split = sorted(x["touch_date"] for x in touched)[len(touched) // 2]
    verdict = {}
    for rk in RULES:
        d = [x[rk] - x["R0"] for x in touched]
        early = [x[rk] - x["R0"] for x in touched if x["touch_date"] < split]
        late = [x[rk] - x["R0"] for x in touched if x["touch_date"] >= split]
        z = zstat(d)
        checks = {"z_ge_min": z is not None and z >= Z_MIN,
                  "halves_positive": bool(early) and bool(late) and sum(early) / len(early) > 0 and sum(late) / len(late) > 0,
                  "n_ge_min": len(d) >= N_MIN}
        verdict[rk] = {"n": len(d), "mean_diff_pp": sum(d) / len(d), "z": z,
                       "early_mean_diff_pp": sum(early) / len(early) if early else None,
                       "late_mean_diff_pp": sum(late) / len(late) if late else None,
                       "checks": checks, "adopted": all(checks.values())}
    record = {rk: {"mean": sum(x[rk] for x in touched) / len(touched), "median": pct([x[rk] for x in touched], 0.5),
                   "p5": pct([x[rk] for x in touched], 0.05), "p95": pct([x[rk] for x in touched], 0.95)}
              for rk in ("R0",) + RULES}
    record["days_to_touch_median"] = pct([x["days_to_touch"] for x in touched], 0.5)
    record["untouched"] = {"n": len(untouched),
                           "half_hit_rate": sum(x["half_hit"] for x in untouched) / len(untouched) if untouched else None,
                           "half_recovered_rate_of_half_hit": (sum(x["half_recovered"] for x in untouched if x["half_hit"])
                                                               / max(1, sum(x["half_hit"] for x in untouched))),
                           "end_ret_median": pct([x["end_ret"] for x in untouched], 0.5)}
    result = {"run_stamp": stamp, "run_window": run_window, "fetched_at": blob.get("fetched_at"),
              "universe": len(blob.get("names") or {}), "fetched": len(data), "bars_median": med,
              "elapsed_s": round(time.time() - t0),
              "params": {"GAP_SKIP": GAP_SKIP, "TARGET": TARGET, "HOLD_MAX": HOLD_MAX, "DEDUP_DAYS": DEDUP_DAYS,
                         "MA_N": MA_N, "Z_MIN": Z_MIN, "N_MIN": N_MIN, "SEED": SEED},
              "stats": dict(stats), "half_split_touch_date": split, "verdict": verdict, "record_only": record,
              "trades": trades}
    json.dump(result, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: result[k] for k in ("stats", "half_split_touch_date", "verdict", "record_only")},
                     ensure_ascii=False, indent=1, default=str), flush=True)
    print(f"[done] {OUT}", flush=True)


if __name__ == "__main__":
    main()
