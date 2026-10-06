"""v5.335 — ABC 📦 박스돌파 사건 표시 + [📦박스돌파만] 칩(표시 전용 — 등급·판정 불변).

사용자 지시: "ABC 행에 '📦박스돌파' 사건 표시 추가 (🩷600돌파 칸처럼 등급과 독립): 최근 20봉 안에 직전 60봉 최고가를
종가로 처음 넘은 봉이 있고, 그 봉 등락 ≥ +7%, 거래량 ≥ 50일 평균 × 2.0, 지금도 그 박스 상단 위. 상수는 강돌파 것과
B 최대 길이(60)를 재사용. 새 상수 금지." · "박스돌파 필터 칩 하나 추가: [📦박스돌파만]" · "등급 계산에 반영 금지".
실데이터: test_fixtures/abc_theme_20261006.json.gz(10-06) — 상신이디피 📦(거래량 2.42배·+11.36%), 포스코퓨처엠 미표시(1.90배).

사보타주 확인(2026-10-07, FAIL 확인 후 원복): grade()가 box_break.ok를 강돌파로 취급 →
test_grade_unchanged_on_real_data·test_grade_ignores_box_break FAIL
"""
from __future__ import annotations

import copy
import gzip
import inspect
import json
import os
import shutil
import subprocess

import pandas as pd
import pytest

import abc_screener as A

ROOT = os.path.dirname(os.path.abspath(__file__))
HTML = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
FX = json.load(gzip.open(os.path.join(ROOT, "test_fixtures", "abc_theme_20261006.json.gz"), "rt", encoding="utf-8"))


def _df(t):
    f = FX["frames"][t]
    return pd.DataFrame({c: f[c] for c in ("Open", "High", "Low", "Close", "Volume")}, index=pd.to_datetime(f["date"])).astype("float64")


def _src(name):
    start = HTML.index(f"function {name}(")
    i = HTML.index("{", HTML.index(")", start))
    d = 0
    for j in range(i, len(HTML)):
        d += {"{": 1, "}": -1}.get(HTML[j], 0)
        if d == 0:
            return HTML[start:j + 1]


def test_real_data_box_break():
    s = A.analyze_abc(_df("091580.KQ"))["box_break"]
    assert s["ok"] is True and s["vol_mult"] == 2.42 and s["day_pct"] == 11.4
    p = A.analyze_abc(_df("003670.KS"))["box_break"]
    assert p["ok"] is False and p["vol_mult"] == 1.9 and p["day_pct"] >= 7    # 등락은 통과, 거래량 1.90배 < 2.0


def test_definition_uses_existing_constants_only():
    """새 상수 금지 — 강돌파 3개 + B 최대 길이(60) + 50일 평균만 cfg로 참조한다."""
    body = inspect.getsource(A._find_box_break)
    code = __import__("re").sub(r"#[^\n]*", "", body.split('"""')[2])           # 주석 제외(실행 코드만)
    assert set(__import__("re").findall(r'cfg\["(\w+)"\]', code)) == {
        "b_max_bars", "strong_window", "strong_day_pct", "strong_vol_mult", "gate_break_vol_avg"}
    assert (A.ABC_CONFIG["b_max_bars"], A.ABC_CONFIG["strong_window"], A.ABC_CONFIG["strong_day_pct"],
            A.ABC_CONFIG["strong_vol_mult"], A.ABC_CONFIG["gate_break_vol_avg"]) == (60, 20, 0.07, 2.0, 50)
    for lit in ("60", "20", "0.07", "2.0", "50", "7"):
        assert f" {lit}" not in code.replace("* 100", ""), f"리터럴 {lit}"


def _synthetic(after_close):
    """평평한 60봉 박스(고가 100) → 마지막 5봉 전에 +10%·거래량 3배로 101에 종가 돌파 → 이후 after_close."""
    n = 200
    close = [95.0] * n; high = [100.0 if i % 10 == 0 else 96.0 for i in range(n)]; vol = [1000.0] * n
    i = n - 5
    close[i - 1] = 92.0; close[i] = 101.2; high[i] = 102.0; vol[i] = 3000.0
    for k in range(i + 1, n):
        close[k] = after_close; high[k] = max(after_close, 96.0)
    return pd.Series(close), pd.Series(high), pd.Series(vol), i


def test_still_above_top_required():
    c, h, v, i = _synthetic(101.0)
    b = A._find_box_break(c, h, v, A.ABC_CONFIG)
    assert b["ok"] is True and b["bars_ago"] == 4 and b["top"] == 100.0 and b["vol_mult"] == 3.0
    c, h, v, _ = _synthetic(99.0)                               # 박스 안으로 되돌아옴
    assert A._find_box_break(c, h, v, A.ABC_CONFIG)["ok"] is False


def test_first_crossing_only():
    """창 안 첫 돌파봉만 본다 — 첫 돌파가 거래량 미달이면 나중 봉이 조건을 채워도 ok 아님."""
    c, h, v, i = _synthetic(101.0)
    v[i] = 1500.0
    c[i + 2], v[i + 2] = 112.0, 5000.0                           # 나중 봉(이미 박스 위)
    b = A._find_box_break(c, h, v, A.ABC_CONFIG)
    assert b["bars_ago"] == 4 and b["ok"] is False


def test_threshold_compares_raw_values_not_rounded():
    """경계: 거래량 1.996배는 표시가 2.0이어도 탈락, 등락 +7.01%는 표시가 7.0이어도 통과(10-06 KR 107640·432720 실례)."""
    c, h, v, i = _synthetic(101.0)
    v[i] = 1996.0
    b = A._find_box_break(c, h, v, A.ABC_CONFIG)
    assert b["vol_mult"] == 2.0 and b["ok"] is False
    c, h, v, i = _synthetic(101.0)
    c[i - 1] = 101.2 / 1.0701
    b = A._find_box_break(c, h, v, A.ABC_CONFIG)
    assert b["day_pct"] == 7.0 and b["ok"] is True


