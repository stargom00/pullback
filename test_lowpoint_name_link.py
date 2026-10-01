"""v5.312 — 저점 탭 보유·종료 목록의 종목명 → 트레이딩뷰 링크.

핵심은 "**다른 탭과 같은 URL**"이다. 그래서 href 문자열을 하드코딩해 비교하지 않고,
production의 `tvUrl()`을 그대로 실행해 **값끼리 대조**한다(링크 규칙이 나중에 바뀌어도
두 곳이 같이 바뀌면 통과, 저점 탭만 갈라지면 FAIL — CLAUDE.md "텍스트 추출 + Node 실행").

사보타주 확인(2026-10-02, FAIL 확인 후 원복):
① `_lptNameLink`에 URL 사본(`'https://www.tradingview.com/chart/?symbol=' + ...`) 주입
   → test_href_matches_other_tabs_kr / _us 2건 FAIL
② 종료 행의 `_lptNameLink(r)`를 옛 `<b>이름</b>`으로 되돌림 → test_closed_rows_have_link FAIL
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

IDX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html")
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node 미설치")
SRC = open(IDX, encoding="utf-8").read()


def _extract(name: str) -> str:
    start = SRC.index(f"function {name}(")
    i = SRC.index("{", start)
    depth = 0
    for j in range(i, len(SRC)):
        if SRC[j] == "{":
            depth += 1
        elif SRC[j] == "}":
            depth -= 1
            if depth == 0:
                return SRC[start:j + 1]
    raise AssertionError(f"{name}: 닫는 중괄호를 못 찾음")


def _layout_const() -> str:
    line = [l for l in SRC.splitlines() if l.strip().startswith("const TV_LAYOUT_ID")]
    assert len(line) == 1
    return line[0]


def _node(expr: str, *fns: str):
    src = (_layout_const() + "\n"
           + "function _escapeHtml(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')"
             ".replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
           + "\n".join(_extract(f) for f in fns)
           + f"\nconsole.log(JSON.stringify({expr}));")
    p = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


FNS = ("tvSymbolUrl", "tvUrl", "_lptNameLink")
KR = {"code": "042000.KQ", "name": "카페24", "mkt": "KR"}
US = {"code": "EVLV", "name": "Evolv Technologies", "mkt": "US"}


def _href(rec):
    """저점 탭이 만든 종목명 링크의 href."""
    html = _node(f"_lptNameLink({json.dumps(rec, ensure_ascii=False)})", *FNS)
    m = re.search(r'href="([^"]+)"', html)
    assert m, f"href가 없다: {html}"
    return m.group(1), html


# ── 다른 탭과 동일한 href인가(값 대조) ──────────────────────────────

def test_href_matches_other_tabs_kr():
    """KR: 저점 탭 href == 다른 탭이 쓰는 tvUrl(코드, 'KR') 결과."""
    got, _ = _href(KR)
    expect = _node(f"tvUrl({json.dumps(KR['code'])}, 'KR')", "tvSymbolUrl", "tvUrl")
    assert got == expect, "저점 탭 링크가 다른 탭과 다르다"
    assert "KRX%3A042000" in got, f"KR은 KRX:코드 관례여야 한다: {got}"


def test_href_matches_other_tabs_us():
    got, _ = _href(US)
    expect = _node(f"tvUrl({json.dumps(US['code'])}, 'US')", "tvSymbolUrl", "tvUrl")
    assert got == expect
    assert "symbol=EVLV" in got, f"US는 심볼 그대로여야 한다: {got}"


def test_href_also_matches_card_object_form():
    """카드가 쓰는 객체 형태(tvUrl({ticker, market}))와도 같은 결과여야 한다."""
    got, _ = _href(KR)
    expect = _node("tvUrl({ticker:'042000.KQ', market:'KR'})", "tvSymbolUrl", "tvUrl")
    assert got == expect


def test_no_url_copy_in_lowpoint_link():
    """저점 탭이 URL을 직접 조립하지 않는지 — tvUrl을 거쳐야 한다."""
    fn = _extract("_lptNameLink")
    assert "tvUrl(" in fn
    assert "tradingview.com" not in fn, "URL 사본이 생겼다(tvSymbolUrl 한 곳 원칙 위반)"


# ── 링크 속성·스타일 ────────────────────────────────────────────────

def test_link_attributes_and_style():
    _, html = _href(KR)
    assert 'class="name-link"' in html, "다른 탭과 같은 .name-link 스타일을 써야 한다"
    assert 'target="_blank"' in html and 'rel="noopener"' in html
    assert "트레이딩뷰" in html                      # title 안내
    assert html.startswith("<b>") and html.endswith("</b>")   # 기존 굵은 글씨 유지
    assert "style=" not in html, "인라인 색/밑줄을 넣으면 다른 탭과 달라진다"


def test_name_link_class_has_no_default_underline():
    """`.name-link`는 기본 상태에서 색 상속·밑줄 없음(파란 밑줄 노출 금지)."""
    rule = [l for l in SRC.splitlines() if l.startswith(".name-link{")]
    assert len(rule) == 1
    assert "color:inherit" in rule[0] and "text-decoration:none" in rule[0]


def test_record_without_code_has_no_link():
    html = _node('_lptNameLink({name:"이름만", mkt:"KR"})', *FNS)
    assert "<a" not in html and "이름만" in html


def test_escapes_name():
    html = _node('_lptNameLink({code:"AAA", name:"<script>x</script>", mkt:"US"})', *FNS)
    assert "<script>" not in html and "&lt;script&gt;" in html


# ── 두 목록 모두 적용 + 다른 셀 불변 ────────────────────────────────

def test_both_lists_use_the_shared_link():
    """보유·종료 행이 같은 함수를 쓴다(사본 금지) — 정의 1개 + 호출 2개."""
    assert SRC.count("function _lptNameLink(") == 1
    # 정의 줄(`function _lptNameLink(r) {`)도 같은 문자열을 포함하므로 **템플릿 호출 형태**로 센다
    assert SRC.count("${_lptNameLink(r)}") == 2, "보유·종료 두 곳에서 호출해야 한다"


def test_closed_rows_have_link():
    """종료 목록 행에도 링크가 있는지 — 분할(`· 분할`) 표기가 있는 행이 종료 행이다."""
    rows = [l for l in SRC.splitlines() if "${_lptNameLink(r)}" in l]
    assert len(rows) == 2
    assert any("분할" in l for l in rows), "종료 행(분할 표기 있는 행)에 링크가 없다"


def test_other_cells_and_buttons_untouched():
    """종목명 외 셀·버튼은 그대로."""
    assert 'onclick="lptStartSell(' in SRC and 'onclick="lptDelete(' in SRC
    assert "_lptFmt(r.buyPrice, r.mkt)" in SRC
    assert "_lptCode(r)" in SRC                     # 코드·시장 보조줄 유지


def _app_version() -> str:
    """app.py의 VERSION 리터럴 — 배지와 **대조**한다. 버전 문자열을 테스트에
    하드코딩하면 다음 버전마다 이 테스트가 깨진다(2026-10-02 v5.312에서 실제로 깨졌다)."""
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py"),
               encoding="utf-8").read()
    m = re.search(r'^VERSION = "(v[\d.]+)"', src, re.M)
    assert m, "app.py VERSION을 못 찾음"
    return m.group(1)


def test_version_badge_matches_app_version():
    m = re.search(r'id="verBadge"[^>]*>(v[\d.]+)<', SRC)
    assert m and m.group(1) == _app_version()
