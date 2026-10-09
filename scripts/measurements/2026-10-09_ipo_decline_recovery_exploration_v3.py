"""상장 후 하락 종목 — 탐색 3차: 분할 매수 vs 1회 진입 vs 유지 확인(판정 없음).

문서: docs/ipo_decline_recovery_exploration.md §4. 2차 스크립트를 import(시작점 `start_index`·신호 배열 `signal_series_v2`·`events`),
2차는 1차(`forward`·유니버스 헬퍼·`pattern`)를 import한다. 확인군은 열지 않는다. ⚠️ 생존편향(상폐 종목 없음)은 1·2차와 같다. 손절 없음.

방식(종목당 1회, 매수 = 결정일 다음 날 시가):
  A 1회        시작점 다음 날 전액.
  B 시간 분할  시작점·+63·+126·+189봉에 각 1/4(데이터가 모자라면 산 만큼만).
  C 가격 분할  시작점 1/4, 이후 종가 ≤ 직전 매수가(체결 시가) × 0.85인 날마다 1/4, 최대 4회, 시작점 +1,000봉 안.
  D 전환       시작점 이후 첫 20>50 교차(2차 (c)) 다음 날 전액.
  E 전환+유지  20>50 교차 후 20봉 연속 MA20 > MA50(교차일 = 1봉째, 2~21봉째 모두 참)이면 21봉째 다음 날 전액. 깨지면 다음 교차부터.
  F 99+유지    99일선 종가 돌파(2차 (b)) 후 20봉 연속 종가 > MA99면 21봉째 다음 날 전액.
실행: MEAS_CACHE=<3,000일 캐시.pkl> python3 scripts/measurements/2026-10-09_ipo_decline_recovery_exploration_v3.py
"""
import importlib.util
import json
import os
import time
from collections import Counter

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("ipo_v2", os.path.join(HERE, "2026-10-09_ipo_decline_recovery_exploration_v2.py"))
v2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v2)
v1, harness, prev = v2.v1, v2.harness, v2.prev

TIME_STEPS = (0, 63, 126, 189)     # "시작점, +63봉, +126봉, +189봉에 각 1/4"
PRICE_DROP = 0.85                  # "종가가 직전 매수가 × 0.85 이하"
PRICE_MAX_BUYS = 4
PRICE_WINDOW = 1000                # "1,000봉 안에 안 되면 산 만큼만"
HOLD_CONFIRM = 20                  # "유지 봉 수(20)는 이번 한 값만"
HORIZONS = (250, 500)
REACH = (0.5, 1.0)                 # "+50%·+100% 도달 비율"
METHODS = ("A", "B", "C", "D", "E", "F")
OUT = os.path.join(HERE, os.path.basename(__file__).replace(".py", ".results.json"))


def _ma(c, n):
    return pd.Series(c).rolling(n).mean().values


def hold_confirm_days(cross, cond, start: int) -> list:
    """교차(cross[i_c]) 뒤 cond가 i_c+1 ~ i_c+20 모두 참인 교차마다 결정일 i_c+20(= 교차일을 1봉째로 센 21봉째). start 이후 교차만, 시간순.
    i_c+20의 판정은 i_c+20까지의 데이터만 쓴다."""
    out = []
    n = len(cond)
    for i_c in np.flatnonzero(cross):
        i_c = int(i_c)
        if i_c < start or i_c + HOLD_CONFIRM >= n:
            continue
        if bool(np.all(cond[i_c + 1:i_c + HOLD_CONFIRM + 1])):
            out.append(i_c + HOLD_CONFIRM)
    return out


