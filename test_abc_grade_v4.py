"""v5.292 (사용자 지시, 안4′) — A급 조건에서 `b.ok` 제거 + `A급 보류` 가드.

지키는 것:
  1. 🩷강돌파 & 기업축 통과 → A급
  2. 🩷강돌파 & 실적 미조회 → **A급 보류**(A급도 B급도 아니다)
  3. `b.ok` 값이 등급에 **아무 영향이 없다**
  4. 🩷강돌파는 `_ABC_EARNINGS_MAX` 상한과 무관하게 **전량 조회**
  5. B급 = "기업축 통과 + 강돌파 아님"
"""
import asyncio
import inspect
import json
import re
import time
from pathlib import Path

import pytest

import abc_screener as A
import app
from test_helpers import code_only

ROOT = Path(__file__).resolve().parent
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


def _res(stage, b_ok=True):
    return {"verdict": "ABC", "c_stage": stage, "b": {"ok": b_ok}}


def _comp(rev=4, eps=2, of=4, turn=300.0, holder=False):
    """production `company_axis()`를 그대로 쓴다 — comp dict를 손으로 만들면
    `fin_unknown` 같은 새 필드를 빼먹어도 테스트가 통과한다(CLAUDE.md
    "입력을 직접 만들어 넣은 재현" 패턴)."""
    return A.company_axis(turn, rev, eps, holder, rev_yoy_of=of)


# ══ 1) A급 = 강돌파 & 기업축 ═════════════════════════════════════════
def test_strong_with_company_axis_is_a_grade():
    assert A.grade(_res(A.STAGE_STRONG), _comp()) == A.GRADE_A


def test_strong_with_failing_company_axis_is_c_grade():
    """실적 미달은 보류가 아니라 C급 — "봤고 미달"이다."""
    c = _comp(rev=1, eps=0)
    assert c["ok"] is False and c["fin_unknown"] is False, c
    assert A.grade(_res(A.STAGE_STRONG), c) == A.GRADE_C


@pytest.mark.parametrize("stage", [A.STAGE_WALL, A.STAGE_WEAK, A.STAGE_WAIT])
def test_non_strong_stages_cannot_be_a_grade(stage):
    assert A.grade(_res(stage), _comp()) == A.GRADE_B


def test_exit_is_c_grade():
    assert A.grade(_res(A.STAGE_EXIT), _comp()) == A.GRADE_C


def test_turnover_bounds_still_apply():
    assert A.grade(_res(A.STAGE_STRONG), _comp(turn=6.0)) == A.GRADE_C      # 하한 미달
    assert A.grade(_res(A.STAGE_STRONG), _comp(turn=1488.0)) == A.GRADE_B   # 상한 초과(천장)


# ══ 2) A급 보류 가드 ═════════════════════════════════════════════════
def test_unfetched_earnings_yields_a_pending_not_a_grade():
    c = _comp(rev=None, eps=None, of=0)
    # company_axis가 결측이면 검사를 건너뛰어 ok=True가 된다 — 그게 가드의 이유다
    assert c["ok"] is True and c["fin_unknown"] is True, c
    assert A.grade(_res(A.STAGE_STRONG), c) == A.GRADE_A_PENDING


def test_pending_only_applies_to_would_be_a_grade():
    """보류는 A급 자리에만 생긴다 — 벽앞·이탈이 보류로 바뀌면 안 된다."""
    c = _comp(rev=None, eps=None, of=0)
    assert A.grade(_res(A.STAGE_WALL), c) == A.GRADE_B
    assert A.grade(_res(A.STAGE_EXIT), c) == A.GRADE_C
    assert A.grade(_res(A.STAGE_STRONG), dict(c, turnover_large=True)) == A.GRADE_B


def test_one_sided_earnings_is_not_unknown():
    """한쪽만 있어도 판정 근거는 있다 — 보류가 아니다."""
    assert A.company_axis(300.0, None, 2, False, rev_yoy_of=0)["fin_unknown"] is False
    assert A.company_axis(300.0, 4, None, False, rev_yoy_of=4)["fin_unknown"] is False


def test_grade_labels_are_constants_and_enumerated():
    assert A.GRADES == (A.GRADE_A, A.GRADE_A_PENDING, A.GRADE_B, A.GRADE_C)
    body = code_only(inspect.getsource(A.grade), comment_markers=("//", "#"))
    body = body[body.index('"""', body.index('"""') + 3) + 3:]      # docstring 제외
    for label in A.GRADES:
        assert f'"{label}"' not in body and f"'{label}'" not in body, (
            f"grade() 본문에 등급 리터럴 {label!r} — GRADE_* 상수를 쓸 것")


def test_grade_never_returns_an_unknown_label():
    allowed = set(A.GRADES) | {None}
    for stage in A.C_STAGES + (None,):
        for rev, eps, of in ((4, 2, 4), (1, 0, 4), (None, None, 0)):
            for turn in (6.0, 300.0, 1488.0):
                g = A.grade(_res(stage), _comp(rev, eps, of, turn))
                assert g in allowed, (stage, rev, eps, turn, g)


# ══ 3) b.ok는 등급에 영향이 없다 ═════════════════════════════════════
def test_b_ok_has_no_effect_on_grade():
    """모든 조합에서 b.ok True/False 결과가 같아야 한다 — 하나라도 갈리면
    `b.ok`가 등급에 남아 있다는 뜻이다."""
    diffs = []
    for stage in A.C_STAGES + (None,):
        for rev, eps, of in ((4, 2, 4), (1, 0, 4), (None, None, 0)):
            for turn in (6.0, 300.0, 1488.0):
                c = _comp(rev, eps, of, turn)
                g_t = A.grade(_res(stage, True), c)
                g_f = A.grade(_res(stage, False), c)
                if g_t != g_f:
                    diffs.append((stage, rev, eps, turn, g_t, g_f))
    assert not diffs, f"b.ok가 등급을 바꾼다: {diffs[:5]}"


