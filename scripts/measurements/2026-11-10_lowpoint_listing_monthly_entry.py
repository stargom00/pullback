"""탐색(판정 없음) — 상장 1년 뒤 · 상장가 대비 −50% 이하 후보 → 월봉 음봉이면 대기 → 첫 월봉 양봉 다음 달 시가 진입.
+ 바닥 달의 공통 특징(바닥 추측 신호) 탐색.

사전등록: docs/lowpoint_listing_monthly_entry.md §1 — 결과 보고 정의를 바꾸지 않는다.
재사용: 상장 후 바닥 탐색 모듈(valid_bars·part_a 정의) → 시스템 측정 모듈(system_sim2·dist) → 11-10(load_data 등).
실행 시각: harness.check_run_window(("kr",)).
실행: MEAS_CACHE=/tmp/lp_1900.pkl python3 scripts/measurements/2026-11-10_lowpoint_listing_monthly_entry.py
      (파일명 날짜는 실행일 — KST — 로 바꾼다. 결과 JSON은 커밋하지 않는다)
"""
import importlib.util
import json
import math
import os
import time
from collections import Counter
from datetime import datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("lp_turn", os.path.join(HERE, "2026-11-10_lowpoint_post_listing_turn.py"))
turn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(turn)
sysm, m, base, harness, lp = turn.sysm, turn.m, turn.base, turn.harness, turn.lp

# ── 사전등록 값(docs/lowpoint_listing_monthly_entry.md §1) ──
START_AFTER_MONTHS = 12      # 상장 후 12개월째 월봉부터 관찰(사용자 "상장 후1년부터")
DRAWDOWN = 0.5               # 월봉 종가 ≤ 상장가 × 0.5면 후보(사용자 "하락폭이 -50% 이상")
NEED_BEAR_FIRST = True       # 후보가 된 뒤 음봉을 1개 이상 거친 다음의 첫 양봉(사용자 "음봉이면 더 지켜보고 양봉이 나온 뒤 진입")
HOLD_MAX = m.HOLD_MAX        # 250거래일
OUT = os.path.join(HERE, os.path.basename(__file__).replace(".py", ".results.json"))


def monthly(df, data_end):
    """월봉(달력 월). data_end가 속한 달은 진행 중이라 버린다."""
    g = df.resample("ME")
    mo = pd.DataFrame({"o": g["Open"].first(), "h": g["High"].max(), "l": g["Low"].min(), "c": g["Close"].last(),
                       "v": g["Volume"].sum(), "first_day": g["Open"].apply(lambda s: s.index[0] if len(s) else pd.NaT)}).dropna()
    mo = mo[mo.index.to_period("M") < pd.Timestamp(data_end).to_period("M")]
    mo["rsi"] = lp.rsi_wilder_sma(mo["c"].astype(float), lp.RSI_PERIOD).values
    return mo


def features(mo, i, listing_px):
    """월봉 i의 특징 — 그 달까지의 정보만(바닥 추측 신호 후보)."""
    r = mo.iloc[i]
    rng = r.h - r.l
    bear_run = 0
    for k in range(i - 1, -1, -1):
        if mo["c"].iloc[k] < mo["o"].iloc[k]:
            bear_run += 1
        else:
            break
    v12 = mo["v"].iloc[max(0, i - 12):i].mean() if i > 0 else float("nan")
    return {"months_since_listing": i, "dd_vs_listing": r.c / listing_px - 1,
            "rsi": None if r.rsi != r.rsi else float(r.rsi), "bear_run_before": bear_run,
            "vol_ratio_12m": float(r.v / v12) if v12 and v12 == v12 and v12 > 0 else None,
            "lower_wick": float((min(r.o, r.c) - r.l) / rng) if rng > 0 else None,
            "close_pos": float((r.c - r.l) / rng) if rng > 0 else None,
            "bull": bool(r.c > r.o), "ret_month": float(r.c / r.o - 1)}


def smd(a, b):
    a = [x for x in a if x is not None and x == x]
    b = [x for x in b if x is not None and x == x]
    if len(a) < 2 or len(b) < 2:
        return None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    sd = math.sqrt((va + vb) / 2)
    return {"bottom_mean": ma, "other_mean": mb, "std_diff": (ma - mb) / sd if sd > 0 else None, "n_bottom": len(a), "n_other": len(b)}


