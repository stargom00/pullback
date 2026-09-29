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


# ── 3단계: 내 일지 ─────────────────────────────────────────────────
def _fn(name: str) -> str:
    i = TEXT.index(f"function {name}(")
    b = TEXT.index("{", i)
    d = 0
    for k in range(b, len(TEXT)):
        d += {"{": 1, "}": -1}.get(TEXT[k], 0)
        if d == 0:
            return TEXT[i:k + 1]
    raise AssertionError(name)


def test_tab_owned_banners_are_hidden_on_tab_switch():
    """종가베팅 배너가 일지에 남던 원인 — 표시가 renderCards()에서만 정해졌다.
    탭 전환(applyTabViewState)에서 주인 탭이 아니면 숨겨야 한다."""
    assert ("const TAB_OWNED_BANNERS = { jonggaSafetyBanner: 'jongga', "
            "jonggaSessionBanner: 'jongga', stopWidthWarnBanner: 'pullback' };") in TEXT
    src = _fn("applyTabViewState")
    assert "Object.entries(TAB_OWNED_BANNERS)" in src and "mode !== owner" in src
    assert "IDXBAR_HIDDEN_MODES.has(mode)" in src
    assert "const IDXBAR_HIDDEN_MODES = new Set(['calendar', 'journal']);" in TEXT


def test_journal_table_is_six_columns_everywhere():
    src = _fn("renderJournal")
    thead = src[src.index("<thead>"):src.index("</thead>")]
    assert thead.count("<th>") == 6, thead
    for fn in ("viewRow", "renderWatchRows", "renderClosedMonths",
               "renderArchivedPendingSection", "partialRow", "editRow"):
        body = _fn(fn)
        spans = [int(x) for x in re.findall(r'colspan="(\d+)"', body)]
        for n in spans:
            assert n in (2, 5, 6), f"{fn}: colspan {n}"
    # editRow: 일반 td 4개 + colspan="2" 1개 = 6열
    er = _fn("editRow")
    assert er.count("<td") == 5 and 'colspan="2"' in er, er.count("<td")


def test_row_menu_keeps_every_existing_action():
    src = _fn("viewRow")
    for call in ("markWatchEntered(", "markWatchMissed(", "markWatchClosed(", "reopenWatch(",
                 "markEntered(", "markMissed(", "partialEditingId=", "markClosed(",
                 "revertToPending(", "reopenRow(", "restoreArchivedPending(",
                 "editingId=", "delJournal("):
        assert call in src, call


def test_journal_title_menu_keeps_tools():
    src = _fn("renderJournal")
    for call in ("refreshPrices()", "openManualAdd()", "exportCSV()",
                 "Notification.requestPermission()", "setJournal([], { deletedIds: ids })", "openRSettings()"):
        assert call in src, call


def test_journal_category_chips():
    src = _fn("renderJournal")
    chips = re.findall(r"catBtn\('([^']+)', '([^']+)'\)", src)
    assert [c[1] for c in chips] == ["전체", "추세", "단타", "재량", "저점", "관찰"], chips


def test_category_stats_reuse_journal_stats_only():
    """카테고리 성적 표는 기존 statcard 값 재배치 — 새 통계 함수 호출이 없어야 한다."""
    src = _fn("renderJournal")
    assert src.count("journalStats(") == 3   # 추세추종·단타·재량 (v5.187과 동일)


# ── v5.295: 숫자 타일 85% · 내 추적 grid ────────────────────────────
def _css_rule(sel: str) -> str:
    i = TEXT.index(sel + "{")
    return TEXT[i:TEXT.index("}", i)]


def test_number_tiles_scaled_through_one_variable():
    assert "--n-k:.85;" in TEXT
    # (선택자, v5.294 기존 px) — 큰 숫자가 기존값 × --n-k로 계산되는가
    for sel, px in ((".tile-val .v", 26), (".jr-risk .v", 22), (".jr-big", 26),
                    (".todo-name .nm", 24), (".todo-cells .v", 20)):
        rule = _css_rule(sel)
        assert f"font-size:calc({px}px * var(--n-k))" in rule, (sel, rule)
    for sel in (".tile", ".jr-risk>div", ".todo", ".todo-cells>div"):
        assert "var(--n-k)" in _css_rule(sel), sel
    # R 누적 큰 숫자가 인라인 고정 px로 남아 있지 않다
    assert "font-size:26px;font-weight:600" not in TEXT
    # 라벨 글자는 그대로
    assert "font-size:12px" in _css_rule(".jr-risk .k")


