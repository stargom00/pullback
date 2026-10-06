"""v5.334 — ABC 탭 검색칸 · 테마 동반 표시(표시 전용 — 등급·판정 불변).

사용자 지시: "ABC 탭 상단에 검색칸: 종목명 또는 코드 일부로 필터. 검색어가 있으면 등급 칩·C단계 칩 설정과 상관없이 전체에서
찾는다 … 검색어를 지우면 원래 칩 상태로" · "ABC 행에 테마 정보 추가: themes_kr.json 테마명 … '테마 동반': 같은 테마 종목 중
당일 +5% 이상 오른 종목 수 / 테마 전체 종목 수 … 테마 동반 수 내림차순 정렬 옵션(기본 정렬은 그대로) … 테마에 없는 종목은 —".
실데이터 픽스처 test_fixtures/abc_theme_20261006.json.gz(2026-10-07 수집, naver 1900일 → app._downcast): 포스코퓨처엠
(003670.KS)·상신이디피(091580.KQ) 전체 일봉 + themes_kr.json "이차전지" 종목들의 마지막 3봉.

사보타주 확인(2026-10-07, FAIL 확인 후 원복):
① 검색이 칩 필터를 따르게(등급 보기로 거름) → test_search_ignores_chips FAIL
② 동반 수 계산에 당일 대신 전일 등락률 사용 → test_day_change_is_last_bar·test_api_rows_carry_themes_on_real_data FAIL
"""
from __future__ import annotations

import asyncio
import gzip
import json
import os
import re
import shutil
import subprocess

import pandas as pd
import pytest

import abc_screener as A
import app

ROOT = os.path.dirname(os.path.abspath(__file__))
HTML = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
FX = json.load(gzip.open(os.path.join(ROOT, "test_fixtures", "abc_theme_20261006.json.gz"), "rt", encoding="utf-8"))


def _df(t):
    f = FX["frames"][t]
    return pd.DataFrame({c: f[c] for c in ("Open", "High", "Low", "Close", "Volume")}, index=pd.to_datetime(f["date"])).astype("float64")


def _fn(name):
    start = HTML.index(f"function {name}(")
    i = HTML.index("{", HTML.index(")", start))
    d = 0
    for j in range(i, len(HTML)):
        d += {"{": 1, "}": -1}.get(HTML[j], 0)
        if d == 0:
            return HTML[start:j + 1]


def _node(expr, pre):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    views = [l for l in HTML.splitlines() if l.startswith("const ABC_GRADE_VIEWS = ")][0]
    src = ("const _escapeHtml = s => String(s);\n" + views + "\nlet _abcData, abcGradeFilter = 'A', abcStageFilter = 'all', abcQuery = '', abcSortTheme = false, abcBoxOnly = false;\n"
           + "\n".join(_fn(n) for n in ("abcSearchMatch", "abcGradeInView", "abcSortByBreakout", "abcThemeUpMax", "abcSortByTheme",
                                        "abcFilteredHitsBase", "abcFilteredHits", "abcThemesHtml")) + "\n" + pre)
    p = subprocess.run(["node", "-e", src + f"\nconsole.log(JSON.stringify({expr}));"], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


HITS = [
    {"ticker": "000001.KS", "name": "A종목", "grade": "A급", "c_stage": "🩷 강돌파", "gate_break": {"bars_ago": 3},
     "themes": [{"theme": "반도체", "up": 2, "total": 4, "no_data": 0}]},
    {"ticker": "003670.KS", "name": "포스코퓨처엠", "grade": "B급", "c_stage": "약돌파",
     "themes": [{"theme": "이차전지", "up": 14, "total": 23, "no_data": 1}]},
    {"ticker": "091580.KQ", "name": "상신이디피", "grade": "C급", "c_stage": "이탈", "themes": []},
    {"ticker": "000002.KS", "name": "A보류", "grade": "A급 보류", "c_stage": "🩷 강돌파", "gate_break": {"bars_ago": 1}, "themes": []},
]
PRE = f"_abcData = {{hits: {json.dumps(HITS, ensure_ascii=False)}}};\n"


# ── 검색 ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("q,want", [("퓨처", ["003670.KS"]), ("091580", ["091580.KQ"]), (".kq", ["091580.KQ"]),
                                    ("상신", ["091580.KQ"]), ("3670", ["003670.KS"]), ("없는종목", [])])
def test_search_ignores_chips(q, want):
    """등급 칩이 A급만이어도 B·C급 종목이 찾아진다(검색어가 있으면 칩 무시)."""
    got = _node(f"(() => {{ abcGradeFilter = 'A'; abcStageFilter = '🩷 강돌파'; abcQuery = {json.dumps(q, ensure_ascii=False)}; return abcFilteredHits().map(h => [h.ticker, h.grade]); }})()", PRE)
    assert [t for t, _ in got] == want
    assert all(g in ("A급", "B급", "C급", "A급 보류") for _, g in got)          # 행에 등급이 그대로 실린다(표시)


