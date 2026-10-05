"""v5.330 — 현재 탭·저점일지 하위 페이지를 URL 해시에(새로고침 복원).

사용자 지시: "스캐너에서 새로고침하면 항상 홈 탭으로 돌아간다. 현재 탭/페이지가 URL에 없어서다." 형식 #tab=저점일지&page=관찰,
history.replaceState, 로드 시 해시가 있으면 복원(없거나 모르는 탭이면 홈), 기존 탭 전환 구조는 유지(읽기·쓰기만 추가).
실제 브라우저 새로고침 검증(전 탭 + 저점일지 4페이지)은 로컬 서버 Playwright로 따로 했다(커밋 보고 참고).

사보타주 확인(2026-10-06, FAIL 확인 후 원복):
① restoreTabFromHash가 늘 false(복원 끔) → test_restore_lowpoint_page·test_restore_scanner_tab FAIL
   (브라우저에서도 새로고침하면 홈으로 감을 확인)
② writeTabHash가 pushState를 씀 → test_write_uses_replace_state FAIL
③ 부트스트랩이 복원 없이 applyTabViewState만 부름 → test_bootstrap_restores_then_falls_back FAIL
"""
from __future__ import annotations

import json
import os
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


PAGES_LINE = [l for l in SRC.splitlines() if l.startswith("const LPT_PAGES = ")][0]
TABS = [["calendar", "홈"], ["pullback", "US눌림목"], ["journal", "추추일지"], ["lowpoint_track", "저점일지"],
        ["moneyflow", ""], ["newlisting", "신규상장"]]

# 가짜 DOM — [data-mode] 버튼·location·history. 버튼 click()은 눌린 mode를 기록한다.
FAKE = f"""
const _TABS = {json.dumps(TABS, ensure_ascii=False)};
const clicked = [], replaced = [], pushed = [], setViews = [];
const _btn = m => ({{ dataset: {{ mode: m }}, textContent: '  ' + (_TABS.find(t => t[0] === m)[1]) + ' ', click() {{ clicked.push(m); }} }});
const document = {{
  querySelectorAll: sel => _TABS.map(t => _btn(t[0])),
  querySelector: sel => {{ const m = /data-mode="([^"]+)"/.exec(sel); return m && _TABS.some(t => t[0] === m[1]) ? _btn(m[1]) : null; }},
}};
const location = {{ hash: '' }};
const history = {{ replaceState: (a, b, h) => {{ replaced.push(h); location.hash = h; }}, pushState: (a, b, h) => pushed.push(h) }};
var mode = 'calendar';
var _lpt = {{ view: 'trades' }};
function lpSetView(v) {{ setViews.push(v); }}
"""


def _js(expr, pre=""):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    src = (FAKE + PAGES_LINE + "\n" + "\n".join(_fn(f) for f in ("tabHashBuild", "tabHashParse", "_tabHashTabs",
                                                                  "writeTabHash", "restoreTabFromHash")) + "\n" + pre)
    p = subprocess.run(["node", "-e", src + f"\nconsole.log(JSON.stringify({expr}));"], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


# ── 순수 함수 ─────────────────────────────────────────────────────
@pytest.mark.parametrize("page,label", [("trades", "매매 기록"), ("eval", "평가"), ("watch", "관찰"), ("hold", "추적")])
def test_build_parse_roundtrip_lowpoint_pages(page, label):
    h = _js(f"tabHashBuild('lowpoint_track', '저점일지', '{page}')")
    assert _js(f"Object.fromEntries(new URLSearchParams({json.dumps(h)}.slice(1)))") == {"tab": "저점일지", "page": label}
    assert _js(f"tabHashParse({json.dumps(h)}, _TABS)") == {"mode": "lowpoint_track", "page": page}


@pytest.mark.parametrize("hash,want", [
    ("", None), ("#", None), ("#foo=bar", None), ("#tab=없는탭", None), ("#tab=%E2%82%AC%%", None),
    ("#tab=US눌림목", {"mode": "pullback", "page": None}),
    ("#tab=pullback", {"mode": "pullback", "page": None}),                    # mode 키도 받는다
    ("#tab=moneyflow", {"mode": "moneyflow", "page": None}),                  # 글자 없는 탭
    ("#tab=저점일지&page=없는페이지", {"mode": "lowpoint_track", "page": None}),
    ("#tab=저점일지&page=watch", {"mode": "lowpoint_track", "page": "watch"}),
    ("#tab=추추일지&page=관찰", {"mode": "journal", "page": None}),            # 하위 페이지는 저점일지만
])
def test_parse_cases(hash, want):
    assert _js(f"tabHashParse({json.dumps(hash)}, _TABS)") == want


def test_page_only_for_lowpoint():
    assert _js("tabHashBuild('journal', '추추일지', 'watch')") == "#tab=" + _js("encodeURIComponent('추추일지')")


# ── 복원·쓰기(가짜 DOM에서 production 함수 실행) ─────────────────────
def test_restore_lowpoint_page():
    got = _js("(() => { location.hash = '#tab=' + encodeURIComponent('저점일지') + '&page=' + encodeURIComponent('관찰');"
              " const r = restoreTabFromHash(); return [r, clicked, _lpt.view, setViews]; })()")
    assert got == [True, ["lowpoint_track"], "watch", ["watch"]]          # 탭 클릭 경로 + 하위 페이지 + 페이지 데이터 로드


def test_restore_scanner_tab():
    got = _js("(() => { location.hash = '#tab=' + encodeURIComponent('US눌림목'); return [restoreTabFromHash(), clicked, setViews]; })()")
    assert got == [True, ["pullback"], []]


@pytest.mark.parametrize("hash", ["", "#tab=없는탭", "#tab=홈", "#garbage"])
def test_no_or_unknown_hash_keeps_home(hash):
    got = _js(f"(() => {{ location.hash = {json.dumps(hash)}; return [restoreTabFromHash(), clicked]; }})()")
    assert got == [False, []]


def test_write_uses_replace_state():
    got = _js("(() => { mode = 'lowpoint_track'; _lpt.view = 'hold'; writeTabHash(); writeTabHash(); return [replaced, pushed]; })()")
    want = "#tab=" + _js("encodeURIComponent('저점일지')") + "&page=" + _js("encodeURIComponent('추적')")
    assert got == [[want], []]                                              # 같은 해시면 다시 쓰지 않는다


def test_bootstrap_restores_then_falls_back():
    assert SRC.count("if (!restoreTabFromHash()) applyTabViewState();") == 1
    tail = SRC[SRC.index("if (!restoreTabFromHash()) applyTabViewState();") - 400:SRC.index("if (!restoreTabFromHash()) applyTabViewState();")]
    assert "\napplyTabViewState();" not in tail                              # 부트스트랩에서 두 번 부르지 않는다
    assert _fn("applyTabViewState").rstrip().endswith("writeTabHash();   // v5.330: 현재 탭을 URL에(새로고침 복원용)\n}")
    assert "if (mode === 'lowpoint_track') writeTabHash();" in _fn("renderLowpointTrack")
    assert "pushState" not in _fn("writeTabHash")


def test_tab_switch_structure_unchanged():
    """클릭 핸들러 본문은 그대로(복원은 버튼 click()으로 같은 경로를 탄다)."""
    handler = SRC[SRC.index("document.querySelectorAll('[data-mode]').forEach(t => t.addEventListener('click', () => {"):]
    handler = handler[:handler.index("}));") + 4]
    assert "writeTabHash" not in handler and "restoreTabFromHash" not in handler and handler.count("applyTabViewState();") == 1
