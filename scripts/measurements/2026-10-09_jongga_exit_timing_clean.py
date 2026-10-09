"""종가베팅 매도 타이밍 (a) 익일 시가 vs (d) 익일 종가 — 데이터 정제 후 재측정.

등록: docs/jongga_exit_timing_clean.md (해석 기본값 2개 사용자 승인 2026-10-09). 등록부: docs/measurement_registry.md.
원 사전등록: docs/kr_jongga_betting_backtest.md "사전등록: 종가베팅 매도 타이밍별 EV (2026-09-09)" → 2026-09-10 실행(무효 — 오염).

**원문 그대로**(모듈 import로 재사용, 재구현 금지):
- 후보·(a) 갭: `2026-08-29_kr_jongga_betting_backtest_extended.py`의 `turnover_rank_at`·`evaluate`(조합 A), `OFFSETS`
  (`checkpoints(60,950,10)`), 비용 0.3%.
- (d)·추가 보고·대응표본 검정: `2026-09-10_jongga_exit_timing.py`의 `evaluate_exit_timing`(룩어헤드 assert 포함)·
  `segment_report`·`paired_test`(d_i = (d) − (a), 양측).
- 판정 구조: 09-10 `run_stage2`와 같다 — (d)−(a) ≥ +0.30%p · |z| ≥ 문턱 · 이전/최근 절반 각각 n ≥ 100 그리고 (d)−(a) ≥ +0.30%p ·
  전체 n ≥ 100. 구간은 (a)/(d) 둘(원 사전등록에서 (b)(c)는 분봉 미확보로 폐기).
- 유니버스·fetch: 2026-09-01 v2 재측정과 같다(채택 재검증 스크립트의 `load_data`).

**바꾸는 것**(등록 문서 §2·§확인):
1. 데이터 정제 — 채택 재검증의 `CleanAccess`/`_Patched`(T까지 `clean_at_checkpoint`, T 다음 유효봉만, 정제 이력 260봉 미만이면
   평가 안 함). T+1이 무효면 다음 유효봉의 시가 (a)·종가 (d).
2. 유효성 게이트 기준값 = 정제 후 채택 재현값 +0.796%(±0.15%p) — 벗어나면 판정하지 않고 보고만.
3. z 문턱 = 2.24(Bonferroni 2개).

실행 시각: harness.check_run_window(["KR"]).
실행: MEAS_CACHE=<경로.pkl> python3 scripts/measurements/2026-10-09_jongga_exit_timing_clean.py  (결과 JSON 커밋 안 함)
"""
import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import harness  # noqa: E402


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


jrv = _load("jongga_adoption_clean", "2026-10-09_jongga_adoption_clean_revalidation.py")   # 정제 접근 계층·데이터
xt = _load("jongga_exit_timing_0910", "2026-09-10_jongga_exit_timing.py")                  # (d)·검정·보고 원문
os.chdir(ROOT)

RUN_MARKETS = ["KR"]
GATE_REF_EV = 0.007956501346550428   # 정제 후 채택 재현 (a) 비용차감 평균 +0.796%(n 301, 2026-10-09 jongga_adoption_clean_revalidation)
GATE_TOL = xt.ADOPTED_TOL            # ±0.15%p(원문)
Z_MIN = 2.24                         # Bonferroni 2개(사용자 승인 2026-10-09) — 원문 1.96 대체
MIN_DIFF = xt.JUDGMENT_MIN_DIFF      # +0.30%p(원문)
MIN_N = xt.JUDGMENT_MIN_N            # 100(원문)
OUT = os.path.join(HERE, os.path.basename(__file__).replace(".py", ".results.json"))


