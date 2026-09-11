"""saveJournal() 저장 직전 "이 카드 가격이 오래됐을 수 있다" 확인 절차
검증 (v5.245, 사용자 지시 — 한화생명 09:15 스캔가 vs 실제가 2.3% 괴리
사고 후속).

`_staleGateApplies(wouldEnter, priceStaleLive)`/`_staleConfirmMessage
(closePrice, staleNote, freshPrice)` 두 순수 함수(static/index.html)를
텍스트 그대로 추출해 Node로 실행 — saveJournal() 자체는 DOM/fetch/
confirm 의존이라 전체 실행 대신 이 두 순수 로직만 검증하고, saveJournal()
이 실제로 이 함수들을 호출하는지는 별도로 소스 텍스트 확인한다
(test_entry_gate_bypass_note.py와 동일 레시피)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
INDEX_PATH = ROOT / "static" / "index.html"


def _extract_function(name: str) -> str:
    text = INDEX_PATH.read_text(encoding="utf-8")
    marker = f"function {name}("
    start = text.find(marker)
    assert start != -1, f"static/index.html에서 `{marker}`를 못 찾음"
    paren_start = text.index("(", start)
    depth = 0
    paren_end = None
    for i in range(paren_start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                paren_end = i
                break
    assert paren_end is not None
    brace_start = text.index("{", paren_end)
    depth = 0
    for i in range(brace_start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise AssertionError(f"`{name}` 함수의 닫는 중괄호를 못 찾음")


STALE_GATE_APPLIES_SRC = _extract_function("_staleGateApplies")
STALE_CONFIRM_MESSAGE_SRC = _extract_function("_staleConfirmMessage")


def _run_node(script: str):
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — 실행 테스트 스킵")
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=15)
    if res.returncode != 0:
        raise AssertionError(f"node 실행 실패:\n{res.stderr}")
    return res.stdout


def _stale_gate_applies(would_enter, price_stale_live):
    script = f"""
{STALE_GATE_APPLIES_SRC}
console.log(JSON.stringify(_staleGateApplies({json.dumps(would_enter)}, {json.dumps(price_stale_live)})));
"""
    return json.loads(_run_node(script))


def _stale_confirm_message(close_price, stale_note, fresh_price):
    script = f"""
{STALE_CONFIRM_MESSAGE_SRC}
console.log(JSON.stringify(_staleConfirmMessage({json.dumps(close_price)}, {json.dumps(stale_note)}, {json.dumps(fresh_price)})));
"""
    return json.loads(_run_node(script))


# ---------------------------------------------------------------------------
# _staleGateApplies
# ---------------------------------------------------------------------------

def test_gate_applies_when_would_enter_and_live():
    assert _stale_gate_applies(True, True) is True


def test_gate_not_applied_when_not_would_enter():
    """관찰/대기·돌파계열은 즉시 진입이 아니라 게이트 자체가 무의미."""
    assert _stale_gate_applies(False, True) is False


def test_gate_not_applied_when_not_live():
    """장마감 후(priceStaleLive=False)는 재조회해도 같은 종가라 트리거 안 됨."""
    assert _stale_gate_applies(True, False) is False


def test_gate_not_applied_when_neither():
    assert _stale_gate_applies(False, False) is False


# ---------------------------------------------------------------------------
# _staleConfirmMessage
# ---------------------------------------------------------------------------

def test_confirm_message_shows_fresh_price_and_diff_pct():
    msg = _stale_confirm_message(5980, "📸 09:12 기준", 5840)
    assert "5980" in msg
    assert "5840" in msg
    assert "-2.3%" in msg or "-2.4%" in msg   # 반올림 여유


def test_confirm_message_shows_positive_diff_with_sign():
    msg = _stale_confirm_message(100, "📸 09:12 기준", 110)
    assert "+10%" in msg


def test_confirm_message_network_failure_fail_open_wording():
    """freshPrice=null(재조회 실패)이면 네트워크 오류 문구가 뜨고,
    그래도 진행할지 물어야 한다(막지 않음)."""
    msg = _stale_confirm_message(5980, "📸 09:12 기준", None)
    assert "네트워크" in msg or "확인하지 못했" in msg
    assert "5980" in msg
    assert "그래도" in msg


def test_confirm_message_includes_stale_note_text():
    msg = _stale_confirm_message(5980, "📸 09:12 기준", 5840)
    assert "09:12" in msg


def test_confirm_message_falls_back_when_note_missing():
    msg = _stale_confirm_message(5980, None, 5840)
    assert "스캔 당시" in msg


# ---------------------------------------------------------------------------
# saveJournal()이 실제로 이 두 함수를 쓰는지(소스 확인, DOM 실행 없이).
# ---------------------------------------------------------------------------

def test_save_journal_uses_stale_gate_functions():
    text = INDEX_PATH.read_text(encoding="utf-8")
    start = text.index("async function saveJournal()")
    end = text.index("async function", start + 10)
    body = text[start:end]
    assert "_staleGateApplies(" in body
    assert "_staleConfirmMessage(" in body
    assert "/api/prices" in body


def test_card_template_renders_price_stale_note():
    """눌림목 등 메인 5탭이 쓰는 카드 템플릿(function card(s))에
    price_stale_note가 조건부로 렌더되는지 — 종가베팅/붕괴/검색결과
    전용 카드(jonggaCard/breakdownCard/searchResultCard)는 이번
    범위 밖이라 확인 안 함."""
    body = _extract_function("card")
    assert "price_stale_note" in body