def test_grade_source_does_not_read_b_ok():
    body = code_only(inspect.getsource(A.grade), comment_markers=("//", "#"))
    body = body[body.index('"""', body.index('"""') + 3) + 3:]
    assert '"b"' not in body and "chart_ok" not in body, (
        f"grade()가 아직 b.ok를 읽는다:\n{body}")


def test_b_ok_is_still_computed_and_shown():
    """등급에서 뺐을 뿐 **계산·표시는 유지**한다(사용자 지시: 참고 칸)."""
    src = inspect.getsource(A.analyze_abc)
    assert 'out["b"] = best' in src, "B 판정 자체가 사라졌다"
    assert "h.b_bars" in HTML, "화면에서 B 참고 칸이 사라졌다"


# ══ 4) 강돌파는 상한 무관 전량 조회 ══════════════════════════════════
def _cands(n_strong, n_other):
    out = [(f"S{i:04d}.KQ", {"c_stage": A.STAGE_STRONG}) for i in range(n_strong)]
    out += [(f"O{i:04d}.KQ", {"c_stage": A.STAGE_WEAK}) for i in range(n_other)]
    return out


async def _run_fill(cands):
    """`api_abc()`의 실적 채우기 블록 **원문**을 잘라 실행(사본 금지)."""
    import textwrap
    src = inspect.getsource(app.api_abc)
    begin = src.index("    loop = asyncio.get_event_loop()")
    end = src.index("    _FLOW_STAGES = {")
    block = textwrap.dedent(src[begin:end])
    ns = {"asyncio": asyncio, "time": time, "cands": cands,
          "abc_screener": A,
          "_ABC_STAGE_PRIORITY": app._ABC_STAGE_PRIORITY,
          "_abc_earnings_peek": app._abc_earnings_peek,
          "_ABC_EARNINGS_CACHE": app._ABC_EARNINGS_CACHE,
          "_ABC_EARNINGS_MAX": app._ABC_EARNINGS_MAX,
          "_earnings_executor": app._earnings_executor,
          "_abc_quarterly_axes": app._abc_quarterly_axes,
          "_abc_earnings_source": app._abc_earnings_source,
          "print": lambda *a, **k: None}
    wrapper = "async def _f():\n" + textwrap.indent(block, "    ") + "\n    return fin\n"
    exec(compile(wrapper, "<abc-earnings-block>", "exec"), ns)
    return await ns["_f"]()


def test_every_strong_ticker_is_fetched_even_past_the_cap(monkeypatch):
    """강돌파 45종목 + 나머지 200종목, 상한 40 → 강돌파는 45 전건."""
    asked = []
    monkeypatch.setattr(app, "_abc_quarterly_axes",
                        lambda t: (asked.append(t),
                                   {"rev_yoy_pos": 4, "rev_yoy_of": 4,
                                    "eps_pos_q": 2, "reason": None})[1])
    monkeypatch.setattr(app, "_ABC_EARNINGS_CACHE", {})
    monkeypatch.setattr(app, "_ABC_EARNINGS_MAX", 40)
    cands = _cands(45, 200)
    fin = asyncio.run(_run_fill(cands))
    strong = [t for t in asked if t.startswith("S")]
    other = [t for t in asked if t.startswith("O")]
    assert len(strong) == 45, f"강돌파가 상한에 잘렸다: {len(strong)}/45"
    assert len(other) == 40, f"비강돌파 상한이 안 걸렸다: {len(other)}"
    assert len(fin) == 85
    # capped는 **비강돌파 몫만** 센다
    assert app._abc_earnings_source["capped"] == 200 - 40


def test_cap_still_applies_to_non_strong(monkeypatch):
    monkeypatch.setattr(app, "_abc_quarterly_axes",
                        lambda t: {"rev_yoy_pos": 4, "rev_yoy_of": 4,
                                   "eps_pos_q": 2, "reason": None})
    monkeypatch.setattr(app, "_ABC_EARNINGS_CACHE", {})
    monkeypatch.setattr(app, "_ABC_EARNINGS_MAX", 10)
    asyncio.run(_run_fill(_cands(0, 50)))
    assert app._abc_earnings_source["capped"] == 40


# ══ 5) 화면 — 보류 색·툴팁 ═══════════════════════════════════════════
def test_pending_grade_has_its_own_colour():
    i = HTML.index("const _ABC_GRADE_COLOR")
    table = HTML[i:HTML.index(";", i)]
    for g in A.GRADES:
        assert f"'{g}'" in table, f"{g} 색이 없다 — 회색으로 떨어져 C급과 헷갈린다: {table}"
    assert "'A급 보류': '#7FB88A'" in table


def test_pending_tooltip_explains_why_and_keeps_the_distinction():
    fn = HTML[HTML.index("function gradeTitle("):HTML.index("function abcRowHtml(")]
    assert "A급 보류" in fn and "h.fin_reason" in fn, fn
    # "미조회"와 "데이터 없음"을 등급에서 뭉개더라도 툴팁은 사유를 그대로 보여준다
    assert "fin_reason" in fn and "미조회" in fn, fn
    assert "gradeTitle(h, fails)" in HTML, "행 렌더가 이 툴팁을 안 쓴다"
