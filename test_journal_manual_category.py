"""일지 "직접 추가"의 카테고리 판정 검사 (v5.289, 사용자 지시).

지키는 것: `tab`이 `저점`이면 `category='저점'`으로 저장되어 **추세추종·재량
어느 통계에도 섞이지 않는다.** 기존 매핑(재량→재량, 그 외→추세추종,
status='watch'→관찰)은 불변.

방법: CLAUDE.md의 "텍스트 추출 + Node 실행" 레시피(v5.233, `test_price_basis_note.py`
선례). `manualCategoryFor()` 소스를 `static/index.html`에서 **중괄호 깊이를 세어**
그대로 잘라내 `node -e`로 **production 코드 자체를 실행**한다 — Python으로 같은
로직을 다시 짜서 비교하면(재구현) 두 구현이 갈라질 수 있으므로 금지.
`node --check`(문법만 확인)로는 판정값을 검증할 수 없다.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


def _extract_block(src: str, header: str) -> str:
    """`header`로 시작하는 선언부터 대응하는 닫는 중괄호까지. 정규식이 아니라
    중괄호 깊이를 세는 이유: 중첩 중괄호가 있는 본문을 정규식으로는 안전하게
    자를 수 없다(`test_trace_const_audit.py`가 AST를 쓰는 것과 같은 이유 —
    "텍스트가 우연히 맞아떨어지는" 상황을 피한다)."""
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
    raise AssertionError(f"{header!r}의 닫는 중괄호를 못 찾음")


def _production_source() -> str:
    """판정에 필요한 production 선언 2개(매핑 상수 + 함수)를 그대로 가져온다."""
    const = _extract_block(HTML, "const MANUAL_CAT_BY_TAB =")
    fn = _extract_block(HTML, "function manualCategoryFor(")
    return const + ";\n" + fn + "\n"


# (status, tab, 기대 category)
CASES = [
    ("pending", "저점", "저점"),
    ("entered", "저점", "저점"),
    ("pending", "재량", "재량"),
    ("entered", "재량", "재량"),
    ("pending", "눌림목", "추세추종"),
    ("entered", "돌파", "추세추종"),
    ("entered", "돌파임박", "추세추종"),
    ("entered", "박스돌파", "추세추종"),
    ("entered", "추세전환", "추세추종"),
    # 관찰(status='watch')은 탭과 무관하게 항상 '관찰' — 저점도 예외 아님
    ("watch", "저점", "관찰"),
    ("watch", "재량", "관찰"),
    ("watch", "눌림목", "관찰"),
    # 실데이터 형태: tab이 빠져 있거나(undefined) 스캐너가 쓴 다른 값
    ("entered", None, "추세추종"),
    ("entered", "ABC", "추세추종"),
    ("entered", "재점화", "추세추종"),
]


def _run_node() -> list:
    src = _production_source()
    inputs = json.dumps([[s, t] for s, t, _ in CASES], ensure_ascii=False)
    script = (
        src
        + f"const cases = {inputs};\n"
        + "console.log(JSON.stringify(cases.map(([s, t]) => manualCategoryFor(s, t))));\n"
    )
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, f"node 실행 실패: {proc.stderr}"
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(shutil.which("node") is None, reason="node 미설치 — 도구 부재는 로직 결함과 다르다")
def test_manual_category_mapping():
    got = _run_node()
    expected = [e for _, _, e in CASES]
    assert got == expected, "\n".join(
        f"  status={s!r} tab={t!r}: 기대 {e!r} / 실제 {g!r}"
        for (s, t, e), g in zip(CASES, got) if e != g
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node 미설치")
def test_jeojeom_is_excluded_from_trend_and_discretion_stats():
    """`category='저점'`이 추세추종·단타·재량 통계 카드의 필터를 **어느 것도**
    통과하지 못하는지 — 통계 카드 필터식을 production 텍스트에서 확인한다.
    세 카드는 전부 정확한 문자열 동등비교라 '저점'은 구조적으로 빠진다."""
    # ⚠️ 검사 범위를 **통계 카드 3줄로 좁힌다.** 파일 전체를 보면
    # `saveEdit()`의 status 동기화(`=== '추세추종' || ... || === '저점'`)에
    # 걸려 오탐한다 — 그 OR은 정당하다(저점도 매매 카테고리라 status를
    # entered로 맞춰야 함). CLAUDE.md "검사 범위를 실행 코드 영역으로 좁힐 것"
    # 패턴을 이 파일을 쓰면서 실제로 한 번 밟았다.
    lines = [ln for ln in HTML.splitlines() if "journalStats(all.filter(" in ln]
    assert len(lines) == 3, f"통계 카드가 3개가 아님: {len(lines)}개 — 카드가 늘면 이 검사를 갱신할 것"
    block = "\n".join(lines)
    assert "(r.category || '추세추종') === '추세추종'" in block
    assert "(r.category || '추세추종') === '단타'" in block
    assert "r.category === '재량'" in block
    # 세 카드 전부 정확한 동등비교여야 한다 — OR이나 배열 포함검사가 생기면
    # '저점'이 섞여 들어올 수 있으므로 실패시킨다.
    assert "||" not in block.replace("(r.category || '추세추종')", ""), (
        f"통계 카드 필터에 OR 분기가 생겼음 — 저점이 섞일 수 있다:\n{block}"
    )
    assert "includes(" not in block and "저점" not in block


@pytest.mark.skipif(shutil.which("node") is None, reason="node 미설치")
def test_edit_form_keeps_jeojeom_selected():
    """편집 폼(`#e_cat`)에 '저점' option이 있고, 저장된 값이 그대로 선택되는지
    — option이 없으면 편집 시 값이 조용히 다른 카테고리로 바뀐다."""
    start = HTML.index('<select id="e_cat"')
    block = HTML[start:HTML.index("</select>", start)]
    values = re.findall(r'<option value="([^"]+)"', block)
    assert values == ["추세추종", "단타", "재량", "저점", "대기", "관찰"], values
    assert "<option value=\"저점\" ${cat==='저점'?'selected':''}>" in block, (
        "#e_cat의 저점 option에 selected 바인딩이 없으면 편집창을 열 때마다 "
        "첫 옵션(추세추종)으로 되돌아간다."
    )


def test_save_edit_status_sync_includes_jeojeom():
    """`saveEdit()`의 status 동기화가 '저점'을 매매 카테고리로 취급하는지 —
    빠지면 편집으로 저점을 고를 때 status가 pending/watch에 그대로 남아
    봇 감시 상태와 어긋난다(재량은 원래 이 분기에 있다)."""
    start = HTML.index("if (r.status !== 'closed') {")
    block = _extract_block(HTML, "if (r.status !== 'closed') {")
    for cat in ("'추세추종'", "'단타'", "'재량'", "'저점'"):
        assert f"r.category === {cat}" in block, f"status 동기화 분기에 {cat}이 없음"
    assert start > 0
