"""2026-11-05 종가베팅 급등군(+15%) 채택 확인 측정 — judge() 분할·판정식 고정.

사보타주로 확인한 것: 문턱을 0.20으로 바꾸면 분할 테스트 FAIL, 시기 반분 검사를 빼면 판정 테스트 FAIL.
"""
import importlib.util
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "surge_subset", os.path.join(HERE, "scripts", "measurements", "2026-11-05_jongga_surge_subset_adoption.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def _rec(ret_t, gap, half):
    return {"ret_t": ret_t, "gap_open": gap, "half": half}


def test_threshold_is_the_09_13_value_not_forward_pick():
    assert m.SURGE_MIN == 0.15


def test_split_boundary_inclusive_at_15pct():
    combo = [_rec(0.15, 1.0, "earlier"), _rec(0.149, 1.0, "earlier"), _rec(0.30, 1.0, "recent")]
    r = m.judge(combo, cost=0.0)
    assert r["surge"]["n"] == 2 and r["ctrl"]["n"] == 1


def test_passes_when_all_five_hold():
    rnd = random.Random(7)
    combo = [_rec(0.2, 0.02 + rnd.gauss(0, 0.01), "earlier" if i % 2 else "recent") for i in range(120)]
    combo += [_rec(0.05, 0.0 + rnd.gauss(0, 0.01), "earlier" if i % 2 else "recent") for i in range(120)]
    r = m.judge(combo, cost=0.003)
    assert r["passed"], r["checks"]
    assert r["surge"]["n"] == 120 and r["ctrl"]["n"] == 120


def test_fails_on_n_below_100():
    rnd = random.Random(1)
    combo = [_rec(0.2, 0.03 + rnd.gauss(0, 0.005), "earlier" if i % 2 else "recent") for i in range(99)]
    combo += [_rec(0.05, 0.0, "earlier"), _rec(0.05, 0.0, "recent")]
    r = m.judge(combo, cost=0.003)
    assert not r["passed"] and r["checks"]["4_n_ge_100"] is False


def test_fails_when_one_half_is_weak():
    rnd = random.Random(3)
    combo = [_rec(0.2, 0.03 + rnd.gauss(0, 0.005), "earlier") for _ in range(80)]
    combo += [_rec(0.2, 0.002 + rnd.gauss(0, 0.005), "recent") for _ in range(80)]     # 후반 약함
    combo += [_rec(0.05, 0.0 + rnd.gauss(0, 0.005), "recent") for _ in range(50)]
    r = m.judge(combo, cost=0.003)
    assert r["checks"]["1_net_mean_ge_0.5pct"] and r["checks"]["4_n_ge_100"]
    assert r["checks"]["3_halves_ge_0.5pct"] is False and not r["passed"]


def test_fails_when_ctrl_is_as_good():
    rnd = random.Random(5)
    combo = [_rec(0.2, 0.02 + rnd.gauss(0, 0.01), "earlier" if i % 2 else "recent") for i in range(120)]
    combo += [_rec(0.05, 0.02 + rnd.gauss(0, 0.01), "earlier" if i % 2 else "recent") for i in range(120)]
    r = m.judge(combo, cost=0.003)
    assert r["checks"]["5_diff_vs_ctrl_z_ge_1.96"] is False and not r["passed"]


def test_cost_is_subtracted():
    combo = [_rec(0.2, 0.006, "earlier"), _rec(0.2, 0.006, "recent")]
    assert abs(m.judge(combo, cost=0.003)["surge"]["net_mean"] - 0.003) < 1e-12


def test_units_match_base_script():
    """원 스크립트(ROUND_TRIP_COST=0.003, NET_MIN 0.005)와 같은 소수 단위 — %와 섞이면 판정이 통째로 틀린다."""
    assert m.NET_MIN == 0.005 and abs(m.REPRO_REF - 0.00796) < 1e-9 and m.REPRO_TOL == 0.0015
