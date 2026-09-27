"""v5.290 (사용자 지시) — 3건 검사:
  1. `_calendar_default_market_session()` 주말·휴장일 → "all", 평일 시간대 규칙 불변
  2. 시장 버튼 클릭 시 `mode==='abc'`는 `renderAbcPage()`로 가고 `renderCards()`를 안 탄다
  3. ABC 진입 시 시장 버튼 disabled / 이탈 시 enabled 복원, 그리고 `market` 값 불변

2·3은 CLAUDE.md "텍스트 추출 + Node 실행" 레시피 — production 코드를 그대로
추출해 node로 돌린다(재구현 금지).
"""
import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
KST = timezone(timedelta(hours=9))
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node 미설치")


def _code_only(src: str) -> str:
    """`//` 주석 줄 제거 — "이 문자열이 없어야 한다" 검사가 주석 인용문에
    걸려 오탐하는 것을 막는다(CLAUDE.md 패턴 1)."""
    return "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("//"))


def _block(header: str, src: str = None) -> str:
    src = HTML if src is None else src
    start = src.index(header)
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError(header)


# ══ 1) 서버: 주말·휴장일 → all, 평일 규칙 불변 ═════════════════════
# (KST datetime, KR 거래일 여부, 기대값)
SESSION_CASES = [
    # 토 10:00 / 일 10:00 → all (규칙 ①)
    (datetime(2026, 9, 26, 10, 0, tzinfo=KST), True, "all"),
    (datetime(2026, 9, 27, 10, 0, tzinfo=KST), True, "all"),
    # 휴장 평일 10:00 → all (규칙 ②). 2026-09-25(금)은 추석 — 실제 휴장일
    (datetime(2026, 9, 25, 10, 0, tzinfo=KST), False, "all"),
    # 평일 08:00 → kr (규칙 ③, 불변)
    (datetime(2026, 9, 28, 8, 0, tzinfo=KST), True, "kr"),
    # 평일 21:00 → us (규칙 ④, 불변)
    (datetime(2026, 9, 28, 21, 0, tzinfo=KST), True, "us"),
    # 경계: 07:00 직전/직후, 20:10 직전/직후 (③④ 경계 불변)
    (datetime(2026, 9, 28, 6, 59, tzinfo=KST), True, "us"),
    (datetime(2026, 9, 28, 7, 0, tzinfo=KST), True, "kr"),
    (datetime(2026, 9, 28, 20, 9, tzinfo=KST), True, "kr"),
    (datetime(2026, 9, 28, 20, 10, tzinfo=KST), True, "us"),
]


@pytest.mark.parametrize("now,is_td,expected", SESSION_CASES)
def test_calendar_default_market_session(monkeypatch, now, is_td, expected):
    import app

    class _FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(app, "datetime", _FakeDT)
    monkeypatch.setattr(app, "is_trading_day", lambda market, d: is_td)
    got = app._calendar_default_market_session()
    assert got == expected, f"{now:%Y-%m-%d %a %H:%M} KST (거래일={is_td}) → {got!r}, 기대 {expected!r}"


def test_weekday_rules_are_not_all():
    """평일 규칙이 실수로 all로 넓어지지 않았는지 — 주말 규칙만 바뀌어야 한다."""
    weekday = [e for now, _, e in SESSION_CASES if now.weekday() < 5 and e != "all"]
    assert "kr" in weekday and "us" in weekday
    assert weekday.count("all") == 0


def test_immediate_session_filter_handles_all():
    """세션이 'all'일 때 즉시행동이 전건 밀려나지 않는지 — 소스에 'ALL' 분기가
    있어야 한다. 없으면 주말마다 🔴가 0건이 된다."""
    import inspect

    import app
    src = inspect.getsource(app.get_calendar)
    assert '_session_mkt_upper == "ALL"' in src, (
        'market_session이 "all"이면 (market or "").upper() == "ALL" 이 전건 거짓이라 '
        "immediate가 전부 immediate_other_market으로 밀린다 — ALL 분기가 필요하다."
    )
    assert '_label = {"KR": "KR", "US": "US"}.get(_session_mkt_upper, "KR/US")' in src


