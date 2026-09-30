"""ABC 탭 필터 칩 검사 (v5.290, 사용자 지시).

레이아웃만 바꾸고 **필터 동작·값·개수·기본값은 불변**임을 강제한다.
CLAUDE.md "텍스트 추출 + Node 실행" 레시피 — `_abcChip()`과 `abcFilteredHits()`를
production 소스에서 그대로 뽑아 `node`로 실행한다(재구현 금지).
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from test_helpers import code_only

ROOT = Path(__file__).resolve().parent
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node 미설치 — 도구 부재는 로직 결함과 다르다")


def _block(header: str) -> str:
    """중괄호 깊이를 세어 선언 전체를 잘라낸다(정규식으로는 중첩 중괄호를
    안전하게 못 자른다)."""
    start = HTML.index(header)
    i = HTML.index("{", start)
    depth = 0
    for j in range(i, len(HTML)):
        if HTML[j] == "{":
            depth += 1
        elif HTML[j] == "}":
            depth -= 1
            if depth == 0:
                return HTML[start:j + 1]
    raise AssertionError(header)


def _render_page_body() -> str:
    return _block("function renderAbcPage()")


# ── 1) 칩 구성: 값·개수·기본값 ────────────────────────────────────
# v5.291(사용자 지시): C단계 라벨이 MA600 기준으로 교체되고 🩷600돌파 토글은
# `🩷 강돌파` 단계로 **흡수**됐다(칩 10개 → 10개, 구성만 변경).
GRADE_CALLS = ["setAbcGrade('all')", "setAbcGrade('A급')", "setAbcGrade('B급')", "setAbcGrade('C급')"]
STAGE_CALLS = ["setAbcStage('all')", "setAbcStage('🩷 강돌파')", "setAbcStage('벽앞')",
               "setAbcStage('약돌파')", "setAbcStage('대기')", "setAbcStage('이탈')"]


def test_chip_count_and_values_unchanged():
    body = _render_page_body()
    calls = re.findall(r'"(set(?:AbcGrade|AbcStage)\([^"]*\))"', body)
    assert calls == GRADE_CALLS + STAGE_CALLS, calls
    # 칩 개수 고정 — 하나 지워도 통과하는 `in` 검사는 쓰지 않는다.
    assert body.count("_abcChip(") == 10, body.count("_abcChip(")
    labels = re.findall(r"_abcChip\('([^']*)'", body)
    assert labels == ["전체", "A급", "B급", "C급",
                      "전체", "🩷 강돌파", "벽앞", "약돌파", "대기", "이탈"], labels
    # v5.291: 흡수된 토글이 되살아나면 실패(같은 사건을 두 곳에서 거르면 어긋난다)
    # ⚠️ `code_only()`로 감싼다 — 제거를 설명하는 **주석**에 이름이 들어 있어
    # HTML 전체를 보면 오탐한다(CLAUDE.md 패턴 1, 이 세션에서 네 번째).
    code = code_only(HTML)
    assert "toggleAbcGateBreak" not in code, "🩷600돌파 토글이 되살아났다"
    assert "abcGateBreakOnly" not in code, "죽은 변수가 되살아났다"


def test_default_filter_values_unchanged():
    assert "let abcGradeFilter = 'all';" in HTML
    assert "let abcStageFilter = 'all';" in HTML


# ── 2) 레이아웃: 두 줄 칩 행, 전폭 버튼 금지 ──────────────────────
def test_chips_are_two_rows_and_not_full_width_buttons():
    body = _render_page_body()
    assert body.count('class="abc-chiprow"') == 2, "등급/C단계 각각 한 줄이어야 함"
    assert "journal-btn" not in code_only(_block("function _abcChip(")), (
        ".journal-btn은 width:100%라 칩이 전폭 블록이 된다 — 이 회귀가 v5.290의 수정 대상이다."
    )
    assert ".abc-chip{" in HTML and "width:auto" in _css(".abc-chip{")
    # 모바일 줄바꿈 허용 + 가로 스크롤 금지
    row = _css(".abc-chiprow{")
    assert "flex-wrap:wrap" in row, row
    assert "overflow-x" not in row and "nowrap" not in row, row


def _css(selector: str) -> str:
    i = HTML.index(selector)
    return HTML[i:HTML.index("}", i)]


def test_journal_btn_style_untouched():
    """일지 쪽 전폭 버튼은 그 스타일에 의존한다 — 같이 바꾸면 안 된다."""
    assert ".journal-btn{width:100%;margin-top:10px;padding:7px;background:transparent;" in HTML


def test_selected_state_uses_on_class_with_same_colors():
    chip = code_only(_block("function _abcChip("))
    assert "' on'" in chip, "선택 상태 표시가 사라짐"
    from test_helpers import resolve_colors
    on = resolve_colors(_css(".abc-chip.on{"), HTML)   # v5.304: 색 토큰을 기본(다크) 값으로 풀어 비교
    for color in ("#1b2a3a", "#3b6ea5", "#cfe6ff"):
        assert color in on, f"선택 색 {color}가 바뀜 — 선택 상태 표시 불변 요구사항 위반"


# ── 3) 필터 동작: production 함수를 node로 실행 ────────────────────
HITS = [
    {"ticker": "A.KQ", "grade": "A급", "c_stage": "🩷 강돌파"},
    {"ticker": "B.KQ", "grade": "B급", "c_stage": "약돌파"},
    {"ticker": "C.KS", "grade": "C급", "c_stage": "대기"},
    {"ticker": "D.KS", "grade": "A급", "c_stage": "이탈"},
    {"ticker": "E.KQ", "grade": "B급", "c_stage": "벽앞"},
    # 실데이터 형태: c_stage 누락 / grade 누락
    {"ticker": "F.KQ", "grade": "B급"},
    {"ticker": "G.KQ", "c_stage": "약돌파"},
]
# (grade, stage) → 통과 티커
CASES = [
    (("all", "all"), ["A.KQ", "B.KQ", "C.KS", "D.KS", "E.KQ", "F.KQ", "G.KQ"]),
    (("A급", "all"), ["A.KQ", "D.KS"]),
    (("B급", "all"), ["B.KQ", "E.KQ", "F.KQ"]),
    (("C급", "all"), ["C.KS"]),
    (("all", "🩷 강돌파"), ["A.KQ"]),
    (("all", "벽앞"), ["E.KQ"]),
    (("all", "약돌파"), ["B.KQ", "G.KQ"]),
    (("all", "대기"), ["C.KS"]),
    (("all", "이탈"), ["D.KS"]),
    (("A급", "🩷 강돌파"), ["A.KQ"]),
    (("B급", "🩷 강돌파"), []),
]


@needs_node
def test_filter_behaviour_unchanged():
    src = _block("function abcFilteredHits()")
    script = (
        "let _abcData, abcGradeFilter, abcStageFilter;\n"
        + src + "\n"
        + f"_abcData = {{hits: {json.dumps(HITS, ensure_ascii=False)}}};\n"
        + f"const cases = {json.dumps([list(c) for c, _ in CASES], ensure_ascii=False)};\n"
        + "console.log(JSON.stringify(cases.map(([g, s]) => {\n"
        + "  abcGradeFilter = g; abcStageFilter = s;\n"
        + "  return abcFilteredHits().map(h => h.ticker);\n"
        + "})));\n"
    )
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    expected = [e for _, e in CASES]
    assert got == expected, "\n".join(
        f"  {c}: 기대 {e} / 실제 {g}" for (c, e), g in zip(CASES, got) if e != g
    )
