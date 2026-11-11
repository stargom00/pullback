"""v5.380 — 홈 브리핑(사용자 지시 2026-11-12 "눌림목페이지 이렇게 수정할수있지? 한눈에 보기 쉽네 이게").

서버: GET /api/brief — yfinance 9개 심볼 + CNN 공포탐욕. 일부 실패해도 나머지는 살고, 소스 상태를 응답에 남긴다.
프론트: 판정이 갈리는 순수 함수(briefDateLabel·briefChgHtml·fgLabel·briefWeekEvents·homeSectorGridHtml)를
production 코드 그대로 Node로 실행한다(CLAUDE.md "텍스트 추출 + Node 실행", 재구현 금지).
"""
import asyncio
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


def _const(name):
    start = SRC.index(f"const {name} =")
    return SRC[start:SRC.index(";\n", start) + 1]


def _node(code):
    if not shutil.which("node"):
        pytest.skip("node 없음")
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# ── 서버 ──────────────────────────────────────────────────────────────
def _run_brief(monkeypatch, yf_fn, fg_fn):
    app._brief_cache.clear()
    monkeypatch.setattr(app, "_fetch_yf_index", yf_fn)
    monkeypatch.setattr(app, "_fetch_fear_greed", fg_fn)
    resp = asyncio.run(app.brief())
    app._brief_cache.clear()
    return json.loads(resp.body)


def test_brief_all_ok(monkeypatch):
    out = _run_brief(monkeypatch,
                     lambda sym, label, dec=2: {"name": label, "value": 1.0, "change": 0.1, "change_pct": 1.0, "date": "2026-11-11"},
                     lambda: {"score": 37, "previous_close": 33, "rating": "fear", "timestamp": None})
    assert set(out["items"]) == set(app.BRIEF_SYMBOLS)
    assert all(isinstance(v, dict) for v in out["items"].values())
    assert out["sources"] == {"yahoo": "ok", "yahoo_missing": [], "fear_greed": "ok"}
    assert out["fear_greed"]["score"] == 37


def test_brief_partial_failure_keeps_rest_and_reports(monkeypatch):
    def yf(sym, label, dec=2):
        if sym in ("^TNX", "KRW=X"):
            raise RuntimeError("blocked")
        return None if sym == "^VIX" else {"name": label, "value": 2.0, "change": 0, "change_pct": 0, "date": "2026-11-11"}

    def fg():
        raise ConnectionError("cnn down")
    out = _run_brief(monkeypatch, yf, fg)
    assert sorted(out["sources"]["yahoo_missing"]) == ["us10y", "usdkrw", "vix"]
    assert out["sources"]["yahoo"] == f"partial {len(app.BRIEF_SYMBOLS) - 3}/{len(app.BRIEF_SYMBOLS)}"
    assert out["items"]["dow"]["value"] == 2.0 and out["items"]["us10y"] is None
    assert out["fear_greed"] is None and out["sources"]["fear_greed"].startswith("fail: ConnectionError")


def test_brief_all_yahoo_fail_is_fail(monkeypatch):
    out = _run_brief(monkeypatch, lambda *a: None, lambda: {"score": 50, "previous_close": None, "rating": None, "timestamp": None})
    assert out["sources"]["yahoo"] == "fail"


def test_parse_fear_greed_shape():
    j = {"fear_and_greed": {"score": 37.4, "rating": "fear", "previous_close": 33.2, "timestamp": "t"}}
    assert app._parse_fear_greed(j) == {"score": 37, "previous_close": 33, "rating": "fear", "timestamp": "t"}
    with pytest.raises(ValueError):
        app._parse_fear_greed({"fear_and_greed": {}})


def test_brief_is_behind_login_gate():
    """봇 읽기 경로·토큰 우회 경로에 들어가면 안 된다(브라우저 세션 전용)."""
    assert "/api/brief" not in getattr(app, "_SYNC_TOKEN_GATED_PATHS", set())
    assert "/api/brief" not in getattr(app, "_BOT_READ_EXACT_PATHS", set())


# ── 프론트 ─────────────────────────────────────────────────────────────
def test_date_label():
    out = _node(_const("BRIEF_DOW") + _fn("briefDateLabel") + """
console.log(JSON.stringify([briefDateLabel('2026-11-11'), briefDateLabel('2026-11-13'), briefDateLabel(null), briefDateLabel('11/11')]));""")
    assert out == ["11/11(수)", "11/13(금)", "", ""]


