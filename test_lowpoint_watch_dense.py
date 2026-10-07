"""v5.326 — 저점 관찰 표 압축 · +5% 진행 바 · 정렬.

사용자 지시: "관찰 페이지 행 높이·열 간격이 넓어 66종목이 한눈에 안 들어옴. 표 밀도를 높이고 '+5%까지 얼마나 왔나'를
시각화." 진행 바: 기준가(0%)가 가운데, 오른쪽 절반 = 0 → +5%(상승색으로 채움), 왼쪽 절반 = 0 → −5%(하락색).
정렬: 등락% 내림차순 기본, 헤더 클릭으로 등락%/경과일 전환. 그룹 헤더 "10-02 주봉 · 활성 N / 도달 M".

사보타주 확인(2026-10-05, FAIL 확인 후 원복):
① lpwBar 폭 상한(목표% 넘으면 꽉 참) 제거 → test_bar_position_boundaries FAIL
② v5.326 CSS 블록에 전역 규칙(table.jr-table td{padding:2px}) 추가 → test_new_css_is_scoped_to_watch FAIL
③ 기본 정렬 방향을 asc로 → test_render_default_sort_and_group_counts FAIL
④ 그룹 헤더의 도달 건수를 활성 건수로 → test_render_default_sort_and_group_counts FAIL
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
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


def _state_line():
    m = re.search(r"^const _lpw = \{.*\};$", SRC, re.M)
    assert m, "_lpw 상태 선언"
    return m.group(0).replace("const _lpw", "var _lpw")


FNS = ("lpwBar", "lpwSortRows", "lpwDays", "lpwGroups", "lpReturnPct", "lpDisplayName", "_lptFmt", "_lptPct",
       "_lptCode", "lpwPendingHtml", "lpwEndedLabel", "lpwDepHigh", "lpwRestPos", "lpwVolRatio", "lpwStageSplit",
       "lpwShapeText", "renderLowpointWatch")


def _js(expr, recs=None, extra=""):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    pre = ("const _escapeHtml = s => String(s);\nconst kstStr = () => '2026-10-07';\nconst tvUrl = (c, m) => 'tv:' + c;\n"
           + _state_line() + "\n" + "\n".join(_fn(f) for f in FNS) + "\n"
           + (f"_lpw.recs = {json.dumps(recs)}; _lpw.reachPct = 5;\n" if recs is not None else "") + extra)
    p = subprocess.run(["node", "-e", pre + f"\nconsole.log(JSON.stringify({expr}));"], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@pytest.mark.parametrize("pct,side,width", [
    (-10, "neg", 100), (-5, "neg", 100), (-2.5, "neg", 50), (0, "pos", 0), (2.5, "pos", 50), (4.5, "pos", 90),
    (5, "pos", 100), (7, "pos", 100), (None, "none", 0),
])
def test_bar_position_boundaries(pct, side, width):
    b = _js(f"lpwBar({json.dumps(pct)}, 5)")
    assert b["side"] == side and b["width"] == pytest.approx(width)


def test_bar_colors_match_change_colors():
    """색 규칙: 양수 바 = 등락% 양수 글자색, 음수 바 = 음수 글자색(테마 토큰)."""
    pct = _fn("_lptPct")
    assert "var(--c-red-fg3)" in pct and "var(--c-blue-fg4)" in pct
    assert ".lpw-bar>i.pos{left:50%;background:var(--c-red-fg3)}" in SRC
    assert ".lpw-bar>i.neg{right:50%;background:var(--c-blue-fg4)}" in SRC


R = lambda i, code, base, last, status="active", date="2026-10-02", **k: {
    "id": i, "tf": "week", "label": "2026-10-02", "code": code, "name": code, "mkt": "US", "base_date": date,
    "base_price": base, "last_close": last, "status": status, **k}
RECS = [R("a", "AAA", 100, 101), R("b", "BBB", 100, 104.5), R("c", "CCC", 100, 97), R("d", "DDD", 100, None),
        R("e", "EEE", 100, 106, "reached", reached_date="2026-10-05", reached_days=3),
        R("f", "FFF", 100, 102, date="2026-09-30"),
        {**R("g", "GGG", 50, 49), "tf": "month", "label": "2026-09-30", "base_date": "2026-09-30"}]


def test_sort_rows():
    ids = lambda key, d: _js(f"lpwSortRows({json.dumps(RECS[:4] + RECS[5:6])}, '{key}', '{d}', '2026-10-07').map(r => r.id)")
    assert ids("pct", "desc") == ["b", "f", "a", "c", "d"]          # +5%에 가까운 순, 현재가 없음은 맨 아래
    assert ids("pct", "asc") == ["c", "a", "f", "b", "d"]
    assert ids("days", "desc")[0] == "f"                             # 기준일 09-30 → 경과 7일이 가장 김


def test_render_default_sort_and_group_counts():
    html = _js("renderLowpointWatch()", RECS)
    sections = html.split('<section class="n-card lpw-card">')[1:]
    assert len(sections) == 3                                          # 코호트 2 + v5.337 숨고르기 1(종료 0건이면 카드 없음)
    wk = sections[0]
    assert "10-02 주봉 · 관찰 5 / 출발 1" in wk and "09-30 월봉 · 관찰 1 / 출발 0" in sections[1]
    assert "기준일 2026-09-30, 2026-10-02" in wk                       # 기준일은 그룹 헤더에만
    body = wk.split("<tbody>")[1].split("</tbody>")[0]
    assert [m for m in re.findall(r">(AAA|BBB|CCC|DDD|FFF)</a>", body)] == ["BBB", "FFF", "AAA", "CCC", "DDD"]
    assert body.count("2026-10-02") == 0, "기준일이 행마다 반복된다"
    # 바: +4.5% → 오른쪽 절반의 90% = 트랙의 45%, −3% → 왼쪽 30%
    assert '<i class="pos" style="width:45.0%">' in body and '<i class="neg" style="width:30.0%">' in body
    assert "<details" not in wk and "숨고르기 · 1" in sections[2]     # v5.337: 출발(도달)은 코호트 밖 숨고르기 섹션으로


def test_render_sort_toggle_by_header():
    html = _js("renderLowpointWatch()", RECS, extra="_lpw.sortKey = 'pct'; _lpw.sortDir = 'asc';")
    body = html.split("<tbody>")[1].split("</tbody>")[0]
    assert re.findall(r">(AAA|BBB|CCC|DDD|FFF)</a>", body) == ["CCC", "AAA", "FFF", "BBB", "DDD"]
    assert "lpwSort('pct')" in html and "lpwSort('days')" in html
    s = _fn("lpwSort")
    assert "_lpw.sortDir = _lpw.sortDir === 'asc' ? 'desc' : 'asc'" in s and "_lpw.sortDir = 'desc'" in s


def test_columns_and_icon_buttons():
    html = _js("renderLowpointWatch()", RECS)
    head = html.split("<thead>")[1].split("</thead>")[0]
    cols = [re.sub(r"<[^>]+>|[▲▼]", "", c).strip() for c in re.findall(r"<th[^>]*>(.*?)</th>", head)]
    assert cols == ["종목", "기준가 → 현재가", "등락", "+5%까지", "경과", ""]
    assert 'aria-label="기록"' in html and 'aria-label="삭제"' in html and ">기록</button>" not in html


def test_new_css_is_scoped_to_watch():
    """v5.326 CSS는 전부 .lpw-* 아래 — 매매 기록·평가·일지 표에 새 규칙이 닿지 않는다."""
    block = SRC[SRC.index("/* v5.326 저점 관찰 압축 표"):SRC.index("/* /v5.326 */")]
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    rules = re.findall(r"([^{}]+)\{[^{}]*\}", re.sub(r"@media[^{]*\{(.*?\})\s*\}", r"\1", block, flags=re.S))
    assert len(rules) >= 15
    for sel in rules:
        for s in sel.split(","):
            assert ".lpw" in s, f"관찰 밖으로 새는 선택자: {s.strip()}"
    for line in block.splitlines():
        assert not re.search(r"#[0-9a-fA-F]{3,6}\b", line), f"하드코딩 색: {line}"


def test_other_pages_markup_untouched():
    """매매 기록·평가 렌더는 lpw 클래스를 쓰지 않는다(스타일이 섞이지 않게)."""
    for f in ("renderLowpointEval", "_lpeCard", "_lpeShortHtml"):
        assert "lpw-" not in _fn(f)
    track = _fn("renderLowpointTrack")
    assert "lpw-" not in track.split("if (view === 'watch')")[0] + track.split("renderLowpointWatch();")[1]