# ══ 2) 시장 버튼 클릭 → abc는 renderAbcPage ════════════════════════
@needs_node
def test_market_click_routes_abc_to_render_abc_page():
    """시장 버튼 클릭 핸들러의 분기를 production 소스에서 뽑아 실행 —
    mode별로 어느 렌더러가 불리는지 확인한다."""
    start = HTML.index("document.querySelectorAll('[data-market]').forEach(t => t.addEventListener('click'")
    end = HTML.index("}));", start)
    handler = HTML[start:end]
    # 분기 부분만 추출(앞의 setMarket/localStorage 등은 DOM 의존)
    br_start = handler.index("if (mode === 'moneyflow')")
    branches = handler[br_start:]
    script = f"""
let calls = [];
let _calendarData = {{}}, _abcData = {{hits: []}};
function loadMoneyflowDate() {{ calls.push('loadMoneyflowDate'); }}
function renderCalendar() {{ calls.push('renderCalendar'); }}
function renderAbcPage() {{ calls.push('renderAbcPage'); }}
function renderCards() {{ calls.push('renderCards'); }}
const out = {{}};
for (const m of ['abc', 'pullback', 'calendar', 'moneyflow', 'imminent', 'jongga']) {{
  calls = [];
  const mode = m;
  {branches}
  out[m] = calls;
}}
console.log(JSON.stringify(out));
"""
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    assert got["abc"] == ["renderAbcPage"], f"abc가 renderAbcPage로 안 감: {got['abc']}"
    assert "renderCards" not in got["abc"], "abc에서 renderCards가 불리면 ABC 표가 파괴된다"
    # 다른 탭은 기존 동작 불변
    assert got["pullback"] == ["renderCards"], got["pullback"]
    assert got["imminent"] == ["renderCards"], got["imminent"]
    assert got["jongga"] == ["renderCards"], got["jongga"]
    assert got["calendar"] == ["renderCalendar"], got["calendar"]
    assert got["moneyflow"] == ["loadMoneyflowDate"], got["moneyflow"]


# ══ 3) ABC 진입/이탈 시 시장 버튼 disabled/복원 ════════════════════
@needs_node
def test_market_buttons_disabled_on_abc_and_restored_on_leave():
    fn = _block("function applyMarketButtonsEnabled(")
    script = f"""
// data-market 버튼 3개를 흉내낸 최소 DOM 스텁
const btns = ['all', 'kr', 'us'].map(m => ({{dataset: {{market: m}}, disabled: false,
  style: {{opacity: '', cursor: ''}}, title: ''}}));
const document = {{ querySelectorAll: () => btns }};
let market = 'kr';              // 값이 바뀌는지 감시용
{fn}
const snap = () => btns.map(b => ({{d: b.disabled, o: b.style.opacity}}));
const out = {{}};
applyMarketButtonsEnabled('pullback'); out.before = snap();
applyMarketButtonsEnabled('abc');      out.onAbc = snap(); out.titleOnAbc = btns[0].title;
applyMarketButtonsEnabled('imminent'); out.afterLeave = snap(); out.titleAfter = btns[0].title;
out.market = market;
console.log(JSON.stringify(out));
"""
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    r = json.loads(proc.stdout)
    assert all(not b["d"] for b in r["before"]), r["before"]
    assert all(b["d"] for b in r["onAbc"]), f"ABC 진입 시 3개 전부 disabled여야 함: {r['onAbc']}"
    assert all(b["o"] == "0.4" for b in r["onAbc"]), f"흐림 표시가 없음: {r['onAbc']}"
    assert all(not b["d"] for b in r["afterLeave"]), f"이탈 시 enabled 복원 실패: {r['afterLeave']}"
    assert all(b["o"] == "" for b in r["afterLeave"]), f"이탈 시 흐림 복원 실패: {r['afterLeave']}"
    assert r["titleOnAbc"] and not r["titleAfter"], "title 안내가 붙거나 걷히지 않음"
    # ⚠️ 핵심: market 값을 건드리면 applyForcedMarket 저장/복원과 얽힌다
    assert r["market"] == "kr", f"applyMarketButtonsEnabled가 market을 바꿨다: {r['market']!r}"


def test_market_buttons_helper_never_calls_set_market():
    """표시 전용이어야 한다 — setMarket/market 대입이 있으면 실패."""
    fn = _code_only(_block("function applyMarketButtonsEnabled("))
    assert "setMarket" not in fn, "applyMarketButtonsEnabled는 market 값을 건드려선 안 된다"
    assert not re.search(r"\bmarket\s*=[^=]", fn), fn


def test_helper_is_wired_into_tab_view_state():
    """탭 전환마다 불려야 한다 — applyTabViewState()는 탭 클릭·초기 로드 양쪽에서 호출된다."""
    body = _block("function applyTabViewState()")
    assert "applyMarketButtonsEnabled(mode)" in body
    assert HTML.count("applyTabViewState();") >= 3   # 탭클릭·journal·초기로드