def test_chg_html_pct_and_pp():
    out = _node(_fn("briefChgHtml") + """
console.log(JSON.stringify([
  briefChgHtml({change_pct: 1.31, change: 99}),
  briefChgHtml({change_pct: -0.55}),
  briefChgHtml({change: 0.02, change_pct: 0.4}, 'pp'),
  briefChgHtml({change_pct: 0}),
  briefChgHtml(null)]));""")
    assert out[0] == '<span class="bf-chg up">▲1.31%</span>'
    assert out[1] == '<span class="bf-chg down">▼0.55%</span>'
    assert out[2] == '<span class="bf-chg up">▲0.02%p</span>'     # 금리는 change(%p)를 쓴다
    assert out[3] == '<span class="bf-chg flat">0.00%</span>'
    assert out[4] == '<span class="bf-chg flat">–</span>'


def test_fg_label_rating_then_bands():
    out = _node(_const("FG_RATING_KO") + _fn("fgLabel") + """
const r = [ {score:37, rating:'fear'}, {score:90, rating:'Extreme Greed'},
  {score:24}, {score:25}, {score:44}, {score:45}, {score:55}, {score:56}, {score:75}, {score:76}, null, {score:null} ].map(fgLabel);
console.log(JSON.stringify(r));""")
    assert out == ["공포", "극단적 탐욕", "극단적 공포", "공포", "공포", "중립", "중립", "탐욕", "탐욕", "극단적 탐욕", None, None]


def test_week_events_today_and_rest_of_week():
    out = _node(_fn("briefWeekEvents") + """
const ev = [{date:'2026-11-12', html:'a'}, {date:'2026-11-15', html:'sun'}, {date:'2026-11-13', html:'ppi'},
            {date:'2026-11-16', html:'nextmon'}, {date:'2026-11-11', html:'yesterday'}, {html:'nodate'}];
const a = briefWeekEvents(ev, '2026-11-12');           // 목
const b = briefWeekEvents(ev, '2026-11-15');           // 일 — 이번 주 남은 날 없음
console.log(JSON.stringify([a.today.map(e=>e.html), a.week.map(e=>e.html), b.today.map(e=>e.html), b.week.map(e=>e.html)]));""")
    assert out == [["a"], ["ppi", "sun"], ["sun"], []]


def test_sector_grid_us_ticker_kr_name_and_cap():
    stub = "const _escapeHtml = s => String(s); const tvUrl = t => 'tv:' + t.ticker; const HOME_SECTOR_MAX = 12;"
    out = _node(stub + _fn("homeSectorGridHtml") + """
const rows = Array.from({length: 14}, (_, i) => ({sector: 'S' + i, n: 3, up_pct: 50, avg_chg: 1 - i,
  top: [{ticker: 'MU', name: 'Micron', chg: 11.98}, {ticker: '000660.KS', name: 'SK하이닉스', chg: -2.5}]}));
const us = homeSectorGridHtml(rows, true, 12), kr = homeSectorGridHtml(rows.slice(0,1), false, 12);
console.log(JSON.stringify([ (us.match(/class="bf-sbox"/g)||[]).length, us.includes('>MU<'), us.includes('>Micron<'),
  kr.includes('>Micron<'), kr.includes('▼2.50%'), homeSectorGridHtml([], true, 12) ]));""")
    assert out[0] == 12                    # 섹터 12개까지만
    assert out[1] is True and out[2] is False  # 미국은 티커
    assert out[3] is True and out[4] is True   # 한국은 종목명, 하락은 ▼
    assert "섹터 데이터 없음" in out[5]


def test_home_wiring():
    """브리핑·섹터가 실제로 불려야 한다 — 로더가 지수 갱신 성공·실패 두 경로 모두에서 호출."""
    li = _fn("loadIndices")
    assert li.count("loadBrief()") >= 2 and li.count("loadHomeSectors()") >= 2
    rt = _fn("renderHomeTiles")
    for sec in ("'미국 증시'", "'국내 증시 · 환율'", "'금리 · 원자재 · 심리'", "'오늘 · 이번 주 일정'"):
        assert rt.count(sec) == 1, sec
    assert rt.count("indexGateOf(") == 1 and "GATE_LABEL[" in rt     # 게이트 점·종합 게이트 유지
    assert '<div id="homeSectorBox"></div>' in SRC
    assert SRC.count('id="homeTiles"') == 1


def test_sector_rows_do_not_use_global_sr_class():
    """전역 .sr은 스크린리더 전용(1px 숨김) — v5.380 첫 렌더에서 섹터 종목 줄이 전부 숨었다(Playwright 확인)."""
    g = _fn("homeSectorGridHtml")
    assert 'class="sr"' not in g and 'class="sh"' not in g
    assert g.count('class="bf-sr"') == 1 and g.count('class="bf-sh"') == 1
