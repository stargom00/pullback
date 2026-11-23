"""다이버전스+양봉 자리만 사기(B) vs 그 자리가 안 나온 신호의 직후 매수(N) — H1 판정. 사전등록: docs/lowpoint_div_skip.md §1.
입력: 진입 타이밍 측정 결과 JSON(B 매매) + 11-10 trades JSON + 같은 캐시. 표본 조건은 진입 타이밍 스크립트의 상수를 import(사본 없음).
실행: MEAS_CACHE=<pkl> python3 scripts/measurements/2026-11-24_lowpoint_div_skip.py <timing.results.json> <system_expectancy.results.json>
"""
import importlib.util
import json
import os
import pickle
import statistics
import sys
from collections import Counter

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("et", os.path.join(HERE, "2026-11-24_lowpoint_entry_timing.py"))
et = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(et)
harness, divergence = et.harness, et.divergence
_spec2 = importlib.util.spec_from_file_location("k5", os.path.join(HERE, "2026-11-15_lowpoint_kosdaq_ma5_filter.py"))
k5 = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(k5)
welch, prop_z = k5.welch, k5.prop_z

Z_MIN, N_MIN = 2.39, 100
OUT = os.path.join(HERE, os.path.basename(__file__).replace(".py", ".results.json"))
fast = et.fast


def side(ss):
    return {"n": len(ss), "fast_rate": sum(map(fast, ss)) / len(ss), "ret_mean": statistics.mean(s["ret"] for s in ss),
            "ret_median": statistics.median(s["ret"] for s in ss), "cap_rate": sum(s["exit"] == "cap" for s in ss) / len(ss),
            "ret_per_100_capdays": statistics.mean(s["ret"] / s["cap_days"] * 100 for s in ss)}


def main():
    run_window = harness.check_run_window(("kr",))
    timing = json.load(open(sys.argv[1]))
    trades = json.load(open(sys.argv[2]))["trades"]
    data = pickle.load(open(os.environ["MEAS_CACHE"], "rb"))["data"]
    b_rows = timing["trades"]["B_divergence"]
    b_keys = {(r["ticker"], r["base_date"]) for r in b_rows}
    stats, n_rows, b_seen = Counter(), [], set()
    views = {}
    for x in trades:
        v = views.get(x["ticker"]) or views.setdefault(x["ticker"], harness.CleanView(data[x["ticker"]]))
        if v.dirty:
            stats["dirty_skip"] += 1
            continue
        p = v.full
        jb = p.index.get_loc(pd.Timestamp(x["buy_date"])) - 1
        if jb + 1 < et.LISTED_MIN:
            stats["listed_lt_1y"] += 1
            continue
        if len(p) - 1 - jb < et.WINDOW + 1 + et.HOLD_MAX:
            stats["future_short"] += 1
            continue
        stats["sample"] += 1
        bd = str(p.index[jb].date())
        e = divergence.entry_signal(p.iloc[:jb + et.WINDOW + 1], bd)
        if e is None:
            n_rows.append({"base_date": bd, "s": x["system"]})
        elif (x["ticker"], bd) in b_keys:
            b_seen.add((x["ticker"], bd))
        else:
            stats["triggered_not_in_B"] += 1
    missing = b_keys - b_seen
    assert not missing, f"B 매매 {len(missing)}건이 표본 재구성에서 안 보임 — 표본 정의 불일치"
    assert stats["sample"] == timing["stats"]["sample"], "표본 수가 진입 타이밍 측정과 다름"
    rows = [{"g": "B", "base_date": r["base_date"], "s": r["cand"]} for r in b_rows] + [{"g": "N", **r} for r in n_rows]
    B = [r for r in rows if r["g"] == "B"]
    N = [r for r in rows if r["g"] == "N"]
    sb, sn = side([r["s"] for r in B]), side([r["s"] for r in N])
    z1 = prop_z(sum(fast(r["s"]) for r in B), len(B), sum(fast(r["s"]) for r in N), len(N))
    z2 = welch([r["s"]["ret"] for r in B], [r["s"]["ret"] for r in N])
    z3 = prop_z(sum(r["s"]["exit"] == "cap" for r in N), len(N), sum(r["s"]["exit"] == "cap" for r in B), len(B))
    split = sorted(r["base_date"] for r in rows)[len(rows) // 2]
    halves = {}
    for nm, sel in (("early", lambda r: r["base_date"] < split), ("late", lambda r: r["base_date"] >= split)):
        bb = [r["s"] for r in B if sel(r)]
        nn = [r["s"] for r in N if sel(r)]
        halves[nm] = {"n_B": len(bb), "n_N": len(nn),
                      "diff": (sum(map(fast, bb)) / len(bb) - sum(map(fast, nn)) / len(nn)) if bb and nn else None}
    pos = all(h["diff"] is not None and h["diff"] > 0 for h in halves.values())
    neg = all(h["diff"] is not None and h["diff"] < 0 for h in halves.values())
    if len(B) < N_MIN or len(N) < N_MIN:
        verdict = "판단 불가(표본 부족)"
    elif z1 >= Z_MIN and pos:
        verdict = "B 우위"
    elif z1 <= -Z_MIN and neg:
        verdict = "N 우위"
    else:
        verdict = "차이 없음"
    dates = sorted(r["base_date"] for r in rows)
    years = (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[0])).days / 365.25
    res = {"run_window": run_window, "run_stamp": harness.run_stamp(data), "stats": dict(stats), "B": sb, "N": sn,
           "O1_diff_pp": (sb["fast_rate"] - sn["fast_rate"]) * 100, "O1_z": z1, "O2_z": z2, "O3_z_N_minus_B_cap": z3,
           "O2_flag": abs(z2) >= Z_MIN, "O3_flag": abs(z3) >= Z_MIN, "halves": halves, "split": split, "verdict": verdict,
           "trades_per_year": {"B": len(B) / years, "N": len(N) / years, "all_direct": stats["sample"] / years}}
    json.dump(res, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