def test_track_header_and_rows_share_one_grid():
    row = _css_rule(".track-row")
    assert "grid-template-columns:var(--track-cols)" in row
    head = _css_rule(".track-head")
    assert "grid-template" not in head, "헤더가 행과 다른 열 정의를 쓴다"
    fn = _fn("renderMyTrackBoard")
    assert 'class="track-row track-head"' in fn


def test_gate_line_does_not_repeat_gate_label():
    fn = _fn("renderJournal")
    assert "· 게이트 ${gateLabel}" not in fn
    assert "계좌 성과는 포지션 탭" not in fn


# ── v5.296: 최대 폭 · US 티커 · US 유니버스 ─────────────────────────
def test_max_width_single_variable_applied_to_header_home_journal():
    assert "--n-maxw:1280px;" in TEXT
    assert "--n-gutter:max(40px, calc((100% - var(--n-maxw)) / 2 + 40px));" in TEXT
    for sel in (".hdr-row", ".hdr-strip", ".jrnl-wrap.home", "#journalView"):
        assert "var(--n-gutter)" in _css_rule(sel), sel


def test_us_ticker_suffix_runs():
    import shutil, subprocess, json as _j
    if not shutil.which("node"):
        import pytest
        pytest.skip("node 미설치")
    js = ("function _escapeHtml(s){return s;}\n" + _fn("usTickerSuffix") +
          "\nconsole.log(JSON.stringify({us: usTickerSuffix('BNS','US'), kr: usTickerSuffix('005930.KS','KR')}));")
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    got = _j.loads(out.stdout)
    assert "BNS" in got["us"] and got["kr"] == ""
    # 홈 세 곳(오늘 할 일·후보 카드·내 추적)이 실제로 부른다
    assert TEXT.count("usTickerSuffix(") >= 5


def test_us_universe_has_no_bare_numeric_codes():
    """접미사 없는 6자리 KR 코드가 watchlist에서 US로 새어 들어오던 사고(v5.296)."""
    import sys
    sys.path.insert(0, str(ROOT))
    import universe
    us = universe.get_universe("us")
    bad = [t for t in us if re.fullmatch(r"\d{5}[0-9A-Z]", t)]
    assert not bad, bad


# ── v5.297: 탭 필터는 헤더 밖 · 전 탭 본문 끝선 · 재점화 감시 블록 제거 ──
def test_tab_filters_live_outside_header():
    hdr = _header()
    for bid in ("strictToggle", "riskSortToggle", "riskTierDropdownToggle", "bearOkToggle",
                "accumSortToggle", "superOnlyToggle", "hiddenToggle"):
        assert f'id="{bid}"' not in hdr, f"{bid}가 헤더 안에 있다(겹침 원인)"
        assert TEXT.count(f'id="{bid}"') == 1, bid
    bar = TEXT[TEXT.index('id="filterToggles"'):]
    bar = bar[:bar.index('id="confirmEntryBanner"')]
    for bid in ("strictToggle", "hiddenToggle", "riskTierBar"):
        assert f'id="{bid}"' in bar, bid
    assert TEXT.index('id="hdrStrip"') < TEXT.index('id="filterToggles"')


def test_every_tab_body_uses_the_same_gutter():
    i = TEXT.index("@media (min-width:900px){\n  .statusbar,.idxbar")
    block = TEXT[i:TEXT.index("\n}\n", i)]
    for sel in (".statusbar", ".idxbar", ".sectorbar", ".alert-panel", ".psubbar", ".jrnl-wrap",
                "#searchBar", "#diagBar", "#content"):
        assert sel in block, sel
    assert "var(--n-gutter)" in block
    assert "--n-maxw:" in TEXT and TEXT.count("--n-maxw:") == 1, "새 폭 변수를 만들지 말 것"


def test_reignition_watch_block_is_gone_but_data_kept_for_home():
    for gone in ("function reignitionPullbackSectionHtml", "function reignitionManualRefresh",
                 "function reignitionRefreshPanelHtml", "재점화 감시 현황 (${"):
        assert gone not in TEXT, gone
    rc = _fn("renderCards")
    assert "reignitionPullback" not in rc
    # 홈 📌 내 추적은 같은 데이터를 계속 쓴다
    assert "function loadReignitionPullbackStatus(" in TEXT
    assert "myTrackReignitionCandidates()" in _fn("renderMyTrackBoard")
