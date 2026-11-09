"""탐색(판정 없음) — A: 상장 후 바닥·상승 전환 시점 / B: 상장 1년 뒤 신호 + 큰 상승 없음 그룹의 +5% 이후 보유 규칙.

사전등록: docs/lowpoint_post_listing_turn.md §1 — 결과 보고 정의를 바꾸지 않는다. 같은 데이터의 세 번째 분할이라 전부 기록만.
재사용: 11-10 시스템 측정 모듈(dist·compare·system_sim2) → 11-10 매도 규칙 모듈(weekly_hits·simulate·self_check) → 10-09(load_data·liquid).
실행 시각: harness.check_run_window(("kr",)).
실행: MEAS_CACHE=/tmp/lp_1900.pkl python3 scripts/measurements/2026-11-10_lowpoint_post_listing_turn.py
      (파일명 날짜는 실행일로 바꾼다. 결과 JSON은 커밋하지 않는다)
"""
import importlib.util
import json
import os
import random
import time
from collections import Counter, defaultdict
from datetime import datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("lp_sys", os.path.join(HERE, "2026-11-10_lowpoint_system_expectancy_listing.py"))
sysm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sysm)
m, base, harness, lp = sysm.m, sysm.base, sysm.harness, sysm.lp

# ── 사전등록 값(docs/lowpoint_post_listing_turn.md §1) ──
LISTED_MARGIN_DAYS = 30     # 데이터 시작이 조회 창 시작 +30일 이후 = 창 안 상장
MIN_HISTORY_DAYS = 730      # 상장 후 2년 이상 데이터(사용자 "응")
TURN_MULT = 1.5             # 상승 전환 = 바닥 이후 첫 종가 ≥ 바닥 × 1.5(사용자 "응")
SIGNAL_AFTER_DAYS = 365     # B: 상장 1년 뒤 신호만(사용자 "상장하고 1년 후부터")
BIG_RISE = 2.0              # B: 신호 기준일까지 최고 종가 < 상장가 × 2.0(11-10 측정과 같음)
SEED = 20261110
OUT = os.path.join(HERE, os.path.basename(__file__).replace(".py", ".results.json"))


def valid_bars(df):
    """A용 — 상장 이력을 지우지 않게 정제(_filter_invalid_bars 갈래B는 장기 정지 이전을 통째로 버려 상장 정보를 잃는다)
    대신 OHLC 네 값이 모두 양수인 봉만 쓴다(무거래일 OHLC=0 오염 제거와 같은 효과)."""
    ok = (df[["Open", "High", "Low", "Close"]] > 0).all(axis=1)
    return df[ok]


def months(a, b):
    return (pd.Timestamp(b) - pd.Timestamp(a)).days / 30.44


def part_a(data, window_start, data_end, stats):
    rows = {}
    for t, raw in data.items():
        if raw.index[0] <= window_start + pd.Timedelta(days=LISTED_MARGIN_DAYS):
            continue                                     # 창 시작에서 잘린 이력 — 상장일 모름
        df = valid_bars(raw)
        if len(df) < 2:
            continue
        listing_date, listing_px = df.index[0], float(df["Open"].iloc[0])
        if (data_end - listing_date).days < MIN_HISTORY_DAYS:
            stats["a_short_history"] += 1
            continue
        c = df["Close"].astype(float)
        bottom_date = c.idxmin()
        bottom = float(c.loc[bottom_date])
        after = c[c.index > bottom_date]
        turn = after[after >= bottom * TURN_MULT]
        rec = after[after >= listing_px]
        rows[t] = {"listing_date": str(listing_date.date()), "listing_price": listing_px,
                   "bottom_date": str(bottom_date.date()), "bottom": bottom, "bottom_vs_listing_pct": (bottom / listing_px - 1) * 100,
                   "months_to_bottom": months(listing_date, bottom_date),
                   "turn_date": str(turn.index[0].date()) if len(turn) else None,
                   "months_bottom_to_turn": months(bottom_date, turn.index[0]) if len(turn) else None,
                   "recovered_listing": bool(len(rec)), "max_close_vs_listing": float(c.max()) / listing_px,
                   "bottom_is_last_60d": (data_end - bottom_date).days <= 60}
    stats["a_stocks"] = len(rows)
    v = list(rows.values())
    mb = [x["months_to_bottom"] for x in v]
    summary = {
        "n": len(v),
        "months_to_bottom": sysm.dist(mb),
        "bottom_bucket": {"<12m": sum(x < 12 for x in mb), "12-24m": sum(12 <= x < 24 for x in mb), ">=24m": sum(x >= 24 for x in mb)},
        "bottom_vs_listing_pct": sysm.dist([x["bottom_vs_listing_pct"] for x in v]),
        "turned_rate": sum(x["turn_date"] is not None for x in v) / len(v) if v else None,
        "months_bottom_to_turn": sysm.dist([x["months_bottom_to_turn"] for x in v if x["months_bottom_to_turn"] is not None]),
        "recovered_listing_rate": sum(x["recovered_listing"] for x in v) / len(v) if v else None,
        "bottom_in_last_60d_rate": sum(x["bottom_is_last_60d"] for x in v) / len(v) if v else None,
        "ever_doubled_listing_rate": sum(x["max_close_vs_listing"] >= 2 for x in v) / len(v) if v else None,
    }
    return rows, summary


