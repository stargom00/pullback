"""v5.248 프론트 두 조각 검증:
1. `_historyPanelHtml(hist, market)` — /api/debug/{ticker}의 history
   섹션을 진단 패널에 렌더하는 순수 함수.
2. `_trackingWaitNote(r)` — "내 일지" 추적대기 하위 문구. 손절가 없으면
   "손절가 입력 필요"(새로고침으로 안 풀림), 손절 있고 가격만 미도착이면
   기존 "앱 새로고침 시 갱신" 유지(사용자 지시 — 새로고침 안내가 실제로
   맞는 경우까지 바꾸면 안 됨).

레시피: 텍스트 추출 + Node 실행, 재구현 아님."""
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
    if text[:start].endswith("async "):
        start -= len("async ")
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


HISTORY_PANEL_SRC = _extract_function("_historyPanelHtml")
TRACKING_WAIT_NOTE_SRC = _extract_function("_trackingWaitNote")

# _historyPanelHtml()이 참조하는 전역 fmtPrice의 최소 스텁(이 파일에서
# fmtPrice 자체의 정확성을 검증하는 게 아니라 history 패널이 그 결과를
# 올바르게 꽂아 넣는지만 보면 됨).
FMT_PRICE_STUB = """
const fmtPrice = (p, mkt) => mkt === 'KR'
  ? Math.round(p).toLocaleString('ko-KR') + '원'
  : '$' + p.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
"""


def _run_node(script: str):
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — 실행 테스트 스킵")
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=15)
    if res.returncode != 0:
        raise AssertionError(f"node 실행 실패:\n{res.stderr}")
    return res.stdout


def _history_html(hist, market="KR"):
    script = f"""
{FMT_PRICE_STUB}
{HISTORY_PANEL_SRC}
console.log(JSON.stringify(_historyPanelHtml({json.dumps(hist)}, {json.dumps(market)})));
"""
    return json.loads(_run_node(script))


def _wait_note(r):
    script = f"""
{TRACKING_WAIT_NOTE_SRC}
console.log(JSON.stringify(_trackingWaitNote({json.dumps(r)})));
"""
    return json.loads(_run_node(script))


# ---------------------------------------------------------------------------
# _historyPanelHtml
# ---------------------------------------------------------------------------

def test_history_panel_no_record():
    html = _history_html({"watch_snapshots": [], "sector_snapshot": [], "journal": [], "요약": "스캐너 기록 없음"})
    assert "스캐너 기록 없음" in html


def test_history_panel_none_returns_empty():
    assert _history_html(None) == ""


def test_history_panel_watch_snapshot_row():
    hist = {
        "watch_snapshots": [{"tab": "돌파임박", "signal_date": "2026-09-04", "pivot": 6030.0,
                             "stop": 5890.0, "status": "confirmed", "confirmed_at": "2026-09-07",
                             "confirm_close": 6410.0}],
        "sector_snapshot": [], "journal": [], "요약": "탭 히스토리 1건 · 섹터스냅샷 0건 · 저널 0건",
    }
    html = _history_html(hist, "KR")
    assert "돌파임박" in html
    assert "2026-09-04" in html
    assert "confirmed" in html
    assert "2026-09-07" in html
    assert "6,030원" in html   # 피벗
    assert "6,410원" in html   # confirm_close


def test_history_panel_sector_row_with_leader():
    hist = {
        "watch_snapshots": [],
        "sector_snapshot": [{"date": "2026-09-06", "sector": "철강", "hit_tabs": ["imminent"], "is_leader": True}],
        "journal": [], "요약": "x",
    }
    html = _history_html(hist)
    assert "2026-09-06" in html and "철강" in html
    assert "imminent" in html
    assert "👑" in html


def test_history_panel_journal_row_with_result():
    hist = {
        "watch_snapshots": [], "sector_snapshot": [],
        "journal": [{"date": "2026-09-05", "status": "entered", "entry": 6100, "stop": 5890,
                    "result_r": 1.5, "tab": "돌파임박"}],
        "요약": "x",
    }
    html = _history_html(hist)
    assert "entered" in html and "돌파임박" in html
    assert "1.5R" in html


def test_history_panel_journal_row_empty_result_r_no_r_suffix():
    hist = {
        "watch_snapshots": [], "sector_snapshot": [],
        "journal": [{"date": "2026-09-05", "status": "entered", "entry": 6100, "stop": 5890,
                    "result_r": "", "tab": "돌파임박"}],
        "요약": "x",
    }
    html = _history_html(hist)
    assert "R" not in html.split("진입")[-1].split("<")[0]   # 결과R 문구가 안 붙어야 함


# ---------------------------------------------------------------------------
# _trackingWaitNote
# ---------------------------------------------------------------------------

def test_no_stop_shows_input_required():
    """★ 핵심. 손절가가 없으면 새로고침 문구가 아니라 입력 필요 안내."""
    r = {"stop": None, "entry": 52200}
    assert _wait_note(r) == "손절가 입력 필요"


def test_no_stop_and_no_entry_still_shows_input_required():
    """entry도 없어도(대기 상태) stop이 없으면 우선 손절가 안내가 맞다."""
    r = {"stop": None, "entry": None}
    assert _wait_note(r) == "손절가 입력 필요"


def test_has_stop_no_price_shows_refresh_note():
    """손절은 있고(entry도 있고) 가격만 아직 안 왔으면 기존 새로고침 문구 유지."""
    r = {"stop": 51500, "entry": 52200}
    assert _wait_note(r) == "앱 새로고침 시 갱신"


def test_has_stop_no_entry_shows_nothing():
    r = {"stop": 51500, "entry": None}
    assert _wait_note(r) == ""
