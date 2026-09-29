"""v5.294 — 헤더·홈·내 일지 재디자인(사용자 지시, 시안 docs/design/2026-09-29/).

재배치만 하고 **키·저장값·시장 강제 규칙은 그대로**여야 한다는 게 핵심 제약이다.
여기서 정적으로 고정하는 것:
  · 메인 탭 줄이 정확히 5개(홈·US눌림목·ABC·추세전환·더보기▾)
  · 기존 data-mode 키 25개가 전부 **정확히 한 번씩** 남아 있음(더보기로
    옮기다 하나 빠지면 그 탭은 조용히 사라진다)
  · FORCED_MARKET_BY_MODE 불변
  · #dailyNoteBox·#calSearchBox가 #calendarDocTop **밖**(renderCalendar가
    다시 그릴 때 입력이 끊기지 않게 — v5.205/v5.224 규칙)
  · 헤더에 보이는 글자에 이모지 없음

사보타주 확인(2026-09-29): ① #dailyNoteBox를 #calendarDocTop 안으로 옮기면
test_static_inputs_outside_calendar_doc_top FAIL ② 더보기 패널에서 data-mode
하나(ibd9)를 지우면 test_every_existing_mode_key_exists_exactly_once FAIL —
둘 다 확인 후 원복했다.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEXT = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

# v5.293 시점의 data-mode 키 전부(git show v5.293:static/index.html 기준).
EXISTING_MODES = {
    "calendar", "themes", "pullback", "abc", "jongga", "turnaround",
    "imminent", "boxbreak", "breakout", "surge_observe", "eod", "positions",
    "journal", "super", "leader", "sectors", "moneyflow", "inverse",
    "breakdown", "pattern", "stage2", "ibd9", "strong_pivot", "earnings", "surge",
}


def _header() -> str:
    return TEXT[TEXT.index('<header class="hdr">'):TEXT.index("</header>") + len("</header>")]


def _element(elem_id: str, close: str) -> str:
    i = TEXT.index(f'id="{elem_id}"')
    return TEXT[i:TEXT.index(close, i)]


def test_main_tab_row_is_exactly_five_buttons():
    body = _element("modeTabs", "</nav>")
    labels = [re.sub(r"<[^>]+>", "", b).strip()
              for b in re.findall(r"<button\b[^>]*>(.*?)</button>", body, re.S)]
    assert labels == ["홈", "US눌림목", "ABC", "추세전환", "더보기"], labels


def test_every_existing_mode_key_exists_exactly_once():
    buttons = re.findall(r'<button class="tab[^"]*" data-mode="(\w+)"', TEXT)
    missing = EXISTING_MODES - set(buttons)
    assert not missing, f"사라진 탭: {sorted(missing)}"
    dup = {m for m in buttons if buttons.count(m) > 1}
    assert not dup, f"두 번 있는 탭: {sorted(dup)}"
    assert set(buttons) == EXISTING_MODES, f"새로 생긴 키: {sorted(set(buttons) - EXISTING_MODES)}"


def test_every_mode_button_lives_in_one_of_the_three_menus():
    """메인·오른쪽 메뉴·더보기 패널 밖에 떠도는 탭 버튼이 없어야 한다."""
    main = set(re.findall(r'data-mode="(\w+)"', _element("modeTabs", "</nav>")))
    util = set(re.findall(r'data-mode="(\w+)"', _element("utilTabs", "</nav>")))
    more = set(re.findall(r'data-mode="(\w+)"', _element("moreTabsPanel", "</header>")))
    assert main | util | more == EXISTING_MODES
    assert not (main & util) and not (main & more) and not (util & more)


def test_forced_market_by_mode_unchanged():
    assert "const FORCED_MARKET_BY_MODE = { jongga: 'kr', pullback: 'us' };" in TEXT


def test_journal_tab_storage_labels_unchanged():
    """일지 `tab` 필드로 저장되는 식별자 — 라벨을 따라 바뀌면 기존 레코드가 끊긴다."""
    i = TEXT.index("const MODE_TAB_LABEL = {")
    block = TEXT[i:TEXT.index("}", i)]
    for pair in ("pullback: '눌림목'", "jongga: '종가베팅'", "imminent: '돌파임박'",
                 "boxbreak: '박스돌파'", "breakout: '돌파'", "turnaround: '추세전환'"):
        assert pair in block, pair


def test_static_inputs_outside_calendar_doc_top():
    """renderCalendar()는 #calendarDocTop.innerHTML을 통째로 다시 쓴다 — 그 안에
    입력창이 있으면 시장 필터 클릭마다 입력·포커스가 날아간다."""
    view = TEXT[TEXT.index('id="calendarView"'):]
    view = view[:view.index('<div class="modal-bg"')]
    top_open = view.index('<div id="calendarDocTop">')
    top_close = view.index("</div>", top_open)   # calendarDocTop은 정적 HTML상 빈 div
    for box in ("dailyNoteBox", "calSearchBox"):
        pos = view.index(f'<div id="{box}">')
        assert not (top_open < pos < top_close), f"#{box}가 #calendarDocTop 안에 있다"
        assert TEXT.count(f'id="{box}"') == 1
    # 렌더러가 문자열로 만들어 넣는 경우도 막는다
    assert "id=\"dailyNoteBox\"" not in TEXT[TEXT.index("function renderCalendar("):][:20000]


_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿\U0001F000-\U0001F2FF]")


def test_no_emoji_in_visible_header_text():
    h = re.sub(r"<!--.*?-->", "", _header(), flags=re.S)
    visible = re.sub(r"<[^>]+>", " ", h)          # 태그(속성 포함) 제거 → 보이는 글자만
    found = _EMOJI.findall(visible)
    assert not found, f"헤더에 이모지: {found}"


# ── 2단계: 홈 ──────────────────────────────────────────────────────
def test_jongga_sell_rule_matches_app():
    """홈 '오늘 할 일' 매도 칸 문구는 app.py JONGGA_SELL_RULE의 사본 — 어긋나면 FAIL."""
    app_src = (ROOT / "app.py").read_text(encoding="utf-8")
    m = re.search(r'^JONGGA_SELL_RULE = "([^"]+)"', app_src, re.M)
    assert m
    assert f"const JONGGA_SELL_RULE = '{m.group(1)}';" in TEXT


def test_home_removed_blocks_are_gone():
    fn = TEXT[TEXT.index("function renderCalendar(data) {"):]
    fn = fn[:fn.index("\n}\n")]
    for gone in ("homeStripBar", "positions_summary", "renderSectorFlowHtml"):
        assert gone not in fn, gone
    assert "IDXBAR_HIDDEN_MODES = new Set(['calendar'" in TEXT


def test_home_column_order():
    fn = TEXT[TEXT.index("function renderCalendar(data) {"):]
    fn = fn[:fn.index("\n}\n")]
    assert "docTop.innerHTML = `${warnHtml}${td.immediateHtml}${td.candidateHtml}${myTrackBoardHtml}`;" in fn
    side = fn[fn.index("docSide.innerHTML ="):]
    order = [side.index(x) for x in ("renderJonggaForwardCard", "renderSectorAccelCard",
                                     "renderLowpointHtml", "renderUpcomingCard")]
    assert order == sorted(order)
