"""저점에서 50일선 첫 돌파 — H1 측정만 · H2 탐색(판정 없음).

사전등록: docs/lowpoint_ma50_turn.md §1 (커밋 7a3bbd5) — 결과를 본 뒤 정의를 바꾸지 않는다.
실행: MEAS_CACHE=<경로.pkl> python3 scripts/measurements/2026-11-14_lowpoint_ma50_turn.py
"""
import json
import math
import os
import pickle
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts", "screens"))
import harness  # noqa: E402
import lowpoint  # noqa: E402
import naver_kr  # noqa: E402

# ── 사전등록 값(§1) ──
DATA_DAYS = 3000
DD = 0.60            # 120봉 최저 저가 ≤ 250봉 최고 고가 × 0.60 (사용자 선택 −40%)
BASE_WIN, BASE_MIN = 60, 40   # 직전 60봉 중 종가 < SMA50 ≥ 40 (사용자 선택)
MIN_HIST = 250
TURNOVER_MIN = 5e8   # 20일 평균 거래대금 ≥ 5억(AI 어림값)
GAP_BARS = 60        # 같은 종목 사건 간격(AI 어림값)
UP, DN, HOLD = 1.30, 0.85, 120
N_MIN = 100
Z_FLAG = 2.94        # 양측 0.05 Bonferroni 15
OUT = os.path.join(HERE, os.path.basename(__file__).replace(".py", ".results.json"))


def load_data():
    cache = os.environ.get("MEAS_CACHE")
    if cache and os.path.exists(cache):
        return pickle.load(open(cache, "rb"))
    uni, meta = naver_kr.fetch_top_turnover_v2(top_n=6000)
    names = dict(uni)
    data, failed = {}, []

    def one(t):
        try:
            df = naver_kr.fetch_history(t, days=DATA_DAYS)
            return t, (None if df is None or df.empty else df[["Open", "High", "Low", "Close", "Volume"]])
        except Exception:
            return t, None
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = [ex.submit(one, t) for t in names]
        for i, f in enumerate(as_completed(futs), 1):
            t, df = f.result()
            (failed.append(t) if df is None else data.__setitem__(t, df))
            if i % 500 == 0:
                print(f"[fetch] {i}/{len(names)} {time.time() - t0:.0f}s", flush=True)
    blob = {"data": data, "names": names, "failed": sorted(failed), "universe_meta": meta,
            "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S %Z")}
    if cache:
        pickle.dump(blob, open(cache, "wb"))
    return blob


def indicators(df: pd.DataFrame) -> pd.DataFrame:
    """정제된 df(그 시점까지)에 지표 열을 붙인다 — 각 값은 그 봉까지의 정보만 쓴다."""
    c, h, l, o, v = (df[k].astype(float) for k in ("Close", "High", "Low", "Open", "Volume"))
    x = pd.DataFrame(index=df.index)
    x["c"], x["h"], x["l"], x["o"], x["v"] = c, h, l, o, v
    x["ma20"] = c.rolling(20).mean()
    x["ma50"] = c.rolling(50).mean()
    x["ma200"] = c.rolling(200).mean()
    x["low120"] = l.rolling(120).min()
    x["high250"] = h.rolling(250).max()
    x["below"] = (c < x["ma50"]).astype(float)
    x["below60"] = x["below"].shift(1).rolling(BASE_WIN).sum()          # j 직전 60봉
    x["turn20"] = (c * v).rolling(20).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    x["atr20"] = tr.rolling(20).mean()
    x["vol50"] = v.shift(1).rolling(50).mean()
    x["rsi"] = lowpoint.rsi_wilder_sma(c, 14).values
    x["cross"] = (c > x["ma50"]) & (c.shift() <= x["ma50"].shift())
    return x


def is_event(x: pd.DataFrame, k: int) -> bool:
    r = x.iloc[k]
    return (k >= MIN_HIST and bool(r.cross) and r.low120 <= r.high250 * DD and r.below60 >= BASE_MIN
            and r.turn20 >= TURNOVER_MIN)


def is_control(x: pd.DataFrame, k: int) -> bool:
    r = x.iloc[k]
    return (k >= MIN_HIST and r.c <= r.ma50 and r.low120 <= r.high250 * DD and r.below60 >= BASE_MIN
            and r.turn20 >= TURNOVER_MIN)


