"""저점 신호 뒤 1년 안 +100% 특징 — 탐색(발견 절반) + 확인(다른 절반). 사전등록: docs/lowpoint_double_features.md §1.
실행: MEAS_CACHE=<pkl> python3 scripts/measurements/2026-11-24_lowpoint_double_features.py <system_expectancy.results.json>
"""
import hashlib
import json
import math
import os
import pickle
import statistics
import sys
from collections import Counter

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(ROOT, "scripts", "screens"))
import harness  # noqa: E402
import lowpoint as lp  # noqa: E402
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("k5", os.path.join(HERE, "2026-11-15_lowpoint_kosdaq_ma5_filter.py"))
k5 = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(k5)
prop_z = k5.prop_z

H, LISTED_MIN, MAX_PICK, Z_MIN, NQ_MIN = 250, 250, 3, 2.39, 50
OUT = os.path.join(HERE, os.path.basename(__file__).replace(".py", ".results.json"))


def feats(p, jb):
    c = p["Close"].to_numpy(float); h = p["High"].to_numpy(float); l = p["Low"].to_numpy(float); v = p["Volume"].to_numpy(float)
    s = lambda a, n: a[jb - n + 1:jb + 1]
    def sma(n, off=0):
        e = jb - off
        return c[e - n + 1:e + 1].mean() if e + 1 >= n else math.nan
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    atr = tr[jb - 14:jb].mean() if jb >= 15 else math.nan
    hi250 = s(h, 250); lo250c = s(c, 250)
    up = c[jb - 49:jb + 1] > c[jb - 50:jb]; vv = v[jb - 49:jb + 1]
    dn_v = vv[~up].sum()
    rsi = lp.rsi_wilder_sma(p["Close"].iloc[:jb + 1]).iloc[-1]
    return {
        "F1_dd_1y": c[jb] / hi250.max() - 1,
        "F2_dd_3y": (c[jb] / c[jb - 749:jb + 1].max() - 1) if jb >= 749 else math.nan,
        "F3_bars_since_1y_high": 249 - int(np.argmax(hi250)),
        "F4_ma200_gap": c[jb] / sma(200) - 1,
        "F5_ma200_slope": sma(200) / sma(200, 20) - 1,
        "F6_atr_pct": atr / c[jb],
        "F7_range60": s(c, 60).max() / s(c, 60).min() - 1,
        "F8_vol20_120": s(v, 20).mean() / max(s(v, 120).mean(), 1),
        "F9_turnover20_log": math.log10(max((s(c, 20) * s(v, 20)).mean(), 1)),
        "F10_higher_low": s(c, 60).min() / c[jb - 119:jb - 59].min() - 1,
        "F11_rsi_d": float(rsi),
        "F12_price_log": math.log10(c[jb]),
        "F13_ma600_gap": (c[jb] / sma(600) - 1) if jb >= 599 else math.nan,
        "F14_bars_since_1y_low": 249 - int(np.argmin(lo250c[::-1][::-1])) if False else 249 - int(len(lo250c) - 1 - np.argmin(lo250c[::-1])),
        "F15_ret20": c[jb] / c[jb - 20] - 1,
        "F16_updown_vol50": (vv[up].sum() / dn_v) if dn_v > 0 else math.nan,
    }


def quart_cuts(vals):
    return list(np.nanpercentile(vals, [25, 50, 75]))


def qidx(x, cuts):
    return int(sum(x > k for k in cuts))


