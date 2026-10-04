"""v5.316 — 홈 재배치: "오늘 할 일"·"후보" 카드 제거, 저점종목 카드를 홈 상단(왼쪽 첫 카드)으로.

사용자 지시 요지: "오늘할일 카드는 1% ATR 종목만 떠서 안 보게 됨 — 후보 카드와 함께 홈에서 제거.
저점종목 카드는 그 위 자리(홈 상단)로 이동."

공유 여부를 먼저 확인했다: renderTodayDecisionHtml과 그 안의 카드·배지·안내·액션(_todayDecisionMap,
decisionOpenJournal/QuickWatch, _decisionHitShape)·종가베팅 시각 사본·후보 접기 상태·수량 헬퍼
(calcSharesGateCap)는 홈에서만 쓰여 지웠다. renderScenarioHtml(일지·내 추적)·scenarioMemoText
(일지 모달)·GATE_LABEL(시장 타일)·ATR_STOP_MULT(스캔 카드)는 공유라 남겼다. 서버의 today_decision
계산·/api/calendar는 그대로다.

사보타주 확인(2026-10-03, FAIL 확인 후 원복): renderCalendar에서 저점종목 카드를 예전 자리
(오른쪽 열 섹터 가속 아래)로 되돌림 → test_lowpoint_card_is_first_card_on_home FAIL
"""
import re
from pathlib import Path

TEXT = (Path(__file__).resolve().parent / "static" / "index.html").read_text(encoding="utf-8")


def _fn(name):
    i = TEXT.index(f"function {name}(")
    b = TEXT.index("{", TEXT.index(")", i))
    d = 0
    for k in range(b, len(TEXT)):
        d += {"{": 1, "}": -1}.get(TEXT[k], 0)
        if d == 0:
            return TEXT[i:k + 1]
    raise AssertionError(name)


def _code_only(src):
    """주석(// 줄, /* */, <!-- -->)을 뺀 실행 코드 — 변경 이력 인용문에 걸리는 오탐 방지."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"<!--.*?-->", "", src, flags=re.S)
    return "\n".join(l for l in src.split("\n") if not l.strip().startswith("//"))


CAL = _fn("renderCalendar")


def test_lowpoint_card_is_first_card_on_home():
    """왼쪽 열 = 경고(있을 때만) → 저점종목 → 내 추적. 오른쪽 열엔 저점종목이 없다."""
    m = re.search(r"docTop\.innerHTML = `([^`]*)`;", CAL)
    assert m, "docTop 조립식을 못 찾음"
    parts = re.findall(r"\$\{([^}]*)\}", m.group(1))
    assert parts == ["warnHtml", "renderLowpointHtml(data.lowpoint)", "myTrackBoardHtml"], parts
    side = CAL[CAL.index("docSide.innerHTML"):]
    side = side[:side.index(";")]
    assert "renderLowpointHtml" not in side
    assert ["renderJonggaForwardCard", "renderSectorAccelCard"] == \
        re.findall(r"(render\w+)\(", side)   # v5.322: 다가오는 일정 카드는 달력에 흡수(test_home_calendar.py)
    assert CAL.count("renderLowpointHtml(") == 1


def test_todo_and_candidate_cards_are_gone_from_home():
    cal_code = _code_only(CAL)
    assert "renderTodayDecisionHtml" not in cal_code and "today_decision" not in cal_code
    code = _code_only(TEXT)
    for gone in ("renderTodayDecisionHtml", "todayDecisionRiskBadge", "_tdBadgeStats", "priceBasisNoteText",
                 "reignitionChecklistTooltip", "TODAY_DECISION_INFO", "_candidatesExpanded",
                 "toggleCandidatesExpanded", "_todayDecisionMap", "decisionOpenJournal", "decisionQuickWatch",
                 "_decisionHitShape", "US_PULLBACK_SHOW_N", "_tdWaitingLabel", "calcSharesGateCap",
                 "JONGGA_BUY_WINDOW", "JONGGA_SELL_RULE", "JONGGA_SELL_CELL"):
        assert gone not in code, f"홈 전용 코드가 남아 있다: {gone}"
    for cls in ('class="todo', 'class="n-card cand', 'aria-label="즉시 행동"', 'aria-label="후보"'):
        assert cls not in TEXT, cls
    assert not re.search(r"(?m)^\s*\.(todo|cand)\b", TEXT), "제거한 카드의 CSS가 남아 있다"


def test_shared_helpers_kept_and_still_used_elsewhere():
    """공유 코드는 남아야 한다 — 다른 탭 회귀 방지."""
    assert TEXT.count("renderScenarioHtml(") >= 3          # 정의 + 일지 + 내 추적
    assert "renderScenarioHtml(" in _fn("renderMyTrackBoard")
    assert "scenarioMemoText(" in _fn("openJournal")
    assert "const GATE_LABEL = {" in TEXT and "GATE_LABEL[" in _fn("renderHomeTiles")
    assert "const ATR_STOP_MULT = 1.5;" in TEXT and "ATR_STOP_MULT" in _fn("riskPctMiniHtml")


def test_home_markup_comments_match_layout():
    assert "왼쪽: 경고(있을 때만) → 저점종목 → 내 추적" in TEXT
    assert "오른쪽: 달력 → 메모 → 종가베팅 실전 N/30 → 섹터 가속" in TEXT   # v5.322
