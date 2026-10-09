"""상장 후 하락 종목 회복 패턴 — 탐색 2차(정의 변경, 판정 없음).

문서: docs/ipo_decline_recovery_exploration.md §3. 1차(`2026-10-09_ipo_decline_recovery_exploration.py`, 24fe098)는 그대로 두고 그 함수를 import한다
(패턴·서술 1 항목·신호 (b)~(e)·진입 뒤 지표·유니버스 헬퍼). 확인군은 열지 않는다. ⚠️ 생존편향(상폐 종목 없음)은 1차와 같다.

바뀐 것(사용자 지시 2026-10-09):
- 시작점(당시 정보): 21번째 봉 이후 종가 ≤ "그때까지의 최고 종가"(21번째 봉부터 그날까지) × 0.6이 된 첫날. 그날부터 "하락 구간".
- 신호는 하락 구간 안에서 **나올 때마다** 센다. 같은 신호로 진입해 250봉 보유 중(진입봉 ~ 진입봉+249)이면 그 신호의 새 발생은 무시(신호별 독립 포지션).
- (a) 미리 = 최근 60봉 종가가 모두 [60봉 최저 종가, × 1.15] 안 **그리고** 그 60봉 최저 ≤ 그때까지 최고 × 0.5 — 조건이 처음 참이 된 날(전날 거짓 → 그날 참).
- 서술 2 추가: 신호가 사후 저점 전(가짜)/후(진짜) 비율, 진짜만의 수익률(실현 불가 참고), "−20% 먼저" 비율 = 가짜 신호율.
- 서술 1: 회복군 vs 아직 안 간 군(미회복 + 보류). 보류군 저점 이후 봉 수 분포. 회복군에서 +50% 전 (b)~(e) 첫 발생 순서 상위 5개.
실행: MEAS_CACHE=<3,000일 캐시.pkl> python3 scripts/measurements/2026-10-09_ipo_decline_recovery_exploration_v2.py
"""
import importlib.util
import json
import os
import time
from collections import Counter

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("ipo_v1", os.path.join(HERE, "2026-10-09_ipo_decline_recovery_exploration.py"))
v1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v1)
harness, prev = v1.harness, v1.prev

START_DROP = 0.6                  # "그때까지의 최고 종가 대비 −40% 이하"
HOLD = 250                        # "같은 신호로 진입해 250봉 보유 중이면 새 신호는 무시"
SIGNALS = v1.SIGNALS              # (a)~(e) 이름 그대로
TURN = ("b_ma99_cross", "c_ma20_50_cross", "d_rebound_high", "e_ma200_cross")
EXPECT_V1 = {"explore": 367, "explore_used": 312}   # 1차와 같은 유니버스인지 확인(다르면 하드 실패)
OUT = os.path.join(HERE, os.path.basename(__file__).replace(".py", ".results.json"))


def start_index(c) -> "int | None":
    """하락 구간 시작: i ≥ 20에서 c[i] ≤ max(c[20..i]) × 0.6인 첫 i(그날까지 데이터만)."""
    run = None
    for i in range(v1.SKIP_FIRST, len(c)):
        run = c[i] if run is None else max(run, c[i])
        if c[i] <= run * START_DROP:
            return i
    return None


def signal_series_v2(s: pd.DataFrame) -> dict:
    """(b)~(e) = 1차 signal_series 그대로, (a) = 2차 정의(조건이 처음 참이 된 날). 모두 그 봉까지의 데이터만."""
    out = v1.signal_series(s)
    c = s["Close"].astype(float).values
    a = np.zeros(len(c), bool)
    run = None
    cond_prev = False
    for i in range(v1.SKIP_FIRST, len(c)):
        run = c[i] if run is None else max(run, c[i])
        cond = False
        if i >= v1.SKIP_FIRST + v1.PRE_A_WIN - 1:
            w = c[i - v1.PRE_A_WIN + 1:i + 1]
            lo = w.min()
            cond = bool(w.max() <= lo * v1.PRE_A_BAND and lo <= run * (1 - v1.PRE_A_DROP))
        a[i] = cond and not cond_prev
        cond_prev = cond
    out["a_pre_base"] = a
    return out


def events(s: pd.DataFrame, sig: dict, s0: int, stats: Counter) -> dict:
    """신호별 진입 목록. 하락 구간(s0 이후) 안 발생마다, 그 신호의 보유 중(진입봉 ~ +249)이면 무시. 저유동성은 진입 안 함(포지션도 안 연다)."""
    out = {}
    for k in SIGNALS:
        rows, hold_end = [], -1
        for i in np.flatnonzero(sig[k]):
            i = int(i)
            if i < s0:
                continue
            stats[f"{k}_raw"] += 1
            if i + 1 <= hold_end:
                stats[f"{k}_ignored_holding"] += 1
                continue
            if not prev.liquid(s.iloc[:i + 1]):
                stats[f"{k}_illiquid"] += 1
                continue
            f = v1.forward(s, i)
            if f.get("no_next_bar"):
                continue
            rows.append({"i": i, "date": str(s.index[i].date()), **f})
            hold_end = i + 1 + HOLD - 1
        out[k] = rows
    return out


