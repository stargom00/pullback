"""측정 D 2단계 — 판정. 사전등록: docs/jongga_surge_day_gap.md (커밋 f620105).

§1.4 판정 조건(전부 만족해야 "차이 있음"):
  1. |중앙값 차이| >= 1.0%p
  2. MWU z >= 1.96 (양측, 부호 무관)
  3. 시기 반분 둘 다 같은 부호 + 각 군 n >= 20
  4. 각 군 n >= 30

문턱은 여기서 바꾸지 않는다. 재현 게이트(전체 평가·조합 A)를 먼저 통과해야 판정한다.
"""
import json
import os
import pickle
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import harness
_m = __import__("2026-08-29_kr_jongga_betting_backtest_extended")

SURGE_MIN = 0.15                 # 사전등록 1.2 — 임의값, 격자탐색 금지
MEDIAN_GAP_MIN = 1.0             # %p, 사전등록 1.4-1
Z_MIN = 1.96                     # 1.4-2
HALF_N_MIN = 20                  # 1.4-3
GROUP_N_MIN = 30                 # 1.4-4
REF_TOTAL, REF_COMBO_A = 121698, 276
GATE_TOL = 0.05

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "2026-09-13_jongga_surge_gap_test.results.json")


def net_gap_pct(r):
    """익일 갭(%) — 백테스트와 같은 비용 가정(왕복 0.3% 차감)."""
    return (r["gap_open"] - _m.ROUND_TRIP_COST) * 100


def describe(rows):
    s = pd.Series([net_gap_pct(r) for r in rows], dtype=float).dropna()
    if s.empty:
        return {"n": 0}
    return {"n": int(len(s)), "median": round(float(s.median()), 3),
            "mean": round(float(s.mean()), 3),
            "win_rate": round(float((s > 0).mean()), 4),
            "std": round(float(s.std(ddof=1)), 3) if len(s) > 1 else None}


def compare(surge, rest):
    a = pd.Series([net_gap_pct(r) for r in rest], dtype=float).dropna()    # 대조군
    b = pd.Series([net_gap_pct(r) for r in surge], dtype=float).dropna()   # 급등군
    if a.empty or b.empty:
        return {"median_gap": None, "mwu_z": None}
    z, sig = harness.mannwhitney_zscore(a, b)
    wz, _ = harness.welch_zscore(a, b)
    return {"median_gap": round(float(b.median() - a.median()), 3),
            "mwu_z": round(float(z), 3) if z is not None else None,
            "mwu_sig": bool(sig),
            "mean_gap_ref_only": round(float(b.mean() - a.mean()), 3),
            "welch_z_ref_only": round(float(wz), 3) if wz is not None else None}


def main():
    cache = os.environ.get("MEAS_CACHE")
    if cache and os.path.exists(cache):
        print(f"[cache] load {cache}", flush=True)
        data = pickle.load(open(cache, "rb"))
    else:
        data = _m.fetch_kr_long_universe()
        if cache:
            pickle.dump(data, open(cache, "wb"))

    records = []
    for off in _m.OFFSETS:
        records.extend(_m.evaluate(data, off, _m.turnover_rank_at(data, off)))

    combo_a = [r for r in records
               if r["base"] and r["candle"] and r["volume"] and r["position"]]

    gate = {}
    for key, got, ref in (("total", len(records), REF_TOTAL),
                          ("combo_a", len(combo_a), REF_COMBO_A)):
        diff = got / ref - 1
        gate[key] = {"got": got, "ref": ref, "diff_pct": round(diff * 100, 2),
                     "passed": abs(diff) <= GATE_TOL}
        print(f"[gate] {key}: {got} vs {ref} ({diff*100:+.2f}%) -> "
              f"{'OK' if gate[key]['passed'] else 'FAIL'}", flush=True)
    gate_ok = all(v["passed"] for v in gate.values())

    surge = [r for r in combo_a if r["ret_t"] >= SURGE_MIN]
    rest = [r for r in combo_a if r["ret_t"] < SURGE_MIN]

    halves = {}
    for half in ("recent", "earlier"):
        s = [r for r in surge if r["half"] == half]
        c = [r for r in rest if r["half"] == half]
        halves[half] = {"surge": describe(s), "rest": describe(c), **compare(s, c)}

    pooled = compare(surge, rest)
    signs = [halves[h]["median_gap"] for h in ("recent", "earlier")]
    same_sign = all(v is not None for v in signs) and (signs[0] > 0) == (signs[1] > 0)
    half_n_ok = all(halves[h]["surge"]["n"] >= HALF_N_MIN
                    and halves[h]["rest"]["n"] >= HALF_N_MIN for h in halves)

    checks = {
        "effect_size": abs(pooled["median_gap"] or 0) >= MEDIAN_GAP_MIN,
        "significance": abs(pooled["mwu_z"] or 0) >= Z_MIN,
        "half_same_sign": bool(same_sign),
        "half_n": bool(half_n_ok),
        "group_n": len(surge) >= GROUP_N_MIN and len(rest) >= GROUP_N_MIN,
    }
    verdict = {"checks": checks, "passed": all(checks.values()) and gate_ok,
               "gate_passed": gate_ok,
               "direction": (None if not pooled["median_gap"] else
                             ("급등군이 나쁨" if pooled["median_gap"] < 0 else "급등군이 좋음"))}

    result = {"meta": {"prereg": "docs/jongga_surge_day_gap.md (commit f620105)",
                       "surge_min": SURGE_MIN, "cost": _m.ROUND_TRIP_COST,
                       "thresholds": {"median_gap_min": MEDIAN_GAP_MIN, "z_min": Z_MIN,
                                      "half_n_min": HALF_N_MIN, "group_n_min": GROUP_N_MIN},
                       "run_stamp": harness.run_stamp(data)},
              "gate": gate,
              "groups": {"surge": describe(surge), "rest": describe(rest)},
              "pooled": pooled, "halves": halves, "verdict": verdict}

    json.dump(result, open(OUT, "w"), ensure_ascii=False, indent=2, default=str)
    print(json.dumps({k: result[k] for k in ("groups", "pooled", "halves", "verdict")},
                     ensure_ascii=False, indent=2), flush=True)
    print(f"[done] -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
