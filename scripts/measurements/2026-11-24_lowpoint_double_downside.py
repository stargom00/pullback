"""1년 +100% 특징 종목의 하락 쪽 — 측정만. 사전등록: docs/lowpoint_double_downside.md §1. 특징 함수·컷은 직전 측정에서 import(사본 없음).
실행: MEAS_CACHE=<pkl> python3 scripts/measurements/2026-11-24_lowpoint_double_downside.py <system_expectancy.results.json> <double_features.results.json>
"""
import importlib.util
import json
import os
import pickle
import statistics
import sys
from collections import Counter

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("df", os.path.join(HERE, "2026-11-24_lowpoint_double_features.py"))
dfm = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(dfm)
harness = dfm.harness
OUT = os.path.join(HERE, os.path.basename(__file__).replace(".py", ".results.json"))


def summ(rows):
    if not rows:
        return {"n": 0}
    dd = [r["mdd"] for r in rows]; re_ = [r["ret_end"] for r in rows]; ok = [r for r in rows if r["ok"]]
    return {"n": len(rows), "double_rate": len(ok) / len(rows),
            "mdd_median": statistics.median(dd), "mdd_le_-30": sum(x <= -0.30 for x in dd) / len(rows),
            "mdd_le_-50": sum(x <= -0.50 for x in dd) / len(rows),
            "ret_end_median": statistics.median(re_), "ret_end_mean": statistics.mean(re_),
            "loss_at_end": sum(x < 0 for x in re_) / len(rows), "end_le_-50": sum(x <= -0.50 for x in re_) / len(rows),
            "hit_-30_before_double": (sum(r["dd30_first"] for r in ok) / len(ok)) if ok else None,
            # 실행 뒤 추가(기록, 사전등록 밖): 사용자 규칙대로 +100%에서 팔고 아니면 250일 뒤 종가 — 매수가 대비 평균·중앙
            "rule100_mean": statistics.mean(1.0 if r["ok"] else r["ret_end"] for r in rows),
            "rule100_median": statistics.median(1.0 if r["ok"] else r["ret_end"] for r in rows),
            "nondouble_end_median": statistics.median([r["ret_end"] for r in rows if not r["ok"]]) if len(ok) < len(rows) else None}


def main():
    run_window = harness.check_run_window(("kr",))
    trades = json.load(open(sys.argv[1]))["trades"]
    cuts = json.load(open(sys.argv[2]))["explore"]
    c4, c7 = cuts["F4_ma200_gap"]["cuts"], cuts["F7_range60"]["cuts"]
    data = pickle.load(open(os.environ["MEAS_CACHE"], "rb"))["data"]
    stats, rows, views = Counter(), [], {}
    for x in trades:
        v = views.get(x["ticker"]) or views.setdefault(x["ticker"], harness.CleanView(data[x["ticker"]]))
        if v.dirty:
            stats["dirty_skip"] += 1; continue
        p = v.full
        jb = p.index.get_loc(pd.Timestamp(x["buy_date"])) - 1
        if jb + 1 < dfm.LISTED_MIN:
            stats["listed_lt_1y"] += 1; continue
        path = p.iloc[jb + 1:jb + 1 + dfm.H]
        if len(path) < dfm.H:
            stats["future_short"] += 1; continue
        buy = float(path["Open"].iloc[0])
        hi, lo = path["High"].to_numpy(float), path["Low"].to_numpy(float)
        hit = np.nonzero(hi >= 2 * buy)[0]
        d30 = np.nonzero(lo <= 0.7 * buy)[0]
        f = dfm.feats(p, jb)
        rows.append({"ok": bool(len(hit)), "mdd": float(lo.min()) / buy - 1, "ret_end": float(path["Close"].iloc[-1]) / buy - 1,
                     "dd30_first": bool(len(hit) and len(d30) and d30[0] < hit[0]),
                     "f4": f["F4_ma200_gap"], "f7": f["F7_range60"]})
    assert len(rows) == json.load(open(sys.argv[2]))["base"]["n"], "표본 수가 직전 측정과 다름"
    g4 = lambda r: r["f4"] <= c4[0]; b4 = lambda r: r["f4"] > c4[2]
    g7 = lambda r: r["f7"] > c7[2]; b7 = lambda r: r["f7"] <= c7[0]
    groups = {"all": rows, "F4_good": [r for r in rows if g4(r)], "F4_bad": [r for r in rows if b4(r)],
              "F7_good": [r for r in rows if g7(r)], "F7_bad": [r for r in rows if b7(r)],
              "both_good": [r for r in rows if g4(r) and g7(r)], "neither_good": [r for r in rows if not g4(r) and not g7(r)]}
    res = {"run_window": run_window, "run_stamp": harness.run_stamp(data), "stats": dict(stats), "cuts": {"F4": c4, "F7": c7},
           "groups": {k: summ(v) for k, v in groups.items()}}
    json.dump(res, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    for k, s in res["groups"].items():
        print(k, {kk: (round(vv, 3) if isinstance(vv, float) else vv) for kk, vv in s.items()})


if __name__ == "__main__":
    main()