def decisions(s: pd.DataFrame, s0: int, liquid) -> dict:
    """방식별 결정일 목록(매수 = 결정일 다음 봉 시가). liquid(i) = 그날까지 데이터로 저유동성 아님.
    저유동성(AI 판단): A는 그날이면 미진입, B는 그 회차 건너뜀, C는 다음 조건일을 기다림, D·E·F는 다음 신호로."""
    c = s["Close"].astype(float).values
    o = s["Open"].astype(float).values
    n = len(c)
    out = {k: [] for k in METHODS}
    if s0 + 1 < n and liquid(s0):
        out["A"] = [s0]
    for st in TIME_STEPS:
        d = s0 + st
        if d + 1 < n and liquid(d):
            out["B"].append(d)
    if s0 + 1 < n and liquid(s0):
        out["C"] = [s0]
        last_fill, last_px = s0 + 1, o[s0 + 1]
        j = last_fill
        while len(out["C"]) < PRICE_MAX_BUYS and j + 1 < n and j <= s0 + PRICE_WINDOW:
            if c[j] <= last_px * PRICE_DROP and liquid(j):
                out["C"].append(j)
                last_fill, last_px = j + 1, o[j + 1]
                j = last_fill
                continue
            j += 1
    sig = v2.signal_series_v2(s)
    ma20, ma50, ma99 = _ma(c, 20), _ma(c, 50), _ma(c, 99)
    cond_e = np.nan_to_num(ma20 > ma50, nan=False) & ~np.isnan(ma50)
    cond_f = np.nan_to_num(c > ma99, nan=False) & ~np.isnan(ma99)
    cands = {"D": [int(i) for i in np.flatnonzero(sig["c_ma20_50_cross"]) if i >= s0],
             "E": hold_confirm_days(sig["c_ma20_50_cross"], cond_e, s0),
             "F": hold_confirm_days(sig["b_ma99_cross"], cond_f, s0)}
    for k, ds in cands.items():
        for d in ds:
            if d + 1 < n and liquid(d):
                out[k] = [d]
                break
    return out


def valuation(s: pd.DataFrame, decs: list, amount: float) -> dict:
    """매수 금액 가중 평가: 회차마다 amount를 결정일 다음 봉 시가에 산다. T봉 평가 = Σ(amount/체결가)·종가[T] ÷ Σ(T까지 체결된 amount) − 1."""
    if not decs:
        return {"entered": False}
    c = s["Close"].astype(float).values
    o = s["Open"].astype(float).values
    n = len(c)
    fills = [(d + 1, float(o[d + 1])) for d in decs]
    f0 = fills[0][0]

    def ret(T):
        inv = sum(amount for f, _ in fills if f <= T)
        sh = sum(amount / px for f, px in fills if f <= T)
        return sh * c[T] / inv - 1
    out = {"entered": True, "n_fills": len(fills), "first_fill": f0, "last_fill": fills[-1][0],
           "bars_to_complete": fills[-1][0] - f0, "avg_cost": len(fills) * amount / sum(amount / px for _, px in fills)}
    for h in HORIZONS:
        T = f0 + h - 1
        out[f"r{h}"] = ret(T) * 100 if T < n else None
    end = min(f0 + HORIZONS[-1] - 1, n - 1)
    path = [ret(T) for T in range(f0, end + 1)]
    out["max_drawdown"] = min(path) * 100
    full = f0 + HORIZONS[-1] - 1 < n
    out["full_500"] = full
    for r in REACH:
        out[f"reach_{int(r * 100)}"] = (max(path) >= r) if full else None
    return out