def main():
    run_window = harness.check_run_window(("kr",))
    t0 = time.time()
    now = datetime.now(m.KST)
    blob = base.load_data()
    data = blob["data"]
    stamp = harness.run_stamp(data)
    stats = Counter()
    window_start = pd.Timestamp(now.date()) - pd.Timedelta(days=base.KR_DAYS)
    data_end = max(df.index[-1] for df in data.values())
    a_rows, _ = turn.part_a(data, window_start, data_end, stats)        # 상장 후 2년+ 이력 종목과 실제 바닥(사후)

    entries, bottom_feats, other_feats = [], [], []
    for t, a in a_rows.items():
        df = turn.valid_bars(data[t])
        mo = monthly(df, data_end)
        lpx = a["listing_price"]
        bottom_month = pd.Timestamp(a["bottom_date"]).to_period("M")
        # 바닥 추측 신호 탐색 — 상장 12개월 이후 & 후보 조건을 만족한 달들 중 바닥 달 vs 나머지
        for i in range(START_AFTER_MONTHS, len(mo)):
            if mo["c"].iloc[i] > lpx * DRAWDOWN:
                continue
            f = features(mo, i, lpx)
            (bottom_feats if mo.index[i].to_period("M") == bottom_month else other_feats).append(f)
        # 진입 규칙: 후보 → (음봉 1개 이상) → 첫 양봉 → 다음 달 첫 거래일 시가
        cand, saw_bear, entry_i = False, False, None
        for i in range(START_AFTER_MONTHS, len(mo) - 1):
            if not cand and mo["c"].iloc[i] <= lpx * DRAWDOWN:
                cand = True
            if not cand:
                continue
            bull = mo["c"].iloc[i] > mo["o"].iloc[i]
            if not bull:
                saw_bear = True
                continue
            if saw_bear or not NEED_BEAR_FIRST:
                entry_i = i + 1
                break
        if entry_i is None:
            stats["no_entry"] += 1
            continue
        d0 = mo["first_day"].iloc[entry_i]
        path = df[df.index >= d0].iloc[:HOLD_MAX]
        if len(path) < HOLD_MAX:
            stats["entry_insufficient_hold"] += 1
            continue
        buy = float(path["Open"].iloc[0])
        sysr = sysm.system_sim2(buy, path)
        after = path["Close"].astype(float)
        entries.append({"ticker": t, "entry_date": str(d0.date()), "buy": buy, "buy_vs_listing": buy / lpx - 1,
                        "months_since_listing": entry_i, "signal_month": str(mo.index[entry_i - 1].date()),
                        "entry_after_bottom_hindsight": d0 > pd.Timestamp(a["bottom_date"]),
                        "buy_vs_bottom": buy / a["bottom"] - 1, "system": sysr,
                        "max_ret_250": float(path["High"].max()) / buy - 1, "min_ret_250": float(path["Low"].min()) / buy - 1,
                        "ret_250_close": float(after.iloc[-1]) / buy - 1})
    stats["entries"] = len(entries)
    keys = ["months_since_listing", "dd_vs_listing", "rsi", "bear_run_before", "vol_ratio_12m", "lower_wick", "close_pos", "ret_month"]
    bottom_signal = {k: smd([f[k] for f in bottom_feats], [f[k] for f in other_feats]) for k in keys}
    bottom_signal["bull_rate"] = {"bottom": sum(f["bull"] for f in bottom_feats) / len(bottom_feats) if bottom_feats else None,
                                  "other": sum(f["bull"] for f in other_feats) / len(other_feats) if other_feats else None}
    e = entries
    summary = {
        "n": len(e),
        "system": sysm.dist([x["system"]["ret"] for x in e]),
        "target_rate": sum(x["system"]["exit"] == "target" for x in e) / len(e) if e else None,
        "days_to_exit": sysm.dist([x["system"]["days"] for x in e]),
        "max_ret_250": sysm.dist([x["max_ret_250"] * 100 for x in e]),
        "min_ret_250": sysm.dist([x["min_ret_250"] * 100 for x in e]),
        "ret_250_close": sysm.dist([x["ret_250_close"] * 100 for x in e]),
        "after_bottom_rate_hindsight": sum(x["entry_after_bottom_hindsight"] for x in e) / len(e) if e else None,
        "buy_vs_bottom_pct": sysm.dist([x["buy_vs_bottom"] * 100 for x in e]),
        "months_since_listing": sysm.dist([x["months_since_listing"] for x in e]),
    }
    result = {"run_stamp": stamp, "run_window": run_window, "elapsed_s": round(time.time() - t0),
              "params": {"START_AFTER_MONTHS": START_AFTER_MONTHS, "DRAWDOWN": DRAWDOWN, "NEED_BEAR_FIRST": NEED_BEAR_FIRST,
                         "HOLD_MAX": HOLD_MAX},
              "stats": dict(stats), "entry_summary": summary, "bottom_signal_exploration": bottom_signal,
              "n_bottom_months": len(bottom_feats), "n_other_candidate_months": len(other_feats), "entries": entries}
    json.dump(result, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: result[k] for k in ("stats", "entry_summary", "bottom_signal_exploration", "n_bottom_months",
                                             "n_other_candidate_months")}, ensure_ascii=False, indent=1, default=str), flush=True)
    print(f"[done] {OUT}", flush=True)


if __name__ == "__main__":
    main()