def test_clearing_query_restores_chip_state():
    got = _node("(() => { abcGradeFilter = 'A'; abcQuery = '퓨처'; const a = abcFilteredHits().map(h => h.ticker);"
                " abcQuery = ''; const b = abcFilteredHits().map(h => h.ticker); return [a, b, abcGradeFilter]; })()", PRE)
    assert got == [["003670.KS"], ["000002.KS", "000001.KS"], "A"]


def test_search_ui_wiring():
    body = _fn("renderAbcPage")
    assert 'id="abcSearch"' in body and 'oninput="abcSetQuery(this.value)"' in body
    s = _fn("abcSetQuery")
    assert "abcQuery = v;" in s and "el.focus()" in s and "abcGradeFilter" not in s and "abcStageFilter" not in s   # 칩 상태는 안 건드림
    assert "${h.grade}" in _fn("abcRowHtml")                                      # 검색 결과 행에도 등급 칸


# ── 테마 동반 ──────────────────────────────────────────────────────
def test_day_change_is_last_bar():
    assert A.day_change_pct(_df("003670.KS")) == 11.88           # 10-06 209,000 ÷ 10-02 186,800 − 1
    assert A.day_change_pct(_df("091580.KQ")) == 11.36
    assert A.day_change_pct(_df("003670.KS").iloc[:1]) is None
    assert A.ABC_CONFIG["theme_up_pct"] == 5.0


def test_theme_companions_pure():
    th = {"T1": ["a", "b", "c", "d"], "T2": ["a", "x"]}
    ch = {"a": 5.0, "b": 4.99, "c": None, "d": 12.0, "x": -1.0}
    got = A.theme_companions(th, ch)
    assert got["a"] == [{"theme": "T1", "up": 2, "total": 4, "no_data": 1}, {"theme": "T2", "up": 1, "total": 2, "no_data": 0}]
    assert "zzz" not in got


def test_api_rows_carry_themes_on_real_data(monkeypatch):
    """10-06 실데이터: 포스코퓨처엠·상신이디피 = 이차전지 15/24(v5.335 사용자 지시로 상신이디피를 이차전지에 추가 — 그 전엔
    14/23·테마 없음). 등락 모름 1 — 유니버스 밖."""
    data = {t: _df(t) for t in FX["frames"]}
    bundle = {"data": data, "universe": FX["names"], "sector_info": {}, "ts": None}
    monkeypatch.setattr(app, "_peek_market_bundle", lambda m: bundle)
    fin = {"rev_yoy_pos": 1, "rev_yoy_of": 1, "eps_pos_q": 2, "reason": None}
    monkeypatch.setattr(app, "_abc_earnings_peek", lambda t: fin)
    monkeypatch.setattr(app, "_abc_quarterly_axes", lambda t: fin)
    monkeypatch.setattr(app, "_load_abc_flags", lambda: {})

    async def no_flow(ts):
        return {}
    monkeypatch.setattr(app, "_flow_fill", no_flow)
    d = asyncio.run(app.api_abc())
    d = json.loads(d.body) if hasattr(d, "body") else d
    by = {h["ticker"]: h for h in d["hits"]}
    assert by["003670.KS"]["themes"] == [{"theme": "이차전지", "up": 15, "total": 24, "no_data": 1}]
    assert by["091580.KQ"]["themes"] == by["003670.KS"]["themes"]
    assert (by["003670.KS"]["grade"], by["091580.KQ"]["grade"]) == ("B급", "C급")   # 등급 불변(표시 전용)
    html = _node(f"[abcThemesHtml({json.dumps(by['003670.KS']['themes'], ensure_ascii=False)}), abcThemesHtml([])]", "")
    assert "이차전지 <b class=\"num\">15/24</b>" in html[0] and ">—<" in html[1]
    # 📦 박스돌파(v5.335): 상신이디피 표시(거래량 2.42배), 포스코퓨처엠 미표시(1.90배 < 2.0)
    assert by["091580.KQ"]["box_break"]["ok"] is True and by["091580.KQ"]["box_break"]["vol_mult"] == 2.42
    assert by["003670.KS"]["box_break"]["ok"] is False and by["003670.KS"]["box_break"]["vol_mult"] == 1.9


def test_theme_sort_option_keeps_default():
    got = _node("(() => { abcGradeFilter = 'all'; const d = abcFilteredHits().map(h => h.ticker); abcSortTheme = true;"
                " const t = abcFilteredHits().map(h => h.ticker); return [d, t]; })()", PRE)
    assert got[0] == ["000002.KS", "000001.KS", "003670.KS", "091580.KQ"]          # 기본 = D+ 순 그대로
    assert got[1] == ["003670.KS", "000001.KS", "000002.KS", "091580.KQ"]          # 동반 14 > 2 > 테마 없음(들어온 순)
    assert "let abcSortTheme = false;" in HTML


def test_theme_display_not_used_in_grade():
    import inspect
    g = inspect.getsource(A.grade).split('"""')[2]
    assert "theme" not in g and "themes" not in inspect.getsource(A.analyze_abc)