def build_trades(data, now, stats):
    """11-10 측정과 같은 신호·워밍업·stale·중복·저유동성·250거래일·갭 규칙(그 스크립트 main의 같은 절차)."""
    window_start = pd.Timestamp(now.date()) - pd.Timedelta(days=base.KR_DAYS)
    hits_by_label = defaultdict(list)
    for t, df in data.items():
        truncated = df.index[0] <= window_start + pd.Timedelta(days=LISTED_MARGIN_DAYS)
        wk_idx = lp.resample_bars(df["Close"], m.TF).index
        for label, bar_date, c0 in m.weekly_hits(df["Close"], now):
            if truncated and wk_idx.get_loc(label) < m.WARMUP_WEEKS:
                stats["hit_warmup_skip"] += 1
                continue
            hits_by_label[label].append({"ticker": t, "label": label, "base_date": pd.Timestamp(bar_date), "base_price": c0})
    week_mode = {}
    for t, df in data.items():
        for lbl, bd in lp.resample_bars(df["Close"], m.TF)["bar_date"].items():
            week_mode.setdefault(lbl, Counter())[pd.Timestamp(bd)] += 1
    hits = []
    for label, hs in hits_by_label.items():
        mode = week_mode[label].most_common(1)[0][0]
        hits += [h for h in hs if h["base_date"] >= mode]
    trades, last_kept, views = [], {}, {}
    for h in sorted(hits, key=lambda x: (x["ticker"], x["base_date"])):
        t = h["ticker"]
        if t in last_kept and (h["base_date"] - last_kept[t]).days < m.DEDUP_DAYS:
            continue
        last_kept[t] = h["base_date"]
        df = data[t]
        view = views.get(t) or views.setdefault(t, harness.CleanView(df))
        if h["base_date"] not in df.index:
            continue
        jb = df.index.get_loc(h["base_date"])
        if not view.valid(jb):
            continue
        pre = view.prefix(jb)
        if not base.liquid(pre):
            continue
        fut = view.future(jb)
        assert len(fut) == 0 or fut.index[0] > pre.index[-1], "future lookahead"
        if len(fut) < m.HOLD_MAX:
            continue
        path = fut.iloc[:m.HOLD_MAX]
        buy = float(path["Open"].iloc[0])
        if not buy > 0 or buy >= h["base_price"] * (1 + m.GAP_SKIP):
            continue
        closes = pd.concat([pre["Close"], path["Close"]]).astype(float)
        ma = closes.rolling(m.MA_N).mean().loc[path.index]
        trades.append({"ticker": t, "base_date": h["base_date"], "pre_max_close": float(pre["Close"].max()),
                       **m.simulate(buy, path, ma), "system": sysm.system_sim2(buy, path)})
    stats["trades"] = len(trades)
    return trades


def main():
    run_window = harness.check_run_window(("kr",))
    t0 = time.time()
    now = datetime.now(m.KST)
    blob = base.load_data()
    data = blob["data"]
    stamp = harness.run_stamp(data)
    rng, stats = random.Random(SEED), Counter()
    m.self_check_hits(data, now, rng, stats)
    window_start = pd.Timestamp(now.date()) - pd.Timedelta(days=base.KR_DAYS)
    data_end = max(df.index[-1] for df in data.values())

    a_rows, a_summary = part_a(data, window_start, data_end, stats)

    trades = build_trades(data, now, stats)
    grp = []
    for x in trades:
        a = a_rows.get(x["ticker"])
        if not a:
            continue
        if (x["base_date"] - pd.Timestamp(a["listing_date"])).days < SIGNAL_AFTER_DAYS:
            continue
        if x["pre_max_close"] >= a["listing_price"] * BIG_RISE:
            continue
        x["after_bottom_hindsight"] = x["base_date"] >= pd.Timestamp(a["bottom_date"])   # 사후 분류(미래 정보) — 기록만
        grp.append(x)
    stats["b_group_trades"] = len(grp)
    touched = [x for x in grp if x["touched"]]
    b = {"group_compare": sysm.compare(touched, "b"),
         "after_bottom_compare": sysm.compare([x for x in touched if x["after_bottom_hindsight"]], "after"),
         "before_bottom_compare": sysm.compare([x for x in touched if not x["after_bottom_hindsight"]], "before"),
         "system_group": sysm.dist([x["system"]["ret"] for x in grp]),
         "system_after_bottom": sysm.dist([x["system"]["ret"] for x in grp if x["after_bottom_hindsight"]]),
         "system_before_bottom": sysm.dist([x["system"]["ret"] for x in grp if not x["after_bottom_hindsight"]])}
    result = {"run_stamp": stamp, "run_window": run_window, "fetched_at": blob.get("fetched_at"), "elapsed_s": round(time.time() - t0),
              "params": {"LISTED_MARGIN_DAYS": LISTED_MARGIN_DAYS, "MIN_HISTORY_DAYS": MIN_HISTORY_DAYS, "TURN_MULT": TURN_MULT,
                         "SIGNAL_AFTER_DAYS": SIGNAL_AFTER_DAYS, "BIG_RISE": BIG_RISE, "SEED": SEED},
              "stats": dict(stats), "A_summary": a_summary, "B_exploration": b, "A_rows": a_rows,
              "B_trades": [{k: v for k, v in x.items()} for x in grp]}
    json.dump(result, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: result[k] for k in ("stats", "A_summary", "B_exploration")}, ensure_ascii=False, indent=1, default=str), flush=True)
    print(f"[done] {OUT}", flush=True)


if __name__ == "__main__":
    main()