def features(x: pd.DataFrame, k: int) -> dict:
    r = x.iloc[k]
    win = x.iloc[k - 119:k + 1]
    bot_pos = int(np.argmin(win["l"].values))           # 바닥 = 120봉 최저 저가
    bot_k = k - 119 + bot_pos
    bottom = float(win["l"].iloc[bot_pos])
    # F10 직전 120봉의 돌파 실패: 돌파 뒤 5봉 안에 종가가 다시 SMA50 아래
    fails = 0
    for i in range(k - 120, k):
        if x["cross"].iloc[i] and any(x["c"].iloc[i + d] < x["ma50"].iloc[i + d] for d in range(1, 6) if i + d < k + 1):
            fails += 1
    pre = x["v"].iloc[max(0, bot_k - 60):bot_k]
    rng = r.h - r.l
    return {
        "F1": float(r.v / r.vol50) if r.vol50 > 0 else None,
        "F2": float(r.c / r.o - 1) if r.o > 0 else None,
        "F3": float((r.c - r.l) / rng) if rng > 0 else None,
        "F4": float(r.o / x["c"].iloc[k - 1] - 1),
        "F5": float(r.ma50 / x["ma50"].iloc[k - 10] - 1),
        "F6": float(r.c / r.ma200 - 1) if r.ma200 == r.ma200 else None,
        "F7": int(k - bot_k),
        "F8": float(r.c / bottom - 1),
        "F9": float(bottom / r.high250 - 1),
        "F10": fails,
        "F11": float(x["l"].iloc[k - 19:k + 1].min() / bottom - 1),
        "F12": float(r.atr20 / x["atr20"].iloc[k - 60]) if x["atr20"].iloc[k - 60] > 0 else None,
        "F13": float(x["v"].iloc[k - 19:k + 1].mean() / pre.mean()) if len(pre) >= 20 and pre.mean() > 0 else None,
        "F14": None if r.rsi != r.rsi else float(r.rsi),
        "F15": int(r.ma20 > r.ma50),
    }


def race(fut: pd.DataFrame) -> dict | None:
    """진입 = 첫 봉 시가. +30% 고가 먼저 = 성공, 종가 −15% 먼저 = 실패(같은 날이면 실패)."""
    if len(fut) == 0 or not fut["Open"].iloc[0] > 0:
        return None
    e = float(fut["Open"].iloc[0])
    f = fut.iloc[:HOLD]
    out, day = "open", None
    for i in range(len(f)):
        hit_dn = f["Close"].iloc[i] <= e * DN
        hit_up = f["High"].iloc[i] >= e * UP
        if hit_dn:
            out, day = "fail", i
            break
        if hit_up:
            out, day = "win", i
            break
    ret = lambda n: float(f["Close"].iloc[n - 1] / e - 1) if len(f) >= n else None
    return {"entry": e, "outcome": out, "day": day, "complete": len(f) >= HOLD or out != "open",
            "r20": ret(20), "r60": ret(60), "r120": ret(120),
            "mfe": float(f["High"].max() / e - 1), "mae": float(f["Low"].min() / e - 1)}


def scan(t, view: harness.CleanView, stats: Counter):
    full = view.full
    if len(full) < MIN_HIST + 2:
        return [], []
    xf = indicators(full)
    ev, ct = [], []
    last_e = last_c = -10 ** 9
    for k in range(MIN_HIST, len(xf) - 1):
        d = xf.index[k]
        e_ok = is_event(xf, k)
        c_ok = (not e_ok) and is_control(xf, k)
        if not (e_ok or c_ok):
            continue
        if (e_ok and k - last_e < GAP_BARS) or (c_ok and k - last_c < GAP_BARS):
            continue
        j = view.raw.index.get_loc(d)
        if view.dirty:                     # 그 시점 정제로 다시 확인(룩어헤드 방어)
            stats["dirty_recheck"] += 1
            xp = indicators(view.prefix(j))
            kp = len(xp) - 1
            if xp.index[kp] != d or not (is_event(xp, kp) if e_ok else is_control(xp, kp)):
                stats["dirty_drop"] += 1
                continue
            x, kk = xp, kp
        else:
            x, kk = xf, k
        res = race(view.future(j))
        if res is None:
            stats["no_future"] += 1
            continue
        row = {"t": t, "date": str(d.date()), **res}
        if e_ok:
            row.update(features(x, kk))
            ev.append(row)
            last_e = k
        else:
            ct.append(row)
            last_c = k
    return ev, ct


def rates(rows):
    """데이터 끝에 걸려 120일을 다 못 본 미판정 건(complete=False)은 뺀다(1-부록)."""
    rows = [r for r in rows if r["complete"]]
    n = len(rows)
    w = sum(r["outcome"] == "win" for r in rows)
    f = sum(r["outcome"] == "fail" for r in rows)
    o = n - w - f
    dec = w + f
    med = lambda k: (float(np.median([r[k] for r in rows if r[k] is not None])) if any(r[k] is not None for r in rows) else None)
    return {"n": n, "win": w, "fail": f, "open": o, "win_rate_decided": w / dec if dec else None,
            "win_rate_all": w / n if n else None, "fail_rate_all": f / n if n else None,
            "r20_med": med("r20"), "r60_med": med("r60"), "r120_med": med("r120"),
            "mfe_med": med("mfe"), "mae_med": med("mae"),
            "days_to_win_med": float(np.median([r["day"] for r in rows if r["outcome"] == "win"])) if w else None}


def prop_z(a, b):
    """두 비율 z(win_rate_all) — b − a."""
    n1, n2 = a["n"], b["n"]
    p1, p2 = a["win"] / n1, b["win"] / n2
    p = (a["win"] + b["win"]) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return (p2 - p1) / se if se > 0 else None


