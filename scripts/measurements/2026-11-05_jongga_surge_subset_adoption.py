"""종가베팅 급등군(+15%) 채택 확인 측정 — 사전등록 docs/jongga_surge_subset_adoption.md.

표본은 2026-10-09_jongga_adoption_clean_revalidation.run(…, clean=True)의 조합 A 레코드 그대로(사본 없음).
분할은 원 레코드의 ret_t ≥ SURGE_MIN(0.15, 09-13 사전등록 값). 판정식은 채택 기준(문서 §1).

실행 시각: harness.check_run_window(["KR"]).
실행: MEAS_CACHE=<경로.pkl> python3 scripts/measurements/2026-11-05_jongga_surge_subset_adoption.py
"""
import importlib.util
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import harness  # noqa: E402

RUN_MARKETS = ["KR"]
SURGE_MIN = 0.15            # 09-13 사전등록(docs/jongga_surge_day_gap.md §1.2) 그대로 — 포워드 29건을 보고 고른 20%가 아니다
NET_MIN = 0.005             # 비용 차감 평균 최소 — 원 스크립트와 같은 소수(0.005 = +0.5%p)
Z_MIN = 1.96
N_MIN = 100
REPRO_REF = 0.00796         # 10-09 채택 재현 검증 조합 A 비용 차감 평균(소수, = +0.796%)
REPRO_TOL = 0.0015


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, fname))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _group_stats(recs, cost):
    """비용 차감 익일 갭(소수, 0.01 = 1%) 기준 집계. 판정식 1·2·4에 쓰는 값 + 기록용 중앙값·오른 비율."""
    n = len(recs)
    if n == 0:
        return {"n": 0}
    g = [r["gap_open"] - cost for r in recs]
    m = sum(g) / n
    sd = statistics.stdev(g) if n > 1 else 0.0
    z0 = (m / (sd / n ** 0.5)) if sd > 0 else None
    return {"n": n, "net_mean": m, "median": statistics.median(g), "sd": sd, "z_vs_zero": z0,
            "up_rate": sum(1 for x in g if x > 0) / n}


def _welch_z(a, b):
    if len(a) < 2 or len(b) < 2:
        return None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va, vb = statistics.variance(a), statistics.variance(b)
    se = (va / len(a) + vb / len(b)) ** 0.5
    return (ma - mb) / se if se > 0 else None


def judge(combo, cost):
    """조합 A 레코드(ret_t·gap_open·half 포함) → 분할·판정. 순수 함수(테스트 대상)."""
    surge = [r for r in combo if r["ret_t"] >= SURGE_MIN]
    ctrl = [r for r in combo if r["ret_t"] < SURGE_MIN]
    s, c = _group_stats(surge, cost), _group_stats(ctrl, cost)
    e = _group_stats([r for r in surge if r["half"] == "earlier"], cost)
    rc = _group_stats([r for r in surge if r["half"] == "recent"], cost)
    zdiff = _welch_z([r["gap_open"] for r in surge], [r["gap_open"] for r in ctrl])
    checks = {
        "1_net_mean_ge_0.5pct": s.get("n", 0) > 0 and s["net_mean"] >= NET_MIN,
        "2_z_vs_zero_ge_1.96": s.get("z_vs_zero") is not None and s["z_vs_zero"] >= Z_MIN,
        "3_halves_ge_0.5pct": bool(e.get("n") and rc.get("n") and e["net_mean"] >= NET_MIN and rc["net_mean"] >= NET_MIN),
        "4_n_ge_100": s.get("n", 0) >= N_MIN,
        "5_diff_vs_ctrl_z_ge_1.96": zdiff is not None and zdiff >= Z_MIN,
    }
    record_only = {
        "surge_ge_20": _group_stats([r for r in surge if r["ret_t"] >= 0.20], cost),
        "surge_ge_25": _group_stats([r for r in surge if r["ret_t"] >= 0.25], cost),
    }
    return {"surge": s, "ctrl": c, "surge_half_earlier": e, "surge_half_recent": rc, "z_diff_vs_ctrl": zdiff,
            "checks": checks, "passed": all(checks.values()), "record_only": record_only}


def main():
    run_window = harness.check_run_window(RUN_MARKETS)
    t0 = time.time()
    reval = _load("jongga_reval", "2026-10-09_jongga_adoption_clean_revalidation.py")
    blob = reval.load_data()
    data = blob["data"]
    stamp = harness.run_stamp(data)
    print(f"[stamp] {stamp}", flush=True)
    J = reval.run(data, list(reval.orig.OFFSETS), True, "판정_정제")
    combo = J["_combo"]
    cost = reval.orig.ROUND_TRIP_COST
    repro = J["combo_a"].get("net_mean")
    repro_ok = repro is not None and abs(repro - REPRO_REF) <= REPRO_TOL
    out = {"run_window": run_window, "stamp": stamp, "repro": {"combo_a_net_mean": repro, "ref": REPRO_REF, "tol": REPRO_TOL, "ok": repro_ok},
           "combo_a_n": len(combo), "surge_min": SURGE_MIN, "cost": cost}
    if not repro_ok:
        out["verdict"] = "보류(재현 게이트 실패)"
    else:
        res = judge(combo, cost)
        out.update(res)
        out["verdict"] = "통과" if res["passed"] else "미달"
    out["elapsed_s"] = round(time.time() - t0)
    path = os.path.join(HERE, "2026-11-05_jongga_surge_subset_adoption_result.json")
    with open(path, "w") as f:
        json.dump({k: v for k, v in out.items() if not k.startswith("_")}, f, ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: v for k, v in out.items() if k in ("verdict", "repro", "surge", "ctrl", "surge_half_earlier",
                                                             "surge_half_recent", "z_diff_vs_ctrl", "checks")},
                     ensure_ascii=False, indent=1, default=str))
    print(f"[done] {path}", flush=True)


if __name__ == "__main__":
    main()