def _tab(xs):
    fh = Counter(x["first_hit"] for x in xs)
    n = len(xs)
    return {"n": n, "tickers": len({x["ticker"] for x in xs}),
            **{f"r{h}": v1._q([x[f"r{h}"] for x in xs]) for h in v1.HORIZONS},
            "max_up_250": v1._q([x["max_up_250"] for x in xs if x["full_250"]]),
            "max_dn_250": v1._q([x["max_dn_250"] for x in xs if x["full_250"]]),
            "up30": fh["up30"], "down20": fh["down20"], "neither": fh["neither"],
            "fake_rate_down20_first": fh["down20"] / n if n else None}


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

    used, sig_rows, desc_rows, orders = set(), {k: [] for k in SIGNALS}, [], Counter()
    best = []
    for t in explore:
        s = harness.CleanView(data[t]).full
        if not len(s) or str(s.index[0].date()) != cands[t]["listed"]:
            stats["first_bar_not_listing_day"] += 1
            continue
        stats["explore_used"] += 1
        used.add(t)
        c = s["Close"].astype(float).values
        trough = v1.SKIP_FIRST + int(np.argmin(c[v1.SKIP_FIRST:])) if len(c) > v1.SKIP_FIRST else None
        # 서술 1(1차 정의 그대로, 그룹만 회복 vs 아직)
        pt = v1.pattern(s)
        if pt and pt.get("declined"):
            g = "recovered" if pt["group"] == "recovered" else "not_yet"
            row = {"ticker": t, "group": g, "group_v1": pt["group"], "post_trough_bars": len(c) - 1 - pt["t"], **v1.describe(s, pt)}
            desc_rows.append(row)
            if g == "recovered":
                sig = signal_series_v2(s)
                firsts = []
                for k in TURN:
                    i = v1._first(sig[k], pt["t"] + 1)
                    if i is not None and i <= pt["rec"]:
                        firsts.append((i, k[0]))
                order = []
                for d in sorted(set(i for i, _ in firsts)):
                    order.append("=".join(sorted(k for i, k in firsts if i == d)))
                orders[" → ".join(order) if order else "(없음)"] += 1
        # 서술 2(2차 정의)
        s0 = start_index(c)
        if s0 is None:
            stats["no_start"] += 1
            continue
        stats["with_start"] += 1
        sig = signal_series_v2(s)
        ev = events(s, sig, s0, stats)
        for k, rows in ev.items():
            for r in rows:
                sig_rows[k].append({"ticker": t, **r, "real": trough is not None and r["i"] >= trough})
        if trough is not None:
            f = v1.forward(s, trough)
            if not f.get("no_next_bar"):
                best.append({"ticker": t, **f})
    assert not (used & confirm), "확인군 종목이 집계에 들어갔다"
    for k, v in EXPECT_V1.items():
        assert stats[k] == v, f"1차와 유니버스가 다르다: {k} {stats[k]} != {v}"

    sig_tab = {}
    for k in SIGNALS:
        xs = sig_rows[k]
        real = [x for x in xs if x["real"]]
        sig_tab[k] = {"all": _tab(xs), "share_before_trough": (len(xs) - len(real)) / len(xs) if xs else None,
                      "n_before_trough": len(xs) - len(real), "n_after_trough": len(real), "real_only_unrealizable": _tab(real)}
    sig_tab["best_trough"] = {"all": _tab(best)}
    keys = ["drop_pct", "decline_bars", "pre60_vol_ratio", "band_days", "above_ma20", "above_ma50", "above_ma99", "above_ma200",
            "ma20_50_cross", "rebound_high_break", "first_bull15", "vol_spike2x", "abc_first_A", "to_recovery"]
    desc = {g: {k: v1._q([r.get(k) for r in desc_rows if r["group"] == g]) for k in keys} for g in ("recovered", "not_yet")}
    desc["not_yet_post_trough_bars"] = v1._q([r["post_trough_bars"] for r in desc_rows if r["group"] == "not_yet"])
    desc["group_counts"] = dict(Counter(r["group_v1"] for r in desc_rows))
    result = {"run_stamp": stamp, "run_window": run_window, "elapsed_s": round(time.time() - t0),
              "params": {"START_DROP": START_DROP, "HOLD": HOLD, "PRE_A_WIN": v1.PRE_A_WIN, "PRE_A_BAND": v1.PRE_A_BAND,
                         "PRE_A_DROP": v1.PRE_A_DROP},
              "stats": dict(stats), "describe": desc, "turn_order_top": orders.most_common(5), "turn_order_n": sum(orders.values()),
              "signals": sig_tab, "signal_rows": sig_rows}
    json.dump(result, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: v for k, v in result.items() if k != "signal_rows"}, ensure_ascii=False, indent=1, default=str), flush=True)
    print(f"[done] {OUT}", flush=True)


if __name__ == "__main__":
    main()
