"""v5.283 — 추추 메뉴 신설 + 시장 강제 탭의 진입/이탈 규칙.

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


def _top_level_modes() -> list:
    """최상위(드롭다운 밖) 탭의 data-mode 목록."""
    head = TEXT[:TEXT.index('<button class="tab" id="chuchuToggle"')]
    head = head[head.index('data-mode="calendar"'):]
    return re.findall(r'<button class="tab[^"]*" data-mode="(\w+)"', head)


def _group_modes(group_id: str) -> list:
    i = TEXT.index(f'<span id="{group_id}"')
    j = TEXT.index("</span>", i)
    return re.findall(r'data-mode="(\w+)"', TEXT[i:j])


# ── 메뉴 구조 ───────────────────────────────────────────────────────
def test_three_tabs_left_the_top_level():
    top = _top_level_modes()
    for m in ("imminent", "boxbreak", "breakout"):
        assert m not in top, f"{m}가 아직 최상위에 있다: {top}"


def test_three_tabs_are_under_chuchu():
    assert _group_modes("chuchuGroup") == ["imminent", "boxbreak", "breakout"]


def test_pullback_and_turnaround_stay_on_top():
    top = _top_level_modes()
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


def test_chuchu_group_is_collapsible_like_the_experiment_group():
    assert 'id="chuchuToggle"' in TEXT and 'id="chuchuGroup"' in TEXT
    # 모바일 폭에서도 같은 규칙으로 줄바꿈되도록 display:contents 방식을 맞춘다
    assert "chuchuGroup').style.display = chuchuExpanded ? 'contents' : 'none'" in TEXT


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
