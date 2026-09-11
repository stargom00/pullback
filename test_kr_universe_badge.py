"""KR 유니버스 축소 화면 배지 — _krUniverseBadgeHtml() 검증 (v5.246,
사용자 지시). "정적 폴백으로 내려앉으면 화면에도 보이게 할지 판단해서
제안"에 대해 제안·승인된 대로 구현 — static_fallback일 때만 상태줄에
경고 배지, dynamic/unknown/timing 없음은 전부 빈 문자열(배지 없음).

레시피: 텍스트 추출 + Node 실행(CLAUDE.md, test_price_basis_note.py 등
선례) — static/index.html에서 그대로 추출해 실행, 재구현 아님."""
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


BADGE_SRC = _extract_function("_krUniverseBadgeHtml")


def _run_node(script: str):
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — 실행 테스트 스킵")
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=15)
    if res.returncode != 0:
        raise AssertionError(f"node 실행 실패:\n{res.stderr}")
    return res.stdout


def _badge_html(tm):
    script = f"""
{BADGE_SRC}
console.log(JSON.stringify(_krUniverseBadgeHtml({json.dumps(tm)})));
"""
    return json.loads(_run_node(script))


def test_static_fallback_shows_badge():
    html = _badge_html({"kr_universe_source": "static_fallback", "kr_universe_dynamic_count": 0})
    assert html != ""
    assert "KR 유니버스 축소" in html
    assert "⚠️" in html


def test_dynamic_shows_no_badge():
    html = _badge_html({"kr_universe_source": "dynamic", "kr_universe_dynamic_count": 1499})
    assert html == ""


def test_unknown_shows_no_badge():
    html = _badge_html({"kr_universe_source": "unknown", "kr_universe_dynamic_count": 0})
    assert html == ""


def test_missing_timing_shows_no_badge():
    """timing 필드 자체가 없는 응답(예: jongga 전용 뷰)에서도 크래시 없이 빈 문자열."""
    assert _badge_html(None) == ""


def test_status_bar_template_calls_badge_function():
    text = INDEX_PATH.read_text(encoding="utf-8")
    start = text.index("async function load(refresh = false) {")
    end = text.index("\nasync function", start + 10)
    body = text[start:end]
    assert "_krUniverseBadgeHtml(_tm)" in body
