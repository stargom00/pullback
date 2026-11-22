"""저점 신호 × 일봉 준비(세력 꿈틀이 검색식) 필터 — H1 판정 측정. 사전등록: docs/lowpoint_stir_filter.md §1.

입력: 11-10 시스템 기대값 스크립트의 결과 JSON(trades) + 같은 캐시(MEAS_CACHE, 일봉). 매매 정의는 그 스크립트가 유일한 원본,
필터는 운영 칩과 같은 stir_screen.stir_check(import — 사본 없음).
실행: MEAS_CACHE=<lp_1900.pkl> python3 scripts/measurements/2026-11-23_lowpoint_stir_filter.py <system_expectancy.results.json>
"""
import importlib.util
import json
import math
import os
import pickle
import statistics
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts", "screens"))
import harness  # noqa: E402
import stir_screen  # noqa: E402

_spec = importlib.util.spec_from_file_location("k5", os.path.join(HERE, "2026-11-15_lowpoint_kosdaq_ma5_filter.py"))
k5 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(k5)
welch, prop_z = k5.welch, k5.prop_z        # 같은 통계 함수(사본 없음)

Z_MIN, N_MIN, FAST_DAYS = 2.39, 100, 20
OUT = os.path.join(HERE, os.path.basename(__file__).replace(".py", ".results.json"))


def fast(x):
    return x["system"]["exit"] == "target" and x["system"]["days"] <= FAST_DAYS


def side(rows):
    r = [x["system"]["ret"] for x in rows]
    tdays = [x["system"]["days"] for x in rows if x["system"]["exit"] == "target"]
    return {"n": len(rows), "fast": sum(fast(x) for x in rows), "fast_rate": sum(fast(x) for x in rows) / len(rows),
            "ret_mean": sum(r) / len(r), "ret_median": statistics.median(r),
            "cap": sum(x["system"]["exit"] == "cap" for x in rows), "cap_rate": sum(x["system"]["exit"] == "cap" for x in rows) / len(rows),
            "target_days_median": statistics.median(tdays) if tdays else None}


def judge(rows, gate=True):
    ok = [x for x in rows if x["stir"] is True]
    no = [x for x in rows if x["stir"] is False]
    if not ok or not no:
        return {"n_pass": len(ok), "n_fail": len(no), "verdict": "판단 불가(한쪽 0건)"}
    split = sorted(x["buy_date"] for x in rows)[len(rows) // 2]
    half = {}
    for nm, sel in (("early", lambda x: x["buy_date"] < split), ("late", lambda x: x["buy_date"] >= split)):
        a, b = [x for x in ok if sel(x)], [x for x in no if sel(x)]
        half[nm] = {"n_pass": len(a), "n_fail": len(b),
                    "fast_diff": (sum(map(fast, a)) / len(a) - sum(map(fast, b)) / len(b)) if a and b else None}
    sa, sb = side(ok), side(no)
    z1 = prop_z(sa["fast"], sa["n"], sb["fast"], sb["n"])
    z2 = welch([x["system"]["ret"] for x in ok], [x["system"]["ret"] for x in no]) if len(ok) > 1 else None
    z3 = prop_z(sb["cap"], sb["n"], sa["cap"], sa["n"])
    pos = all(h["fast_diff"] is not None and h["fast_diff"] > 0 for h in half.values())
    neg = all(h["fast_diff"] is not None and h["fast_diff"] < 0 for h in half.values())
    if gate and len(ok) < N_MIN:
        verdict = "판단 불가(표본 부족)"
    elif gate and len(no) < N_MIN:
        verdict = "판단 불가(미통과 표본 부족)"
    elif z1 is not None and z1 >= Z_MIN and pos:
        verdict = "채택"
    elif z1 is not None and z1 <= -Z_MIN and neg:
        verdict = "역방향"
    else:
        verdict = "기각"
    return {"pass": sa, "fail": sb, "O1_fast_diff_pp": (sa["fast_rate"] - sb["fast_rate"]) * 100, "O1_z": z1,
            "O2_ret_diff_pp": sa["ret_mean"] - sb["ret_mean"], "O2_z": z2, "O3_z_fail_minus_pass_cap": z3,
            "O2_flag": bool(z2 is not None and abs(z2) >= Z_MIN), "O3_flag": bool(z3 is not None and abs(z3) >= Z_MIN),
            "halves": half, "split": split, "verdict": verdict}


def main():
    run_window = harness.check_run_window(("kr",))
    src = json.load(open(sys.argv[1]))
    trades = src["trades"]
    if not trades:
        raise SystemExit("매매 0건")
    data = pickle.load(open(os.environ["MEAS_CACHE"], "rb"))["data"]
    rows, unknown = [], 0
    for x in trades:
        df = data[x["ticker"]]
        trunc = df[df.index < pd.Timestamp(x["buy_date"])]
        s = stir_screen.stir_check(harness.clean_at_checkpoint(trunc) if len(trunc) else None)
        if s["stir"] is None:
            unknown += 1
        assert s["stir_date"] is None or s["stir_date"] < x["buy_date"], "lookahead"
        rows.append({**x, "stir": s["stir"], "stir_date": s["stir_date"]})
    known = [x for x in rows if x["stir"] is not None]
    res = {"run_window": run_window, "source_stamp": src.get("run_stamp"), "n_trades": len(trades), "stir_unknown": unknown,
           "n_pass": sum(x["stir"] is True for x in known), "n_fail": sum(x["stir"] is False for x in known),
           "all": judge(known),
           "record_kosdaq": judge([x for x in known if x["ticker"].endswith(".KQ")], gate=False),
           "record_kospi": judge([x for x in known if x["ticker"].endswith(".KS")], gate=False),
           "pass_trades": [{k: x[k] for k in ("ticker", "label", "buy_date", "buy", "stir_date")} | {"exit": x["system"]["exit"], "days": x["system"]["days"], "ret": x["system"]["ret"]}
                           for x in known if x["stir"]]}
    json.dump(res, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: v for k, v in res.items() if k != "pass_trades"}, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
