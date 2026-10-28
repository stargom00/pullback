"""v5.357 — 신규상장 표 종목명 옆 섹터(사용자 지시 2026-10-28 "종목 이름옆에 섹터를 표시하고싶어").

서버: `_newlisting_view()`가 응답 시점에 행마다 `sector`를 붙인다(`_sector_of` 그대로, "기타"→None).
스크린 결과 파일은 불변 — 저장된 행에 sector가 없어도 응답에는 있어야 한다.
프론트: `_nlTable`(production 코드 그대로를 Node로 실행)이 sector가 있으면 종목명 옆에 넣고, null이면 뺀다.
재구현 금지 원칙(CLAUDE.md "텍스트 추출 + Node 실행")을 따른다.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import app  # noqa: E402

SRC = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()


def _fn(name):
    start = SRC.index(f"function {name}(")
    i = SRC.index("{", SRC.index(")", start))
    d = 0
    for j in range(i, len(SRC)):
        d += {"{": 1, "}": -1}.get(SRC[j], 0)
        if d == 0:
            return SRC[start:j + 1]
    raise AssertionError(name)


# ── 서버 ──────────────────────────────────────────────────────────────
def _view_with(monkeypatch, rows, sector_map):
    entry = {"ref": "2026-09-30", "rows": rows, "counts": {}}
    monkeypatch.setattr(app, "_lowpoint_read", lambda path: entry if path == app.NEWLISTING_DATA_PATH else None)
    monkeypatch.setattr(app, "_lowpoint_load_state", lambda: {})
    monkeypatch.setattr(app, "_sector_of", lambda t: sector_map.get(t, "기타"))
    return app._newlisting_view()


def test_view_attaches_sector_and_nulls_unmapped(monkeypatch):
    rows = [{"market": "KOSDAQ", "code": "111111.KQ", "name": "가"},
            {"market": "US", "code": "NEWU", "name": "n"},
            {"market": "KOSPI", "code": "222222.KS", "name": "나"}]
    out = _view_with(monkeypatch, rows, {"111111.KQ": "2차전지", "NEWU": "Software - Application"})
    got = {r["code"]: r["sector"] for r in out["rows"]}
    assert got == {"111111.KQ": "2차전지", "NEWU": "Software - Application", "222222.KS": None}
    # 저장된 행(entry)은 그대로 — 파일에 sector를 써넣지 않는다
    assert all("sector" not in r for r in rows)


def test_view_keeps_other_fields_and_source(monkeypatch):
    rows = [{"market": "US", "code": "A", "name": "a", "months": 15, "drawdown_pct": -40.0}]
    out = _view_with(monkeypatch, rows, {})
    r = out["rows"][0]
    assert (r["months"], r["drawdown_pct"], r["sector"]) == (15, -40.0, None)
    assert out["source"] == "data" and out["ok"] is True


def test_nl_sector_helper_edge_cases(monkeypatch):
    monkeypatch.setattr(app, "_sector_of", lambda t: {"X": "반도체-장비"}.get(t, "기타"))
    assert app._nl_sector("X") == "반도체-장비"
    assert app._nl_sector("Y") is None
    assert app._nl_sector(None) is None
    assert app._nl_sector("") is None


# ── 프론트 ────────────────────────────────────────────────────────────
_STUBS = """
const _nl = {sortKey: 'months', sortDir: 'asc'};
function _escapeHtml(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function tvUrl(c, m){return 'tv://' + c;}
function _lptFmt(v, m){return String(v);}
function _lptPct(v){return v==null?'—':String(v)+'%';}
function nlToggleDdSort(){}
"""


def _table(rows, mkt):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    js = _STUBS + _fn("_nlTable") + f"\nconsole.log(JSON.stringify(_nlTable({json.dumps(rows)}, {json.dumps(mkt)})));"
    p = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


_ROW = {"code": "111111.KQ", "name": "가나다", "market": "KOSDAQ", "listed": "2025-08-14", "months": 14,
        "close": 1000, "close_date": "2026-09-30", "peak_close": 2000, "peak_close_date": "2026-01-05",
        "ref_close": 1000, "ref_close_date": "2026-09-30", "drawdown_pct": -50.0}


def test_table_shows_sector_next_to_name():
    html = _table([{**_ROW, "sector": "2차전지"}], "KR")
    name_end = html.index("가나다</a></b>")
    sec = html.index('class="jr-s nl-sector"')
    code_line = html.index('class="jr-s num"')
    assert name_end < sec < code_line, "섹터는 종목명 바로 뒤, 코드 줄 앞"
    assert ">2차전지</span>" in html


def test_table_omits_sector_when_null():
    for v in (None, ""):
        html = _table([{**_ROW, "sector": v}], "KR")
        assert "nl-sector" not in html
        assert "가나다</a></b>" in html
    html = _table([dict(_ROW)], "KR")     # 키 자체가 없는 옛 응답도 그대로 그려진다
    assert "nl-sector" not in html and "가나다</a></b>" in html


def test_table_escapes_sector_text():
    html = _table([{**_ROW, "sector": "<b>x</b>"}], "KR")
    assert "&lt;b&gt;x&lt;/b&gt;" in html and "<b>x</b>" not in html


# ── v5.359 줄간격 ───────────────────────────────────────────────────
def test_table_has_nl_table_class_and_css():
    html = _table([dict(_ROW)], "KR")
    assert 'class="jr-table lpt-table nl-table"' in html
    css = SRC[SRC.index("<style"):SRC.index("</style>")]
    for rule in (".nl-table td{padding:7px 12px}", ".nl-table th{padding:8px 12px}", ".nl-table .jr-s{margin-top:1px}"):
        assert css.count(rule) == 1, rule
        # 다른 규칙 안에 끼어 있지 않아야 한다(v5.356 .news-why 사고와 같은 유형)
        depth = css[:css.index(rule)].count("{") - css[:css.index(rule)].count("}")
        assert depth == 0, f"{rule} 가 depth {depth}에 중첩"