def main():
    run_window = harness.check_run_window(("kr",))
    trades = json.load(open(sys.argv[1]))["trades"]
    data = pickle.load(open(os.environ["MEAS_CACHE"], "rb"))["data"]
    stats, rows, views = Counter(), [], {}
    for x in trades:
        v = views.get(x["ticker"]) or views.setdefault(x["ticker"], harness.CleanView(data[x["ticker"]]))
        if v.dirty:
            stats["dirty_skip"] += 1; continue
        p = v.full
        jb = p.index.get_loc(pd.Timestamp(x["buy_date"])) - 1
        if jb + 1 < LISTED_MIN:
            stats["listed_lt_1y"] += 1; continue
        path = p.iloc[jb + 1:jb + 1 + H]
        if len(path) < H:
            stats["future_short"] += 1; continue
        buy = float(path["Open"].iloc[0])
        assert abs(buy - x["buy"]) / x["buy"] < 1e-3, "매수가 불일치"
        hit = np.nonzero(path["High"].to_numpy(float) >= 2 * buy)[0]
        f = feats(p, jb)
        # 기록용: 4거래일 뒤 수익과 5번째 거래일 시가 매수
        c = p["Close"].to_numpy(float)
        r4 = c[jb + 4] / c[jb] - 1
        path5 = p.iloc[jb + 5:jb + 5 + H]
        b5 = float(path5["Open"].iloc[0]) if len(path5) else math.nan
        hit5 = bool(len(path5) == H and (path5["High"].to_numpy(float) >= 2 * b5).any())
        half = "disc" if int(hashlib.md5(x["ticker"].encode()).hexdigest(), 16) % 2 == 0 else "conf"
        rows.append({"ticker": x["ticker"], "base_date": str(p.index[jb].date()), "half": half, "ok": bool(len(hit)),
                     "days": int(hit[0]) + 1 if len(hit) else None, "ret_end": float(path["Close"].iloc[-1]) / buy - 1,
                     "r4": r4, "ok5": hit5 if len(path5) == H else None, **f})
    assert rows, "표본 0건"
    disc = [r for r in rows if r["half"] == "disc"]; conf = [r for r in rows if r["half"] == "conf"]
    fk = [k for k in rows[0] if k.startswith("F")]
    base = {"all": sum(r["ok"] for r in rows) / len(rows), "n": len(rows), "n_disc": len(disc), "n_conf": len(conf),
            "days_median": statistics.median([r["days"] for r in rows if r["ok"]]) if any(r["ok"] for r in rows) else None}
    explore = {}
    for k in fk:
        vals = [r[k] for r in disc if r[k] == r[k]]
        cuts = quart_cuts(vals)
        q = [[r for r in disc if r[k] == r[k] and qidx(r[k], cuts) == i] for i in range(4)]
        rates = [sum(r["ok"] for r in g) / len(g) if g else None for g in q]
        explore[k] = {"cuts": cuts, "n": [len(g) for g in q], "rate": rates,
                      "diff_q4_q1": (rates[3] - rates[0]) if rates[0] is not None and rates[3] is not None else None}
    picks = sorted([k for k in fk if explore[k]["diff_q4_q1"] is not None], key=lambda k: -abs(explore[k]["diff_q4_q1"]))[:MAX_PICK]
    confirm = {}
    if conf:
        split = sorted(r["base_date"] for r in conf)[len(conf) // 2]
    for k in picks:
        cuts = explore[k]["cuts"]; good = 3 if explore[k]["diff_q4_q1"] > 0 else 0; bad = 3 - good
        G = [r for r in conf if r[k] == r[k] and qidx(r[k], cuts) == good]; B = [r for r in conf if r[k] == r[k] and qidx(r[k], cuts) == bad]
        z = prop_z(sum(r["ok"] for r in G), len(G), sum(r["ok"] for r in B), len(B)) if G and B else None
        hv = {}
        for nm, sel in (("early", lambda r: r["base_date"] < split), ("late", lambda r: r["base_date"] >= split)):
            g = [r for r in G if sel(r)]; b = [r for r in B if sel(r)]
            hv[nm] = (sum(r["ok"] for r in g) / len(g) - sum(r["ok"] for r in b) / len(b)) if g and b else None
        passed = bool(z is not None and z >= Z_MIN and len(G) >= NQ_MIN and len(B) >= NQ_MIN and all(d is not None and d > 0 for d in hv.values()))
        confirm[k] = {"good_q": good + 1, "bad_q": bad + 1, "n_good": len(G), "n_bad": len(B),
                      "rate_good": sum(r["ok"] for r in G) / len(G) if G else None, "rate_bad": sum(r["ok"] for r in B) / len(B) if B else None,
                      "z": z, "halves": hv, "verdict": "확인" if passed else "발견만"}
    r4v = [r for r in rows if r["ok5"] is not None]
    cuts4 = quart_cuts([r["r4"] for r in r4v])
    post4 = {"cuts": cuts4, "quartiles": []}
    for i in range(4):
        g = [r for r in r4v if qidx(r["r4"], cuts4) == i]
        post4["quartiles"].append({"n": len(g), "rate_day5_entry": sum(r["ok5"] for r in g) / len(g) if g else None,
                                   "rate_signal_entry": sum(r["ok"] for r in g) / len(g) if g else None})
    res = {"run_window": run_window, "run_stamp": harness.run_stamp(data), "stats": dict(stats), "base": base,
           "explore": explore, "picks": picks, "confirm": confirm, "post4": post4}
    json.dump(res, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: v for k, v in res.items() if k != "explore"}, ensure_ascii=False, indent=1, default=str))
    for k in fk:
        e = explore[k]; print(k, [round(x, 3) if x is not None else None for x in e["rate"]], e["n"], [round(x, 3) for x in e["cuts"]])


if __name__ == "__main__":
    main()
