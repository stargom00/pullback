"""v5.348 — 추세전환·마감정리를 "더보기"로 옮김(메뉴 위치만). 해시로 직접 열리고, 열려 있으면 더보기 라벨에 탭 이름이 뜬다.

이 테스트는 가짜 탭 목록이 아니라 **실제 static/index.html의 [data-mode] 버튼**(메뉴별 순서·글자)으로 restoreTabFromHash·updateMoreTabsToggle을
Node로 실행한다 — 그래서 더보기에서 항목을 빼면 해시 복원이 바로 깨진다.
사보타주 확인(2026-10-15, FAIL 확인 후 원복): 더보기에서 마감정리 버튼 삭제 → test_hash_opens_moved_tab[마감정리-eod]·test_moved_tabs_live_in_more_panel FAIL.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SRC = (Path(__file__).resolve().parent / "static" / "index.html").read_text(encoding="utf-8")


def _fn(name):
    start = SRC.index(f"function {name}(")
    i = SRC.index("{", SRC.index(")", start))
    d = 0
    for j in range(i, len(SRC)):
        d += {"{": 1, "}": -1}.get(SRC[j], 0)
        if d == 0:
            return SRC[start:j + 1]
    raise AssertionError(name)


def _section(start_marker, end_marker):
    i = SRC.index(start_marker)
    return SRC[i:SRC.index(end_marker, i)]


def _buttons(html):
    """[(mode, 첫 글자 노드)] — 버튼의 첫 텍스트(태그 앞까지, 더보기 라벨이 쓰는 firstChild와 같은 부분)."""
    return [(m, t.strip()) for m, t in re.findall(r'<button class="tab[^"]*" data-mode="(\w+)"[^>]*>([^<]*)', html)]


MAIN = _buttons(_section('id="modeTabs"', "</nav>"))
UTIL = _buttons(_section('id="utilTabs"', "</nav>"))
MORE = _buttons(_section('id="moreTabsPanel"', "</header>"))
PAGES_LINE = ([l for l in SRC.splitlines() if l.startswith("const LPT_PAGES = ")][0] + "\n"
              + [l for l in SRC.splitlines() if l.startswith("const ABC_GRADE_VIEWS = ")][0])


def _js(expr):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    tabs = MAIN + UTIL + MORE
    fake = f"""
const _TABS = {json.dumps(tabs, ensure_ascii=False)};
const _MORE = {json.dumps(MORE, ensure_ascii=False)};
const clicked = [];
const _btn = (m, l) => ({{ dataset: {{ mode: m }}, textContent: l, firstChild: {{ textContent: l }}, click() {{ clicked.push(m); }} }});
const document = {{
  querySelectorAll: sel => _TABS.map(([m, l]) => _btn(m, l)),
  querySelector: sel => {{ const x = /data-mode="([^"]+)"/.exec(sel); const t = x && _TABS.find(([m]) => m === x[1]); return t ? _btn(t[0], t[1]) : null; }},
}};
const moreTabsPanel = {{ querySelector: sel => {{ const x = /data-mode="([^"]+)"/.exec(sel); const t = x && _MORE.find(([m]) => m === x[1]); return t ? _btn(t[0], t[1]) : null; }} }};
const moreTabsToggle = {{ firstChild: {{ textContent: '더보기' }}, classList: {{ on: false, toggle(c, v) {{ this.on = v; }} }} }};
const location = {{ hash: '' }};
var mode = 'calendar';
var _lpt = {{ view: 'trades' }};
var abcGradeFilter = 'A';
function lpSetView() {{}}
"""
    src = fake + PAGES_LINE + "\n" + "\n".join(_fn(f) for f in ("_tabSubPages", "tabHashParse", "_tabHashTabs", "restoreTabFromHash",
                                                              "updateMoreTabsToggle"))
    p = subprocess.run(["node", "-e", src + f"\nconsole.log(JSON.stringify({expr}));"], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_moved_tabs_live_in_more_panel():
    """이동만, 삭제 아님: 메인·기록 메뉴에서 빠지고 더보기 '탭' 섹션 끝(포지션 뒤)에 추세전환 → 마감정리."""
    assert "turnaround" not in dict(MAIN) and "eod" not in dict(UTIL)
    tab_sec = _buttons(_section('<div class="more-h">탭</div>', "</div>\n    <div class=\"more-sec\">"))
    assert [m for m, _ in tab_sec][-3:] == ["positions", "turnaround", "eod"]
    assert dict(MORE)["turnaround"] == "추세전환" and dict(MORE)["eod"] == "마감정리"
    modes = [m for m, _ in MAIN + UTIL + MORE]
    assert modes.count("turnaround") == 1 and modes.count("eod") == 1
    assert [m for m, _ in UTIL] == ["themes", "journal", "lowpoint_track", "coin_journal"]


@pytest.mark.parametrize("label,mode", [("추세전환", "turnaround"), ("마감정리", "eod")])
def test_hash_opens_moved_tab(label, mode):
    got = _js(f"(() => {{ location.hash = '#tab={label}'; const ok = restoreTabFromHash(); return [ok, clicked]; }})()")
    assert got == [True, [mode]]


@pytest.mark.parametrize("label,mode", [("추세전환", "turnaround"), ("마감정리", "eod")])
def test_more_label_shows_current_moved_tab(label, mode):
    got = _js(f"(() => {{ mode = '{mode}'; updateMoreTabsToggle(); return [moreTabsToggle.firstChild.textContent, moreTabsToggle.classList.on]; }})()")
    assert got == [label, True]
    assert _js("(() => { mode = 'journal'; updateMoreTabsToggle(); return moreTabsToggle.firstChild.textContent; })()") == "더보기"