def test_grade_unchanged_on_real_data():
    assert A.analyze_abc(_df("091580.KQ"))["c_stage"] == A.STAGE_EXIT
    comp = {"ok": True}
    for t, want in (("091580.KQ", A.GRADE_C), ("003670.KS", A.GRADE_B)):
        r = A.analyze_abc(_df(t))
        assert A.grade(r, comp) == want


def test_grade_ignores_box_break():
    """같은 결과에서 box_break만 바꿔도 등급이 같다(모든 단계)."""
    base = A.analyze_abc(_df("003670.KS"))
    for stage in (A.STAGE_STRONG, A.STAGE_EXIT, base["c_stage"]):
        for comp in ({"ok": True}, {"ok": False}, {"ok": True, "fin_unknown": True}):
            r0 = {**copy.deepcopy(base), "c_stage": stage, "box_break": None}
            r1 = {**r0, "box_break": {"ok": True, "bars_ago": 0, "top": 1, "day_pct": 30, "vol_mult": 9}}
            assert A.grade(r0, comp) == A.grade(r1, comp)
    assert "box_break" not in inspect.getsource(A.grade)


def _node(expr, hits):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    views = [l for l in HTML.splitlines() if l.startswith("const ABC_GRADE_VIEWS = ")][0]
    src = ("const _escapeHtml = s => String(s);\n" + views
           + "\nlet _abcData, abcGradeFilter = 'all', abcStageFilter = 'all', abcQuery = '', abcSortTheme = false, abcBoxOnly = false;\n"
           + "\n".join(_src(n) for n in ("abcSearchMatch", "abcGradeInView", "abcSortByBreakout", "abcThemeUpMax", "abcSortByTheme",
                                         "abcFilteredHitsBase", "abcFilteredHits", "abcBoxBreakHtml"))
           + f"\n_abcData = {{hits: {json.dumps(hits, ensure_ascii=False)}}};\n")
    p = subprocess.run(["node", "-e", src + f"\nconsole.log(JSON.stringify({expr}));"], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


BB = lambda ok: {"ok": ok, "bars_ago": 2, "top": 1000, "day_pct": 11.4, "vol_mult": 2.42}
HITS = [{"ticker": "091580.KQ", "name": "상신이디피", "grade": "C급", "c_stage": "이탈", "box_break": BB(True)},
        {"ticker": "003670.KS", "name": "포스코퓨처엠", "grade": "B급", "c_stage": "약돌파", "box_break": BB(False)},
        {"ticker": "000001.KS", "name": "X", "grade": "A급", "c_stage": "🩷 강돌파", "box_break": None}]


def test_box_only_chip_filters():
    got = _node("(() => { const a = abcFilteredHits().map(h => h.ticker); abcBoxOnly = true; const b = abcFilteredHits().map(h => h.ticker);"
                " abcGradeFilter = 'A'; const c = abcFilteredHits().map(h => h.ticker); abcQuery = '포스코'; const d = abcFilteredHits().map(h => h.ticker);"
                " return [a.length, b, c, d]; })()", HITS)
    assert got == [3, ["091580.KQ"], [], ["003670.KS"]]       # 칩은 등급 보기와 AND, 검색은 칩 무시(v5.334 규칙 유지)


def test_box_break_cell():
    got = _node("[abcBoxBreakHtml(_abcData.hits[0]), abcBoxBreakHtml(_abcData.hits[1]), abcBoxBreakHtml(_abcData.hits[2])]", HITS)
    assert "📦 박스돌파 D+2" in got[0] and got[1] == "" and got[2] == ""
    assert HTML.count("${abcGateBreakHtml(h)}${abcBoxBreakHtml(h)}") == 1
    assert HTML.count("_abcChip('📦박스돌파만', abcBoxOnly, 'toggleAbcBoxOnly()')") == 1


# ── v5.336 강돌파(_find_gate_break)도 원값 비교 ─────────────────────────
def _gate_series(day_ratio, bar_vol):
    """700봉 평평(100, 거래량 1000) → 마지막 3봉 전 95로 눌렸다가 MA600을 종가로 첫 돌파."""
    n = 700
    close = [100.0] * n; vol = [1000.0] * n
    i = n - 3
    close[i - 1] = 95.0; close[i] = 95.0 * day_ratio; vol[i] = bar_vol
    for k in range(i + 1, n):
        close[k] = close[i]
    return pd.Series(close), pd.Series(vol), i


def test_gate_break_threshold_compares_raw_values():
    """사용자 지시: "경계 테스트(1.996배 탈락, +7.01% 통과) 추가"."""
    c, v, _ = _gate_series(1.08, 1996.0)
    g = A._find_gate_break(c, v, A.ABC_CONFIG)
    assert g["vol_mult"] == 2.0 and g["vol_ok"] is False and g["strong"] is False      # 표시 2.0이어도 1.996배는 탈락
    c, v, _ = _gate_series(1.0701, 3000.0)
    g = A._find_gate_break(c, v, A.ABC_CONFIG)
    assert g["day_pct"] == 7.0 and g["day_ok"] is True and g["strong"] is True        # 표시 7.0이어도 +7.01%는 통과
    c, v, _ = _gate_series(1.0699, 3000.0)
    assert A._find_gate_break(c, v, A.ABC_CONFIG)["day_ok"] is False                   # +6.99%는 탈락(표시도 7.0)
