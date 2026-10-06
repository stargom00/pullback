"""ABC 탭 필터 칩 검사 (v5.290, 사용자 지시 · v5.332 등급 보기 개편).

v5.332 사보타주 확인(2026-10-06, FAIL 확인 후 원복):
① 필터가 등급을 고침(A급 보류 → A급) → test_filter_never_changes_grades FAIL
② 기본값을 'all'로 → test_default_filter_values_unchanged FAIL
③ D+ 정렬을 내림차순으로 → test_filter_behaviour_unchanged FAIL


v5.290: 레이아웃만 바꾸고 필터 동작·값·개수·기본값은 불변임을 강제했다. v5.332부터 등급 칩은 새 구성(A급만·A급+B급·전체, 기본 A급만)을 고정한다.
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
# v5.291(사용자 지시): C단계 라벨이 MA600 기준으로 교체되고 🩷600돌파 토글은 `🩷 강돌파` 단계로 흡수.
# v5.332(사용자 지시 "등급 필터 칩: [A급만] [A급+B급] [전체]. 기본값 A급만 … 각 칩에 건수"): 등급 칩은
# ABC_GRADE_VIEWS 하나에서 만들어진다(라벨·키 사본 없음). C단계 칩은 그대로.
STAGE_CALLS = ["setAbcStage('all')", "setAbcStage('🩷 강돌파')", "setAbcStage('벽앞')",
               "setAbcStage('약돌파')", "setAbcStage('대기')", "setAbcStage('이탈')"]


def test_chip_count_and_values_unchanged():
    body = _render_page_body()
    assert "...ABC_GRADE_VIEWS.map(([k, l]) => _abcChip(`${l} (${gcnt[k]})`, abcGradeFilter === k, `setAbcGrade('${k}')`))" in body
    assert "const ABC_GRADE_VIEWS = [['A', 'A급만'], ['AB', 'A급+B급'], ['all', '전체']];" in HTML
    calls = re.findall(r'"(setAbcStage\([^"]*\))"', body)
    assert calls == STAGE_CALLS, calls
    # 칩 개수 고정 — 하나 지워도 통과하는 `in` 검사는 쓰지 않는다(등급 1회(map) + C단계 6 + v5.335 📦 1 + v5.334 정렬 2)
    assert body.count("_abcChip(") == 10, body.count("_abcChip(")
    labels = re.findall(r"_abcChip\('([^']*)'", body)
    assert labels == ["전체", "🩷 강돌파", "벽앞", "약돌파", "대기", "이탈", "📦박스돌파만", "기본(D+ 순)", "테마 동반순"], labels
    # v5.291: 흡수된 토글이 되살아나면 실패(같은 사건을 두 곳에서 거르면 어긋난다)
    # ⚠️ `code_only()`로 감싼다 — 제거를 설명하는 **주석**에 이름이 들어 있어
    # HTML 전체를 보면 오탐한다(CLAUDE.md 패턴 1, 이 세션에서 네 번째).
    code = code_only(HTML)
    assert "toggleAbcGateBreak" not in code, "🩷600돌파 토글이 되살아났다"
    assert "abcGateBreakOnly" not in code, "죽은 변수가 되살아났다"


def test_default_filter_values_unchanged():
    assert "let abcGradeFilter = 'A';" in HTML          # v5.332: 기본 A급만(사용자 지시)
    assert "let abcStageFilter = 'all';" in HTML


# ── 2) 레이아웃: 두 줄 칩 행, 전폭 버튼 금지 ──────────────────────
def test_chips_are_two_rows_and_not_full_width_buttons():
    body = _render_page_body()
    assert body.count('class="abc-chiprow"') == 4, "검색/등급/C단계/정렬 각각 한 줄이어야 함(v5.334 검색·정렬 줄 추가)"
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
# v5.332: 등급 보기 'A'(A급+A급 보류)·'AB'·'all' · 정렬 = MA600 첫 돌파 D+ 오름차순(없으면 아래, 같으면 서버 순서)
HITS = [
    {"ticker": "A.KQ", "grade": "A급", "c_stage": "🩷 강돌파", "gate_break": {"bars_ago": 7}},
    {"ticker": "B.KQ", "grade": "B급", "c_stage": "약돌파", "gate_break": {"bars_ago": 2}},
    {"ticker": "C.KS", "grade": "C급", "c_stage": "대기", "gate_break": None},
    {"ticker": "D.KS", "grade": "A급 보류", "c_stage": "🩷 강돌파", "gate_break": {"bars_ago": 1}},
    {"ticker": "E.KQ", "grade": "B급", "c_stage": "벽앞"},
    # 실데이터 형태: c_stage 누락 / grade 누락 / bars_ago 누락
    {"ticker": "F.KQ", "grade": "B급", "gate_break": {"bars_ago": None}},
    {"ticker": "G.KQ", "c_stage": "약돌파"},
]
CASES = [
    (("all", "all"), ["D.KS", "B.KQ", "A.KQ", "C.KS", "E.KQ", "F.KQ", "G.KQ"]),
    (("A", "all"), ["D.KS", "A.KQ"]),                         # A급 보류 포함
    (("AB", "all"), ["D.KS", "B.KQ", "A.KQ", "E.KQ", "F.KQ"]),
    (("all", "🩷 강돌파"), ["D.KS", "A.KQ"]),
    (("all", "벽앞"), ["E.KQ"]),
    (("all", "약돌파"), ["B.KQ", "G.KQ"]),
    (("A", "약돌파"), []),
    (("AB", "약돌파"), ["B.KQ"]),
]


def _node(script):
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


FILTER_SRC = "let abcQuery = '';\nlet abcSortTheme = false;\nlet abcBoxOnly = false;\n" + "\n".join(_block(f"function {n}(") for n in (
    "abcSearchMatch", "abcGradeInView", "abcSortByBreakout", "abcThemeUpMax", "abcSortByTheme", "abcFilteredHitsBase",
    "abcFilteredHits", "abcGradeCounts"))
VIEWS_LINE = [l for l in HTML.splitlines() if l.startswith("const ABC_GRADE_VIEWS = ")][0]


@needs_node
def test_filter_behaviour_unchanged():
    script = (
        "let _abcData, abcGradeFilter, abcStageFilter;\n" + VIEWS_LINE + "\n" + FILTER_SRC + "\n"
        + f"_abcData = {{hits: {json.dumps(HITS, ensure_ascii=False)}}};\n"
        + f"const cases = {json.dumps([list(c) for c, _ in CASES], ensure_ascii=False)};\n"
        + "console.log(JSON.stringify(cases.map(([g, s]) => {\n"
        + "  abcGradeFilter = g; abcStageFilter = s;\n"
        + "  return abcFilteredHits().map(h => h.ticker);\n"
        + "})));\n"
    )
    got = _node(script)
    expected = [e for _, e in CASES]
    assert got == expected, "\n".join(
        f"  {c}: 기대 {e} / 실제 {g}" for (c, e), g in zip(CASES, got) if e != g
    )


@needs_node
@pytest.mark.parametrize("stage", ["all", "🩷 강돌파", "약돌파", "벽앞"])
def test_counts_match_lists_and_sum_to_total(stage):
    """칩 건수 = 그 보기로 실제 보이는 행 수. 서로소 분해(A + B급만 + 그 외 = 전체, AB = A + B급)가 맞는다."""
    script = (
        "let _abcData, abcGradeFilter, abcStageFilter;\n" + VIEWS_LINE + "\n" + FILTER_SRC + "\n"
        + f"_abcData = {{hits: {json.dumps(HITS, ensure_ascii=False)}}}; abcStageFilter = {json.dumps(stage, ensure_ascii=False)};\n"
        + "const c = abcGradeCounts(); const lens = {};\n"
        + "for (const [k] of ABC_GRADE_VIEWS) { abcGradeFilter = k; lens[k] = abcFilteredHits().length; }\n"
        + "const rows = _abcData.hits.filter(h => abcStageFilter === 'all' || h.c_stage === abcStageFilter);\n"
        + "const onlyB = rows.filter(h => h.grade === 'B급').length;\n"
        + "console.log(JSON.stringify({c, lens, total: rows.length, onlyB}));\n"
    )
    d = _node(script)
    assert d["c"] == d["lens"]
    assert d["c"]["all"] == d["total"] and d["c"]["AB"] == d["c"]["A"] + d["onlyB"]
    others = d["total"] - d["c"]["AB"]
    assert d["c"]["A"] + d["onlyB"] + others == d["c"]["all"]


@needs_node
def test_filter_never_changes_grades():
    """등급은 서버가 정한다 — 필터·건수·정렬이 등급 값(그리고 행 내용)을 바꾸지 않는다."""
    script = (
        "let _abcData, abcGradeFilter, abcStageFilter = 'all';\n" + VIEWS_LINE + "\n" + FILTER_SRC + "\n"
        + f"_abcData = {{hits: {json.dumps(HITS, ensure_ascii=False)}}};\n"
        + "const before = JSON.stringify(_abcData.hits);\n"
        + "const seen = {};\n"
        + "for (const [k] of ABC_GRADE_VIEWS) { abcGradeFilter = k; abcGradeCounts(); for (const h of abcFilteredHits()) seen[h.ticker] = h.grade ?? null; }\n"
        + "console.log(JSON.stringify({same: before === JSON.stringify(_abcData.hits), seen}));\n"
    )
    d = _node(script)
    assert d["same"], "필터가 서버 등급(행)을 바꿨다"
    assert d["seen"] == {h["ticker"]: h.get("grade") for h in HITS}


def test_empty_view_offers_full_list():
    body = _render_page_body()
    assert "없음 — <button type=\"button\" class=\"abc-chip\" onclick=\"setAbcGrade('all')\">전체 보기</button>" in body
    assert "setAbcGrade(g) { abcGradeFilter = g; renderAbcPage(); writeTabHash(); }" in HTML     # 선택은 URL 해시에
