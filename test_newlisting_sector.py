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
    assert got == {"111111.KQ": "2차전지", "NEWU": "응용 소프트웨어", "222222.KS": None}   # v5.362: 영문 industry는 한글로
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
function _newsAttr(v){return _escapeHtml(v).replace(/'/g,'&#39;');}
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


# ── v5.362 섹터 한글 · US 열 정렬 ────────────────────────────────────
def test_us_industry_ko_table_is_complete_and_korean():
    import re
    from us_industry_ko import US_INDUSTRY_KO
    assert len(US_INDUSTRY_KO) == 145, "Morningstar industry 145개"
    assert len(set(US_INDUSTRY_KO.values())) == len(US_INDUSTRY_KO), "한글 라벨 중복"
    for en, ko in US_INDUSTRY_KO.items():
        assert re.search(r"[가-힣]", ko), f"{en!r} → {ko!r} 한글 아님"
        assert "  " not in en and en == en.strip()


def test_nl_sector_translates_english_and_passes_korean(monkeypatch):
    monkeypatch.setattr(app, "_sector_of", lambda t: {
        "PMI": "Medical Devices", "X.KS": "자동차부품", "ODD": "Not A Real Industry", "Z": "기타"}.get(t, "기타"))
    assert app._nl_sector("PMI") == "의료기기"
    assert app._nl_sector("X.KS") == "자동차부품"
    assert app._nl_sector("ODD") == "Not A Real Industry"   # 표에 없으면 원문 유지(조용히 비우지 않는다)
    assert app._nl_sector("Z") is None


def test_table_cells_use_nowrap_and_name_clamp():
    html = _table([{**_ROW, "name": "Eastern International Ltd. - Ordinary Shares", "sector": "진단·연구"}], "US")
    assert 'class="nl-name"' in html and 'class="nl-title"' in html
    assert html.count('nowrap') >= 5, "상장일·개월수·종가·신고가·하락률 칸이 nowrap이어야 한다"
    css = SRC[SRC.index("<style"):SRC.index("</style>")]
    for rule in (".nl-table td.nl-name{max-width:320px}", "-webkit-line-clamp:2", ".nl-table td.nowrap,.nl-table th.nowrap{white-space:nowrap}"):
        assert rule in css, rule


# ── v5.363 US 업종 채우기 ───────────────────────────────────────────
@pytest.fixture
def fill_env(monkeypatch, tmp_path):
    import us_industry_cache as uic
    rows = [{"market": "US", "code": "PMI"}, {"market": "US", "code": "ELOG"}, {"market": "US", "code": "MNTN"},
            {"market": "KOSDAQ", "code": "111111.KQ"}]
    monkeypatch.setattr(app, "_lowpoint_read", lambda path: {"rows": rows} if path == app.NEWLISTING_DATA_PATH else None)
    cache = {"MNTN": "Software - Application"}
    asked = []

    def fake_build(tickers, throttle=0.5, save_every=50):
        asked.extend(tickers)
        cache.update({t: "Medical Devices" for t in tickers})
        return dict(cache)
    monkeypatch.setattr(uic, "load_cache", lambda: dict(cache))
    monkeypatch.setattr(uic, "build_cache", fake_build)
    monkeypatch.setattr(uic, "cache_path", lambda: str(tmp_path / "us_industry_cache.json"))
    monkeypatch.setattr(uic, "lock_path", lambda: str(tmp_path / "us_industry_cache.lock"))
    monkeypatch.setattr(app, "NEWLISTING_INDUSTRY_FILL_MARKER", str(tmp_path / "fill.marker"))
    reloaded = []
    monkeypatch.setattr(app, "_reload_us_industry_cache_if_updated", lambda: reloaded.append(1))
    return {"asked": asked, "cache": cache, "reloaded": reloaded, "tmp": tmp_path, "uic": uic}


def test_fill_requests_only_missing_us_tickers(fill_env):
    out = app._newlisting_fill_us_industry()
    assert fill_env["asked"] == ["ELOG", "PMI"], "캐시에 없는 US만, KR 제외"
    assert out["us_rows"] == 3 and out["missing"] == 2 and out["asked"] == 2 and out["filled"] == 2
    assert fill_env["reloaded"] == [1]
    assert (fill_env["tmp"] / "fill.marker").exists() and app._nl_fill_marker_is_today()
    assert not (fill_env["tmp"] / "us_industry_cache.lock").exists(), "락 해제"


def test_fill_respects_cap(fill_env):
    out = app._newlisting_fill_us_industry(max_n=1)
    assert fill_env["asked"] == ["ELOG"] and out["asked"] == 1 and out["missing"] == 2


def test_fill_skips_when_locked(fill_env):
    fill_env["uic"].acquire_lock()
    out = app._newlisting_fill_us_industry()
    assert out["skipped"] == "locked" and fill_env["asked"] == []
    assert not (fill_env["tmp"] / "fill.marker").exists(), "건너뛴 날은 마커를 안 찍어 다음 틱에 다시 시도"


def test_fill_nothing_missing_still_marks(fill_env):
    fill_env["cache"].update({"PMI": "x", "ELOG": "y"})
    out = app._newlisting_fill_us_industry()
    assert out["missing"] == 0 and fill_env["asked"] == [] and app._nl_fill_marker_is_today()


def test_newlisting_job_calls_fill(monkeypatch):
    import types
    calls = []
    monkeypatch.setattr(app, "_newlisting_fill_us_industry", lambda max_n=300: calls.append(1) or {"filled": 0})
    fake_nl = types.SimpleNamespace(
        build=lambda ref, **kw: {"ref_date": "2026-10-31", "rows": [], "counts": {}},
        write_publish=lambda entry, path=None: None)
    import sys as _sys
    monkeypatch.setitem(_sys.modules, "newlisting", fake_nl)
    monkeypatch.setattr(app, "_lowpoint_last_slot", lambda tf, now: (now, "2026-10-31"))
    from datetime import datetime
    out = app._newlisting_job_blocking("newlisting", datetime(2026, 11, 1, 10, 0))
    assert calls == [1] and "industry_fill" in out
