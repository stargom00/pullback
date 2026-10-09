"""v5.345 — 스캔 통계 줄(#status) 누수 수정: 별도 화면 탭에서는 #status가 항상 숨김.

경위(브라우저 재현 2026-10-09, headless Chrome — /api/abc만 운영 모양으로 바꿔 넣고 나머지는 실제 프론트 코드):
ABC 탭 → 저점일지(5페이지 모두)로 가면 "스캔 기준 2026-10-08 19:42 KST · 728건 · 번들 캐시만 읽음(새 조회 없음)" 줄이 남았다
(ABC 해시로 연 뒤 이동도 같음, 저점일지 해시 새로고침·홈에서 직접 진입은 안 남음). 원인: #status는 #content와 같은 스캔형 탭 묶음
(로더 6곳만 씀)인데 applyTabViewState가 별도 화면 탭에서 #content·검색줄·진단줄만 숨기고 #status는 표시 규칙에서 빠져 있었다.
수정: #content와 같은 규칙(SCAN_AREA_IDS)으로 같이 켜고 끈다.

사보타주 확인(2026-10-09, FAIL 확인 후 원복): SCAN_AREA_IDS에서 'status'를 뺌 → test_status_hidden_in_every_custom_view FAIL
(브라우저 대표 경로도 다시 누수)
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
CUSTOM = ["journal", "moneyflow", "positions", "calendar", "surge_observe", "lowpoint_track", "newlisting"]


def _fn(name):
    start = SRC.index(f"function {name}(")
    i = SRC.index("{", SRC.index(")", start))
    d = 0
    for j in range(i, len(SRC)):
        d += {"{": 1, "}": -1}.get(SRC[j], 0)
        if d == 0:
            return SRC[start:j + 1]
    raise AssertionError(name)


def _line(prefix):
    return [l for l in SRC.splitlines() if l.startswith(prefix)][0]


FAKE = """
const els = {};
const document = { getElementById: id => (els[id] = els[id] || { id, style: { display: '' }, classList: { toggle() {} } }) };
let mode = 'abc';
const noop = () => {};
const hideFilterButtonsIfNotApplicable = noop, applyMarketButtonsEnabled = noop, updateMoreTabsToggle = noop, writeTabHash = noop,
      renderJournal = noop, onEnterMoneyflowTab = noop, onEnterPositionsTab = noop, onEnterCalendarTab = noop,
      onEnterSurgeObserveTab = noop, onEnterLowpointTrackTab = noop, onEnterNewlistingTab = noop, load = noop;
"""


def _run(body):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    src = (FAKE + _line("const IDXBAR_HIDDEN_MODES = ") + "\n" + _line("const TAB_OWNED_BANNERS = ") + "\n"
           + _line("const SCAN_AREA_IDS = ") + "\n" + _fn("applyTabViewState") + "\n" + f"console.log(JSON.stringify((() => {{ {body} }})()));")
    p = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_status_hidden_in_every_custom_view():
    """스캔형 탭(ABC 등)에서 #status가 보이다가 → 별도 화면 탭 7개 어디로 가도 숨김. 다시 스캔형 탭이면 표시."""
    got = _run(f"""const out = {{}};
      for (const m of {json.dumps(CUSTOM)}) {{
        mode = 'abc'; applyTabViewState(); const before = els.status.style.display;
        mode = m; applyTabViewState(); out[m] = [before, els.status.style.display, els.content.style.display];
      }}
      mode = 'pullback'; applyTabViewState(); out.back = els.status.style.display;
      return out;""")
    for m in CUSTOM:
        assert got[m] == ["", "none", "none"], (m, got[m])           # #content와 같은 규칙
    assert got["back"] == ""


def test_custom_view_list_is_the_one_in_apply():
    """테스트의 별도 화면 탭 목록이 applyTabViewState의 isCustomView 구성과 같다(새 탭이 생기면 이 테스트가 먼저 깨진다)."""
    body = _fn("applyTabViewState")
    modes = re.findall(r"mode === '([a-z_]+)'", body.split("const isCustomView")[0])
    assert sorted(modes) == sorted(CUSTOM)
    assert "for (const id of SCAN_AREA_IDS) document.getElementById(id).style.display = isCustomView ? 'none' : '';" in body
    assert _line("const SCAN_AREA_IDS = ") == "const SCAN_AREA_IDS = ['content', 'status'];"
    assert "#status{display:none" not in SRC.replace(" ", "")                           # CSS로 가리는 땜질 없음