def feat_table(rows):
    rows = [r for r in rows if r["complete"]]
    keys = [f"F{i}" for i in range(1, 16)]
    wins = [r for r in rows if r["outcome"] == "win"]
    fails = [r for r in rows if r["outcome"] == "fail"]
    if len(wins) < N_MIN or len(fails) < N_MIN:
        raise SystemExit(f"표본 부족: 성공 {len(wins)} 실패 {len(fails)} (각 {N_MIN} 이상 필요)")
    dates = sorted(r["date"] for r in wins + fails)
    split = dates[len(dates) // 2]
    out = {}
    for k in keys:
        a = pd.Series([r[k] for r in fails], dtype=float)
        b = pd.Series([r[k] for r in wins], dtype=float)
        if a.dropna().empty or b.dropna().empty:
            raise SystemExit(f"{k}: 값이 전부 비었다 — 특징 계산 결함")
        z, _ = harness.mannwhitney_zscore(a, b)
        halves = {}
        for nm, sel in (("early", lambda r: r["date"] < split), ("late", lambda r: r["date"] >= split)):
            za, _ = harness.mannwhitney_zscore(pd.Series([r[k] for r in fails if sel(r)], dtype=float),
                                               pd.Series([r[k] for r in wins if sel(r)], dtype=float))
            halves[nm] = za
        sd = math.sqrt((a.var() + b.var()) / 2)
        # 사분위별 성공률(성공+실패만)
        both = [(r[k], r["outcome"] == "win") for r in wins + fails if r[k] is not None and r[k] == r[k]]
        qs = np.quantile([v for v, _ in both], [0.25, 0.5, 0.75])
        buck = [[], [], [], []]
        for v, w in both:
            buck[int(np.searchsorted(qs, v, side="right"))].append(w)
        out[k] = {"win_median": float(b.median()), "fail_median": float(a.median()),
                  "win_mean": float(b.mean()), "fail_mean": float(a.mean()),
                  "smd": float((b.mean() - a.mean()) / sd) if sd > 0 else None,
                  "mwu_z": z, "half_z": halves,
                  "flag": bool(z is not None and abs(z) >= Z_FLAG and all(h is not None and (h > 0) == (z > 0) for h in halves.values())),
                  "quartile_cut": [float(q) for q in qs],
                  "quartile_win_rate": [sum(x) / len(x) if x else None for x in buck],
                  "quartile_n": [len(x) for x in buck]}
    return out, split


def main():
    run_window = harness.check_run_window(("kr",))
    t0 = time.time()
    blob = load_data()
    data = blob["data"]
    stamp = harness.run_stamp(data)
    print(f"[stamp] {stamp}", flush=True)
    stats = Counter()
    ev, ct = [], []
    for i, (t, df) in enumerate(sorted(data.items()), 1):
        v = harness.CleanView(df)
        a, b = scan(t, v, stats)
        ev += a
        ct += b
        if i % 500 == 0:
            print(f"[scan] {i}/{len(data)} ev={len(ev)} ct={len(ct)} {time.time() - t0:.0f}s", flush=True)
    re_, rc = rates(ev), rates(ct)
    ft, split = feat_table(ev)
    wins = [r for r in ev if r["outcome"] == "win"]
    wait = {"F7_days_from_bottom_med_all": float(np.median([r["F7"] for r in ev])),
            "F8_gain_from_bottom_med_all": float(np.median([r["F8"] for r in ev])),
            "F8_gain_from_bottom_med_win": float(np.median([r["F8"] for r in wins]))}
    res = {"run_stamp": stamp, "run_window": run_window, "fetched_at": blob.get("fetched_at"),
           "universe": len(blob["names"]), "fetched": len(data), "failed": len(blob["failed"]),
           "elapsed_s": round(time.time() - t0), "stats": dict(stats),
           "params": {"DD": DD, "BASE": [BASE_WIN, BASE_MIN], "TURNOVER_MIN": TURNOVER_MIN, "GAP_BARS": GAP_BARS,
                      "UP": UP, "DN": DN, "HOLD": HOLD, "Z_FLAG": Z_FLAG, "DATA_DAYS": DATA_DAYS},
           "H1": {"incomplete": {"event": sum(not r["complete"] for r in ev), "control": sum(not r["complete"] for r in ct)}, "event": re_, "control": rc, "win_rate_z_event_minus_control": prop_z(rc, re_), "waiting": wait},
           "H2": {"split_date": split, "features": ft},
           "years": dict(Counter(r["date"][:4] for r in ev)),
           "events": ev}
    json.dump(res, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: res[k] for k in ("H1", "years", "stats")}, ensure_ascii=False, indent=1, default=str))
    for k, v in ft.items():
        print(k, "win", round(v["win_median"], 3), "fail", round(v["fail_median"], 3), "z", v["mwu_z"] and round(v["mwu_z"], 2),
              "halves", {a: b and round(b, 2) for a, b in v["half_z"].items()}, "flag", v["flag"],
              "Q", [x and round(x, 2) for x in v["quartile_win_rate"]])


if __name__ == "__main__":
    main()