def collect(data, offsets, clean=True):
    """체크포인트마다 원 순위·원 (d) 추출을 그대로 부른다. clean이면 harness 접근만 정제판(원 코드 무수정).
    xt.orig·jrv.orig는 같은 파일의 별도 모듈 인스턴스지만 둘 다 sys.modules의 harness 하나를 보므로 _Patched가 양쪽에 적용된다."""
    half_idx = len(offsets) // 2
    saved = (xt.orig.OFFSETS, xt.orig.RECENT_HALF, xt.orig.EARLIER_HALF)
    xt.orig.OFFSETS, xt.orig.RECENT_HALF, xt.orig.EARLIER_HALF = list(offsets), set(offsets[:half_idx]), set(offsets[half_idx:])
    recs = []
    try:
        ctx = jrv._Patched(jrv.CleanAccess(data)) if clean else None
        if ctx:
            ctx.__enter__()
        try:
            for off in offsets:
                rank = xt.orig.turnover_rank_at(data, off)
                recs.extend(xt.evaluate_exit_timing(data, off, rank))
        finally:
            if ctx:
                ctx.__exit__()
    finally:
        xt.orig.OFFSETS, xt.orig.RECENT_HALF, xt.orig.EARLIER_HALF = saved
    return recs


def judge(recs):
    """09-10 run_stage2 판정 구조 그대로, 문턱만 Z_MIN. 게이트를 먼저 보고, 벗어나면 판정하지 않는다(None)."""
    if not recs:
        raise SystemExit("조합 A 레코드 0건 — 하드 실패")
    bad = [r for r in recs if r["open_t1"] <= 0 or r["close_t1"] <= 0 or r["close_t"] <= 0]
    if bad:                                                   # 정제가 빠뜨린 무효봉 — 측정 결함이므로 판정 전에 멈춘다
        raise SystemExit(f"정제 후에도 가격 ≤ 0 레코드 {len(bad)}건: {bad[:3]}")
    overall = xt.segment_report(recs)
    pt = xt.paired_test(recs)
    e = [r for r in recs if r["half"] == "earlier"]
    rc = [r for r in recs if r["half"] == "recent"]
    seg_e, seg_r = xt.segment_report(e), xt.segment_report(rc)
    pt_e, pt_r = xt.paired_test(e), xt.paired_test(rc)
    gate_diff = overall["a"]["net_mean"] - GATE_REF_EV
    gate_pass = abs(gate_diff) <= GATE_TOL
    checks = None
    passed = None
    if gate_pass:
        checks = {
            "diff_ge_030pp": overall["diff_d_minus_a_pp"] >= MIN_DIFF * 100,
            "z_ge_224": pt["z"] is not None and abs(pt["z"]) >= Z_MIN,
            "halves_reproduced": (seg_e["n"] >= MIN_N and seg_r["n"] >= MIN_N
                                  and seg_e["diff_d_minus_a_pp"] >= MIN_DIFF * 100
                                  and seg_r["diff_d_minus_a_pp"] >= MIN_DIFF * 100),
            "n_ge_100": overall["n"] >= MIN_N,
        }
        passed = all(checks.values())
    return {"overall": overall, "paired_test": pt, "half_earlier": seg_e, "paired_test_earlier": pt_e,
            "half_recent": seg_r, "paired_test_recent": pt_r,
            "validity_gate": {"ref_ev": GATE_REF_EV, "tol": GATE_TOL, "a_net_mean": overall["a"]["net_mean"],
                              "diff_pp": gate_diff * 100, "pass": gate_pass},
            "checks": checks, "passed": passed}


def main():
    run_window = harness.check_run_window(RUN_MARKETS)
    t0 = time.time()
    blob = jrv.load_data()
    data = blob["data"]
    stamp = harness.run_stamp(data)
    print(f"[stamp] {stamp}", flush=True)
    harness.assert_sufficient_depth(data, xt.orig.OFFSETS)
    recs = collect(data, list(xt.orig.OFFSETS), clean=True)
    res = judge(recs)
    result = {"run_stamp": stamp, "run_window": run_window, "fetched_at": blob.get("fetched_at"),
              "universe_count": blob.get("universe_count"), "fetched": len(data),
              "params": {"Z_MIN": Z_MIN, "MIN_DIFF": MIN_DIFF, "MIN_N": MIN_N, "GATE_REF_EV": GATE_REF_EV,
                         "GATE_TOL": GATE_TOL, "COST": xt.ROUND_TRIP_COST},
              **res, "raw_records": recs, "elapsed_s": round(time.time() - t0)}
    json.dump(result, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    show = {k: v for k, v in result.items() if k != "raw_records"}
    print(json.dumps(show, ensure_ascii=False, indent=1, default=str), flush=True)
    print(f"[done] {OUT}", flush=True)


if __name__ == "__main__":
    main()
