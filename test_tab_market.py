"""v5.283 — 추추 메뉴 신설 + 시장 강제 탭의 진입/이탈 규칙.
(v5.294: 추추·⋯실험은 더보기 패널로 합쳐졌다 — 메뉴 구조 검사를 그 기준으로 교체.)

[시장 강제] 예전엔 종가베팅만 탭 클릭 핸들러에 `setMarket('kr')`이 박혀
있었고 **복원이 없었다** — 보고 나오면 다른 탭에도 kr이 남았다. US눌림목이
같은 방식이면 ABC 등에서 미국만 보이게 된다. 규칙을 `applyForcedMarket()`
**한 곳**으로 모으고 복원을 넣었다(사용자 지시 A안).

[라벨과 키] 버튼 라벨만 "US눌림목"이고 `data-mode="pullback"`과 저장
식별자(`MODE_TAB_LABEL.pullback` → 일지 `tab:"눌림목"`, app.py의 EV 조회키·
`tab == "눌림목"` 분기)는 **그대로**다. 키까지 바꾸면 기존 레코드가 끊긴다.

[해시] URL 해시 라우팅은 이 앱에 **존재하지 않는다**(`location.hash` 0건) —
딥링크 관련 항목은 조사 후 삭제했다.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
TEXT = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


def _fn(name: str) -> str:
    for decl in (f"async function {name}(", f"function {name}("):
        i = TEXT.find(decl)
        if i != -1:
            break
    assert i != -1, name
    b = TEXT.index("{", i)
    d = 0
    for k in range(b, len(TEXT)):
        if TEXT[k] == "{":
            d += 1
        elif TEXT[k] == "}":
            d -= 1
            if d == 0:
                return TEXT[i:k + 1]
    raise AssertionError(name)


def _modes_in(elem_id: str) -> list:
    """`id=elem_id` 요소(nav/div) 안의 data-mode 목록 — 등장 순서 그대로.

    v5.294: 추추·⋯실험 접기 그룹이 **더보기 패널 하나**로 합쳐졌다. 메인 탭
    줄(#modeTabs), 오른쪽 작은 메뉴(#utilTabs), 더보기 패널(#moreTabsPanel)을
    각자 닫는 태그까지 잘라 본다(중첩 없는 구조라 첫 닫는 태그로 충분 —
    패널은 섹션 div가 중첩돼 있어 `</header>` 직전까지 자른다).
    """
    i = TEXT.index(f'id="{elem_id}"')
    if elem_id == "moreTabsPanel":
        j = TEXT.index("</header>", i)
    else:
        j = TEXT.index("</nav>", i)
    return re.findall(r'data-mode="(\w+)"', TEXT[i:j])


# ── 메뉴 구조 (v5.294, 사용자 지시) ─────────────────────────────────
# 메인: 홈 · US눌림목 · ABC · 추세전환 · 더보기▾ / 오른쪽: 업종/테마 · 마감정리 · 일지
EXPECTED_MAIN = ["calendar", "pullback", "abc", "turnaround"]
EXPECTED_UTIL = ["themes", "eod", "journal"]
EXPECTED_MORE_TABS = ["jongga", "imminent", "boxbreak", "breakout", "surge_observe", "positions"]


def test_main_tab_order():
    assert _modes_in("modeTabs") == EXPECTED_MAIN
    # 더보기 토글은 data-mode가 없는 버튼 — 메인 줄의 **마지막**이어야 한다
    i = TEXT.index('id="modeTabs"')
    body = TEXT[i:TEXT.index("</nav>", i)]
    assert body.rstrip().endswith("</button>")
    assert body.index('id="moreTabsToggle"') > body.index('data-mode="turnaround"')


def test_util_menu():
    assert _modes_in("utilTabs") == EXPECTED_UTIL


def test_more_panel_tabs_section_order():
    more = _modes_in("moreTabsPanel")
    assert more[:len(EXPECTED_MORE_TABS)] == EXPECTED_MORE_TABS, more


def test_default_tab_is_still_calendar():
    assert '<button class="tab active" data-mode="calendar">홈</button>' in TEXT


def test_three_breakout_tabs_are_in_the_more_panel():
    for m in ("imminent", "boxbreak", "breakout"):
        assert m not in _modes_in("modeTabs")
        assert m in _modes_in("moreTabsPanel")


def test_pullback_and_turnaround_stay_on_top():
    top = _modes_in("modeTabs")
    assert "pullback" in top and "turnaround" in top, top


def test_label_is_us_pullback_but_key_is_unchanged():
    m = re.search(r'<button class="tab" data-mode="pullback"[^>]*>([^<]+)</button>', TEXT)
    assert m and m.group(1) == "US눌림목", m and m.group(1)


def test_internal_identifier_stays_korean():
    """**저장 식별자를 라벨과 같이 바꾸면 안 된다** — 기존 일지 레코드
    (`tab:"눌림목"`)와 app.py의 EV 조회키가 끊긴다."""
    i = TEXT.index("const MODE_TAB_LABEL = {")
    block = TEXT[i:TEXT.index("}", i)]
    assert "pullback: '눌림목'" in block, block
    assert "US눌림목" not in block, "저장 식별자가 라벨을 따라 바뀌었다"
    import sys
    sys.path.insert(0, str(ROOT))
    import app
    assert app.GATE_MODE_LABELS["pullback"] == "눌림목"


# ── 시장 강제/복원 ──────────────────────────────────────────────────
def _run(clicks):
    """`applyForcedMarket()`을 **그대로 꺼내 실행**한다(재구현 아님).

    clicks: [(mode, user_picks_market_or_None), ...] 순서대로 탭 이동.
    """
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    src = _fn("applyForcedMarket")
    i = TEXT.index("const FORCED_MARKET_BY_MODE")
    consts = TEXT[i:TEXT.index("function applyForcedMarket")]
    harness = f"""
let market = 'all';
function setMarket(m) {{ market = m; }}
{consts}
{src}
let mode = 'abc';
const steps = {json.dumps(clicks)};
for (const [next, pick] of steps) {{
  const prev = mode; mode = next;
  applyForcedMarket(prev, mode);
  if (pick) {{ if (FORCED_MARKET_BY_MODE[mode]) _userPickedInForcedTab = true; setMarket(pick); }}
}}
console.log(JSON.stringify({{market}}));
"""
    p = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip())["market"]


def test_abc_to_jongga_forces_kr():
    assert _run([["jongga", None]]) == "kr"


def test_abc_to_jongga_to_abc_restores_all():
    assert _run([["jongga", None], ["abc", None]]) == "all"


def test_abc_to_pullback_forces_us():
    assert _run([["pullback", None]]) == "us"


def test_abc_to_pullback_to_abc_restores_all():
    assert _run([["pullback", None], ["abc", None]]) == "all"


def test_forced_to_forced_keeps_the_original_saved_value():
    """강제 → 강제 직행에서 저장값을 kr/us로 덮어쓰면 복원이 망가진다."""
    assert _run([["jongga", None], ["pullback", None], ["abc", None]]) == "all"
    assert _run([["pullback", None], ["jongga", None], ["abc", None]]) == "all"


def test_user_choice_inside_a_forced_tab_survives_the_exit():
    """탭 안에서 직접 고른 시장은 이탈해도 유지된다."""
    assert _run([["pullback", "kr"], ["abc", None]]) == "kr"
    assert _run([["jongga", "us"], ["abc", None]]) == "us"


def test_restore_returns_to_whatever_was_set_before():
    """'all'만 되는 게 아니라 직전 값 무엇이든 되돌아와야 한다."""
    assert _run([["abc", "kr"], ["pullback", None], ["abc", None]]) == "kr"


def test_rule_lives_in_one_place():
    """탭별 복붙 금지(사용자 지시) — 클릭 핸들러에 setMarket 직접 호출이
    남아 있으면 규칙이 둘로 갈린다."""
    handler = TEXT[TEXT.index("document.querySelectorAll('[data-mode]').forEach"):]
    handler = handler[:handler.index("applyTabViewState();")]
    code = "\n".join(l for l in handler.splitlines() if not l.strip().startswith("//"))
    assert "applyForcedMarket(" in code, code
    assert "setMarket(" not in code, f"핸들러가 시장을 직접 바꾼다: {code}"