def main():
    run_window = harness.check_run_window(v1.RUN_MARKETS)
    t0 = time.time()
    kind = v1.kind_listing()
    blob = v1.line.load_data()
    data = blob["data"]
    stamp = harness.run_stamp(data)
    stats = Counter()
    cands = {t: i for t, i in kind.items() if i["listed"] >= v1.LIST_FROM and not v1.excluded_kind(i["name"]) and t in data}
    explore = sorted(t for t in cands if v1.is_explore(t))
    confirm = set(t for t in cands if not v1.is_explore(t))
    stats["explore"] = len(explore)
    used, rows = set(), []
    hold_stats = {"E": Counter(), "F": Counter()}
    occ = {"E": [], "F": []}
    for t in explore:
        s = harness.CleanView(data[t]).full
        if not len(s) or str(s.index[0].date()) != cands[t]["listed"]:
            stats["first_bar_not_listing_day"] += 1
            continue
        stats["explore_used"] += 1
        used.add(t)
        c = s["Close"].astype(float).values
        s0 = v2.start_index(c)
        if s0 is None:
            stats["no_start"] += 1
            continue
        stats["with_start"] += 1
        trough = v1.SKIP_FIRST + int(np.argmin(c[v1.SKIP_FIRST:]))
        liq_cache = {}

        def liquid(i):
            if i not in liq_cache:
                liq_cache[i] = bool(prev.liquid(s.iloc[:i + 1]))
            return liq_cache[i]
        decs = decisions(s, s0, liquid)
        row = {"ticker": t, "s0": s0, "trough": trough, "trough_close": float(c[trough])}
        for k in METHODS:
            amount = 0.25 if k in ("B", "C") else 1.0
            val = valuation(s, decs[k], amount)
            if val["entered"]:
                val["avg_cost_vs_trough_pct"] = (val["avg_cost"] / c[trough] - 1) * 100
            row[k] = val
        rows.append(row)
        # E·F: 유지 확인이 거른 교차, 그리고 확인된 신호를 발생마다(2차 방식) 센 가짜 신호율
        sig = v2.signal_series_v2(s)
        ma20, ma50, ma99 = _ma(c, 20), _ma(c, 50), _ma(c, 99)
        for k, cross, cond in (("E", sig["c_ma20_50_cross"], np.nan_to_num(ma20 > ma50, nan=False) & ~np.isnan(ma50)),
                               ("F", sig["b_ma99_cross"], np.nan_to_num(c > ma99, nan=False) & ~np.isnan(ma99))):
            conf = set(hold_confirm_days(cross, cond, s0))
            for i_c in np.flatnonzero(cross):
                i_c = int(i_c)
                if i_c < s0:
                    continue
                if i_c + HOLD_CONFIRM >= len(c):
                    hold_stats[k]["undecided_data_end"] += 1
                    continue
                if i_c + HOLD_CONFIRM in conf:
                    hold_stats[k]["passed"] += 1
                else:
                    hold_stats[k]["filtered"] += 1
                    hold_stats[k]["filtered_before_trough"] += int(i_c < trough)
            arr = np.zeros(len(c), bool)
            for d in conf:
                arr[d] = True
            st2 = Counter()
            for r in v2.events(s, {**{x: np.zeros(len(c), bool) for x in v2.SIGNALS}, "a_pre_base": arr}, s0, st2)["a_pre_base"]:
                occ[k].append({"ticker": t, **r, "real": r["i"] >= trough})
    assert not (used & confirm), "확인군 종목이 집계에 들어갔다"
    for k, v in v2.EXPECT_V1.items():
        assert stats[k] == v, f"1·2차와 유니버스가 다르다: {k}"

    def q(xs):
        return v1._q(xs)
    tab = {}
    for k in METHODS:
        ent = [r[k] for r in rows if r[k]["entered"]]
        full = [x for x in ent if x["full_500"]]
        tab[k] = {"n_stocks": len(rows), "n_entered": len(ent), "no_entry_rate": 1 - len(ent) / len(rows) if rows else None,
                  "r250": q([x["r250"] for x in ent]), "r500": q([x["r500"] for x in ent]),
                  "bars_to_complete": q([x["bars_to_complete"] for x in ent]) if k in ("B", "C") else None,
                  "fills_dist": dict(Counter(x["n_fills"] for x in ent)) if k in ("B", "C") else None,
                  "avg_cost_vs_trough_pct": q([x["avg_cost_vs_trough_pct"] for x in ent]),
                  "max_drawdown": q([x["max_drawdown"] for x in ent]),
                  "n_full_500": len(full), "reach_50": sum(x["reach_50"] for x in full) / len(full) if full else None,
                  "reach_100": sum(x["reach_100"] for x in full) / len(full) if full else None}
    inter = [r for r in rows if all(r[k]["entered"] and r[k]["r250"] is not None for k in METHODS)]
    paired = {"n": len(inter), **{k: q([r[k]["r250"] for r in inter]) for k in METHODS}}
    fake = {}
    for k in ("E", "F"):
        xs = occ[k]
        fh = Counter(x["first_hit"] for x in xs)
        hs = hold_stats[k]
        fake[k] = {"hold": dict(hs), "filtered_before_trough_share": hs["filtered_before_trough"] / hs["filtered"] if hs["filtered"] else None,
                   "occurrences": len(xs), "fake_rate_down20_first": fh["down20"] / len(xs) if xs else None,
                   "up30": fh["up30"], "down20": fh["down20"], "neither": fh["neither"],
                   "r250": q([x["r250"] for x in xs]), "share_before_trough": sum(1 for x in xs if not x["real"]) / len(xs) if xs else None}
    result = {"run_stamp": stamp, "run_window": run_window, "elapsed_s": round(time.time() - t0),
              "params": {"TIME_STEPS": TIME_STEPS, "PRICE_DROP": PRICE_DROP, "PRICE_MAX_BUYS": PRICE_MAX_BUYS, "PRICE_WINDOW": PRICE_WINDOW,
                         "HOLD_CONFIRM": HOLD_CONFIRM, "HORIZONS": HORIZONS},
              "stats": dict(stats), "methods": tab, "paired_r250": paired, "hold_confirm_fake": fake, "rows": rows}
    json.dump(result, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False, indent=1, default=str), flush=True)
    print(f"[done] {OUT}", flush=True)


if __name__ == "__main__":
    main()
