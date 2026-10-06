"""v5.331 — ABC 표시 전용 3건(매물대 볼륨 프로파일 · B 품질 라벨 · 진돌이/가돌이 라벨). 등급·판정 불변.

사용자 지시: "전부 표시 전용, 등급 반영 금지" · "기존 KR 전체 ABC 등급 분포 전후 완전 일치". 전체 비교는 네트워크
데이터가 필요해 scripts/measurements/2026-10-06_abc_display_only_check.py로 돌렸다(KR 2,455종목, 판정 필드·등급
3경우 차이 0건). 여기서는 같은 비교를 **실제 종목 13개 고정 픽스처**(test_fixtures/abc_display_only.json.gz — 2026-10-06
naver 1900일 → app._downcast, 기댓값 = 수정 전 abc_screener(51343b9)의 결과)로 매번 돌린다.

사보타주 확인(2026-10-06, FAIL 확인 후 원복):
① grade()가 B 품질을 반영(재하락 주의면 C급) → test_judgement_and_grades_unchanged_on_real_fixture FAIL
② b_quality의 전반/후반 비교 방향을 뒤집음 → test_b_quality_labels_synthetic·test_b_quality_labels_real FAIL
③ 진돌이 기준을 > 로(정확히 3.0배는 가돌이) → test_breakout_label_boundary FAIL
④ 매물대가 현재가 아래 구간까지 고름 → test_supply_profile_above_current_only FAIL
"""
from __future__ import annotations

import gzip
import inspect
import json
import os

import pandas as pd
import pytest

import abc_screener as A

ROOT = os.path.dirname(os.path.abspath(__file__))
FX = json.load(gzip.open(os.path.join(ROOT, "test_fixtures", "abc_display_only.json.gz"), "rt", encoding="utf-8"))
FIN = {"pass": (4, 4, 2), "fail": (1, 4, 0), "unknown": (None, 0, None)}


def _df(t):
    o = FX[t]["ohlcv"]
    return pd.DataFrame({c: o[c] for c in ("Open", "High", "Low", "Close", "Volume")},
                        index=pd.to_datetime(o["date"])).astype("float64")


def _grade(r, case):
    rp, rof, eps = FIN[case]
    return A.grade(r, A.company_axis(r["b_turnover_eok"], rp, eps, False, rev_yoy_of=rof))


def _norm(v):
    return json.loads(json.dumps(v, default=str))


@pytest.mark.parametrize("t", sorted(FX))
def test_judgement_and_grades_unchanged_on_real_fixture(t):
    r = A.analyze_abc(_df(t))
    exp = FX[t]["expect"]
    assert {k: _norm(r.get(k)) for k in exp["judge"]} == exp["judge"]
    assert {k: (r.get("breakout") or {}).get(k) for k in exp["breakout"]} == exp["breakout"]
    assert {c: _grade(r, c) for c in FIN} == exp["grades"]


def test_fixture_covers_every_label_and_stage():
    labels = {FX[t]["new_labels"]["b_quality"] for t in FX}
    stages = {FX[t]["expect"]["judge"]["c_stage"] for t in FX}
    assert labels == {A.B_ABSORB, A.B_REDROP, A.B_NEUTRAL, A.B_NONE}
    assert set(A.C_STAGES) <= stages and {FX[t]["new_labels"]["breakout"] for t in FX} >= {A.BREAKOUT_TRUE, A.BREAKOUT_FALSE}
    # 사보타주 ①이 실제로 등급을 바꿀 수 있는 표본(재하락 주의 + C급 아닌 등급)이 있어야 한다
    assert any(FX[t]["new_labels"]["b_quality"] == A.B_REDROP and FX[t]["expect"]["grades"]["pass"] in (A.GRADE_A, A.GRADE_B)
               for t in FX)


def test_b_quality_labels_real():
    got = {t: A.analyze_abc(_df(t))["b_quality"]["label"] for t in FX}
    assert got == {t: FX[t]["new_labels"]["b_quality"] for t in FX}
    for t in FX:
        q = A.analyze_abc(_df(t))["b_quality"]
        if q["label"] == A.B_REDROP:
            assert q["low2"] < q["low1"]
        if q["label"] == A.B_ABSORB:
            assert q["low2"] > q["low1"] and q["vol2"] < q["vol1"]


def test_hideep_b_not_formed_and_grade_c():
    """하이딥(365590.KQ): 저점(09-30) 직후라 B 미형성, 실제 실적(EPS 흑자 0/2분기 · 매출 YoY 1분기만 판정 가능)으로 C급 — 전후 동일."""
    r = A.analyze_abc(_df("365590.KQ"))
    assert r["b_quality"] == {"label": A.B_NONE} and r["c_stage"] == A.STAGE_WAIT
    comp = A.company_axis(r["b_turnover_eok"], 0, 0, False, rev_yoy_of=1)
    assert A.grade(r, comp) == A.GRADE_C


def _bq(c1, c2, v1, v2):
    closes = pd.Series(c1 + c2, dtype="float64")
    vols = pd.Series(v1 + v2, dtype="float64")
    return A.b_quality(closes, vols, 0, {"bars": len(closes), "range_pct": 10.0})["label"]


