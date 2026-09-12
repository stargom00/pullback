"""v5.253 ⛔ 피벗 밀착 기본 접힘 — 분류 로직과 적용 범위 검증.

CLAUDE.md "텍스트 추출 + Node 실행" 레시피: static/index.html의 실제 함수
`pivotHuggingDistAtr`을 중괄호 깊이로 잘라내 node로 **그대로 실행**한다
(Python 재구현 금지 — 두 구현이 갈라질 위험을 원천 차단).

검증 대상 2가지:
  1. 밀착 판정 자체(0 <= dist < 0.5ATR)
  2. **적용 범위** — 돌파·박스돌파 탭에만 접히고 눌림목엔 안 붙는다.
     8번 측정의 4셀이 돌파/박스돌파뿐이라 이 경계가 곧 근거 경계다
     (docs/display_only_bench_revalidation.md, 2026-09-13 사용자 확인).
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

IDX = Path(__file__).resolve().parents[0] / "static" / "index.html"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node 미설치")


def extract_function(name: str) -> str:
    """중괄호 깊이를 세어 함수 본문을 그대로 잘라낸다(정규식 금지 — 중첩
    중괄호를 안전하게 못 자름, test_price_basis_note.py와 같은 방식)."""
    src = IDX.read_text(encoding="utf-8")
    start = src.index(f"function {name}(")
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError(f"{name}: 닫는 중괄호를 못 찾음")


def run_js(snippet: str):
    src = extract_function("pivotHuggingDistAtr") + "\n" + snippet
    out = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# close/pivot/atr_pct -> 밀착이면 ATR배수, 아니면 null
CASES = [
    # atr_pct 2% of close 10050 = 201원. dist 50원 = 0.25ATR -> 밀착
    ({"close": 10050, "pivot": 10000, "atr_pct": 2}, True),
    # dist 0 (피벗 정확히 = close 아님, close>pivot 이어야 함) -> close==pivot은 제외
    ({"close": 10000, "pivot": 10000, "atr_pct": 2}, False),
    # dist 150원 / 201 = 0.746ATR -> 밀착 아님
    ({"close": 10150, "pivot": 10000, "atr_pct": 2}, False),
    # 경계 바로 아래 — ATR은 close 기준(close*atr_pct/100)이라 pivot이 아니라
    # close에서 역산해야 한다: dist = (close-pivot)/(close*0.02).
    # pivot = close*0.9902 -> dist = 0.49ATR
    ({"close": 10000, "pivot": 9902, "atr_pct": 2.0}, True),
    # close < pivot (아직 미돌파) -> 대상 아님
    ({"close": 9900, "pivot": 10000, "atr_pct": 2}, False),
    # 결측/이상값
    ({"close": None, "pivot": 10000, "atr_pct": 2}, False),
    ({"close": 10050, "pivot": None, "atr_pct": 2}, False),
    ({"close": 10050, "pivot": 10000, "atr_pct": 0}, False),
]


def test_hug_classification():
    payload = json.dumps([c[0] for c in CASES])
    got = run_js(
        f"const items = {payload};"
        "console.log(JSON.stringify(items.map(i =>"
        " pivotHuggingDistAtr(i.close, i.pivot, i.atr_pct) != null)));"
    )
    expected = [c[1] for c in CASES]
    assert got == expected, f"{got} != {expected}"


def test_boundary_is_exclusive_at_half_atr():
    """0.5ATR 정확히는 밀착 아님(구간 정의가 [0, 0.5)).

    ATR 가격폭은 **close** 기준(close*atr_pct/100)이다 — pivot 기준이 아니다.
    close=10000, atr_pct=2 -> ATR 200원. pivot=9900이면 dist가 정확히 0.5ATR.
    """
    got = run_js(
        "console.log(JSON.stringify({"
        " just_under: pivotHuggingDistAtr(10000, 9901, 2.0) != null,"
        " exactly_half: pivotHuggingDistAtr(10000, 9900, 2.0) != null,"
        " atr_is_close_based: pivotHuggingDistAtr(10000, 9900, 2.0)}));"
    )
    assert got["just_under"] is True, "0.495ATR은 밀착이어야 한다"
    assert got["exactly_half"] is False, "0.5ATR 정확히는 밀착 아님"
    assert got["atr_is_close_based"] is None


def test_collapse_scope_is_breakout_and_boxbreak_only():
    """renderCards의 적용 범위가 돌파·박스돌파로 한정돼 있는가.

    8번 측정 4셀(돌파 KR/US · 박스돌파 KR/US) 밖으로 새는 순간 근거 없는
    확장이 된다 — 눌림목이 들어가면 FAIL.
    """
    src = IDX.read_text(encoding="utf-8")
    anchor = src.index("const hugApplies")
    line = src[anchor:src.index("\n", anchor)]
    assert "'breakout'" in line and "'boxbreak'" in line, line
    for forbidden in ("'pullback'", "'imminent'", "'turnaround'", "'pattern'"):
        assert forbidden not in line, f"근거 범위 밖 탭이 포함됨: {forbidden} in {line}"


def test_default_is_collapsed_not_a_filter():
    """기본 접힘이어야 한다 — 펼침 Set에 들어있을 때만 목록에 남긴다.
    (필터 토글이면 기본이 '표시'가 되므로 조건 방향이 반대가 된다.)"""
    src = IDX.read_text(encoding="utf-8")
    i = src.index("const hugApplies")
    block = src[i:i + 600]
    assert "if (hugApplies && !pivotHugExpandedModes.has(mode))" in block, block
    # 펼침 상태를 서버/로컬에 저장하지 않는다(새로고침하면 다시 접힘)
    assert "localStorage" not in block


def test_hidden_items_are_still_reachable():
    """완전 은폐가 아니라 건수 표시 + 펼치기 버튼이 있어야 한다."""
    src = IDX.read_text(encoding="utf-8")
    assert "pivotHugToggle" in src
    assert "피벗 밀착(0~0.5ATR)" in src and "펼치기" in src
    # 전부 밀착이라 본목록이 비는 경우의 안내
    assert "전부 ⛔ 피벗 밀착(0~0.5ATR) 구간이라 접혀 있습니다" in src
