"""v5.285 — 시총 필터 상태 배지: 값별 색/문구 + **캘린더에도** 렌더.

[사고] 2026-09-24 재배포 직후 스캔이 fail_open이었는데 화면엔 아무 표시도
없었다. 배지 함수(`_krMcapFilterBadgeHtml`)는 있었지만 **호출부가 스캔 탭의
status 줄 하나뿐**이었고, 캘린더(첫 화면)는 서버가 보내주는
`immediate_pipeline_health.kr_mcap_filter_source`를 아예 읽지 않았다.

판정 로직은 재구현하지 않고 `static/index.html`에서 함수 텍스트를 그대로
꺼내 node로 실행한다(CLAUDE.md "텍스트 추출 + Node 실행" 레시피).
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
TEXT = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


def _fn(name: str) -> str:
    i = TEXT.index(f"function {name}(")
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


def _badge(tm) -> str:
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    src = _fn("_krMcapFilterBadgeHtml")
    harness = f"{src}\nconsole.log(JSON.stringify(_krMcapFilterBadgeHtml({json.dumps(tm)})));"
    p = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip())


def test_fail_open_is_red():
    html = _badge({"kr_mcap_filter_source": "fail_open"})
    assert "#FF6B6B" in html and "미적용" in html


def test_stale_disk_is_yellow_and_says_the_filter_is_on():
    html = _badge({"kr_mcap_filter_source": "stale_disk"})
    assert "#f2b33d" in html, html
    assert "#FF6B6B" not in html, "stale_disk는 빨강이면 안 된다(fail_open과 구분)"
    assert "낡음" in html


def test_normal_and_missing_states_render_nothing():
    # v5.287: disk_current(= 이번 슬롯 목록을 디스크에서 복원)도 경고 아님.
    for tm in ({"kr_mcap_filter_source": "mobile_api"},
               {"kr_mcap_filter_source": "disk_current"},
               {"kr_mcap_filter_source": None}, {}, None):
        assert _badge(tm) == "", tm


def test_calendar_renders_the_badge_from_pipeline_health():
    """캘린더 렌더 경로에 실제로 호출부가 있어야 한다 — 함수만 있고 아무도
    안 부르던 것이 이번 사고의 형태다."""
    assert "_krMcapFilterBadgeHtml(data.immediate_pipeline_health)" in TEXT
    docTop = TEXT[TEXT.index("docTop.innerHTML = `"):]
    docTop = docTop[:docTop.index("`;")]
    assert "${mcapBadgeHtml}" in docTop, docTop


def test_both_screens_share_one_badge_function():
    """스캔 탭·캘린더가 각자 판정하면 또 갈라진다 — 호출부는 2곳, 정의는 1곳."""
    assert TEXT.count("function _krMcapFilterBadgeHtml(") == 1
    assert TEXT.count("_krMcapFilterBadgeHtml(") >= 3   # 정의 1 + 호출 2