def test_b_quality_labels_synthetic():
    up, down = [100, 98, 97, 99, 101] * 2, [103, 104, 102, 105, 104] * 2
    assert _bq(up, down, [500] * 10, [300] * 10) == A.B_ABSORB          # 저점↑ · 거래량↓
    assert _bq(up, down, [300] * 10, [500] * 10) == A.B_NEUTRAL         # 저점↑ · 거래량↑
    assert _bq(down, up, [500] * 10, [300] * 10) == A.B_REDROP          # 저점↓(종가 최저 97 < 102)
    assert _bq(up, up, [500] * 10, [300] * 10) == A.B_NEUTRAL           # 같은 저점
    assert A.b_quality(pd.Series([1.0]), pd.Series([1.0]), 0, {"bars": 3, "range_pct": None}) == {"label": A.B_NONE}
    assert "seg_c" in inspect.getsource(A.b_quality) and "low" not in inspect.signature(A.b_quality).parameters   # 종가 기준


def test_breakout_label_boundary():
    """기존 c2_vol_mult(3.0)를 그대로 — 정확히 3.0배 = 진돌이, 미만 = 가돌이."""
    import numpy as np
    n = 260
    base = [100.0] * 230 + [90.0] * 25 + [120.0] * 5         # MA200 아래 → 위 돌파(255번째 봉)
    for vol_peak, want in ((3.0, A.BREAKOUT_TRUE), (2.99, A.BREAKOUT_FALSE)):
        vols = [1000.0] * n
        vols[255] = 1000.0 * vol_peak
        c = pd.Series(base, dtype="float64")
        bo = A._find_breakout(c, pd.Series(vols, dtype="float64"), A.ABC_CONFIG)
        assert bo and bo["vol_mult"] == pytest.approx(vol_peak) and bo["label"] == want
    assert A.ABC_CONFIG["c2_vol_mult"] == 3.0
    assert 'cfg["c2_vol_mult"]' in inspect.getsource(A._find_breakout)


def test_supply_profile_above_current_only():
    closes = pd.Series([10.0] * 50 + [20.0] * 50 + [30.0] * 50 + [15.0] * 10, dtype="float64")
    vols = pd.Series([100.0] * 50 + [900.0] * 50 + [300.0] * 50 + [50.0] * 10, dtype="float64")
    p = A.supply_profile(closes, vols, 15.0)
    assert len(p["bins"]) == A.ABC_CONFIG["supply_profile_bins"] == 10
    assert p["zone"]["lo"] == pytest.approx(20.0) and p["zone"]["hi"] == pytest.approx(22.0)     # 현재가(15) 위 최대 = 20원대
    assert sum(b["vol"] for b in p["bins"]) == pytest.approx(float(vols.sum()))
    top = A.supply_profile(closes, vols, 31.0)
    assert top["zone"] is None                                                                     # 위에 거래량 없음
    lines = A.format_supply_bins(p, 15.0)
    assert len(lines) == 10 and lines[0].lstrip().startswith(("▲", "★"))                        # 수동 대조용 구간별 로그


def test_display_only_not_used_in_grade():
    g = inspect.getsource(A.grade)
    for k in ("b_quality", "supply_zone", "label", "B_ABSORB", "B_REDROP", "BREAKOUT_TRUE"):
        assert k not in g.split('"""')[2], k
    assert "supply_band" not in A.ABC_CONFIG and "supply_min_bars" not in A.ABC_CONFIG


def test_wyckoff_note_recorded():
    src = open(A.__file__, encoding="utf-8").read()
    assert "②자동반등·③2차테스트·⑤스프링·⑦되돌림은 의도적으로 모델링하지 않는다" in src
    assert "와이코프 ②③⑤⑦" in open(os.path.join(ROOT, "CLAUDE.md"), encoding="utf-8").read()


# ── 화면(node, production 원문) ────────────────────────────────────
def _fn(src, name):
    start = src.index(f"function {name}(")
    i = src.index("{", src.index(")", start))
    d = 0
    for j in range(i, len(src)):
        d += {"{": 1, "}": -1}.get(src[j], 0)
        if d == 0:
            return src[start:j + 1]


def test_cells_render_labels_and_zone():
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    html = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
    js = _fn(html, "abcBQualityHtml") + "\n" + _fn(html, "abcSupplyZoneHtml") + """
console.log(JSON.stringify([abcBQualityHtml({label: 'B 미형성'}), abcBQualityHtml({label: '흡수', low1: 100, low2: 105, vol1: 500, vol2: 300}),
  abcSupplyZoneHtml({lo: 5083.6, hi: 5722.7, vol_share_pct: 17.7}), abcSupplyZoneHtml(null), abcBQualityHtml(null)]));"""
    p = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    none, absorb, zone, nozone, empty = json.loads(p.stdout)
    assert ">B 미형성<" in none and ">흡수<" in absorb and "100 → 후반 105" in absorb
    assert ">5,084~5,723원<" in zone and "17.7%" in zone and ">—<" in nozone and empty == ""
    assert "supply_above" not in html and html.count("abcBQualityHtml(h.b_quality)") == 1 and html.count("abcSupplyZoneHtml(h.supply_zone)") == 1
    assert "h.breakout_label" in html
