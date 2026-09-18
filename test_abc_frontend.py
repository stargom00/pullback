"""v5.267 — 🔺 ABC 탭 프론트 판정 로직.

CLAUDE.md의 "텍스트 추출 + Node 실행" 레시피 그대로다 — 재구현이 아니라
`static/index.html`의 production 함수를 **그대로 꺼내 실행**한다.

이 파일이 지키는 것:
  1. `abcFinCell` — **"판정 불가"를 "미달"로 보이게 하면 안 된다.**
     naver 모바일이 분기를 6개만 줘서 최근 4분기 YoY를 못 채우는 게 흔하고,
     서버도 그럴 땐 감점하지 않는다(test_abc_route.py). 화면이 그걸 "매출 1/4"
     처럼 보여주면 사용자가 **없는 근거로 종목을 버린다**.
  2. `abcFilteredHits` — 필터 칩이 실제로 거르는가(C1이 C1만).
  3. 탭 배선·경고 문구가 살아 있는가(관심 신호 · 진입 근거 없음).
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
INDEX_PATH = ROOT / "static" / "index.html"
TEXT = INDEX_PATH.read_text(encoding="utf-8")


def _extract_function(name: str) -> str:
    """중괄호 깊이를 세어 top-level 함수 하나를 텍스트 그대로 자른다
    (정규식은 중첩 중괄호에서 안전하지 않다)."""
    marker = f"function {name}("
    start = TEXT.find(marker)
    assert start != -1, f"`{marker}`를 못 찾음 — 이름이 바뀌었으면 이 테스트도 같이 갱신"
    brace = TEXT.index("{", start)
    depth = 0
    for i in range(brace, len(TEXT)):
        if TEXT[i] == "{":
            depth += 1
        elif TEXT[i] == "}":
            depth -= 1
            if depth == 0:
                return TEXT[start:i + 1]
    raise AssertionError(f"`{name}`의 닫는 중괄호를 못 찾음 — 파일이 잘렸나")


def _run(src: str, expr: str):
    if not shutil.which("node"):
        pytest.skip("node 미설치 — 도구 부재는 로직 결함과 다르다")
    p = subprocess.run(["node", "-e", f"{src}\nconsole.log(JSON.stringify({expr}))"],
                       capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip())


FIN_SRC = _extract_function("abcFinCell")
FILTER_SRC = _extract_function("abcFilteredHits")


def fin(**h):
    base = {"rev_yoy_pos": None, "rev_yoy_of": 0, "eps_pos_q": None, "fin_reason": None}
    base.update(h)
    return _run(FIN_SRC, f"abcFinCell({json.dumps(base, ensure_ascii=False)})")


# ── 1. 판정 불가 ≠ 미달 ────────────────────────────────────────────

def test_no_data_says_unknown_not_a_number():
    out = fin(fin_reason="실적 표 없음")
    assert ">판정 불가<" in out
    assert "매출" not in out and "0/" not in out, out


def test_short_history_shows_the_real_denominator():
    """YoY가 2분기만 가능하면 **2분기라고 써야** 한다 — '1/4'로 보이면 미달로 읽힌다."""
    out = fin(rev_yoy_pos=1, rev_yoy_of=2, eps_pos_q=2,
              fin_reason="매출 YoY 판정 불가(분기 6개 — YoY 가능 2분기)")
    assert "매출 1/2" in out, out
    assert "/4" not in out, "분모가 4로 고정돼 있다 — 미달로 오독된다"


def test_reason_is_reachable_as_a_tooltip():
    out = fin(rev_yoy_pos=1, rev_yoy_of=2, eps_pos_q=2, fin_reason="분기 6개")
    assert "분기 6개" in out, "이유가 화면 어디에도 없다"


def test_full_data_renders_plainly():
    out = fin(rev_yoy_pos=4, rev_yoy_of=4, eps_pos_q=2)
    assert "매출 4/4" in out and "EPS 2/2" in out
    assert "판정 불가" not in out


def test_eps_alone_is_not_swallowed():
    """매출만 판정 불가여도 EPS는 보여야 한다."""
    out = fin(eps_pos_q=1, fin_reason="매출 YoY 판정 불가")
    # 이유는 툴팁으로 남아도 되지만 **본문이** 판정 불가면 안 된다.
    assert "EPS 1/2" in out and ">판정 불가<" not in out, out


def test_zero_denominator_is_not_rendered_as_a_fraction():
    """rev_yoy_of=0에 rev_yoy_pos=0이면 '매출 0/0'이 떠선 안 된다."""
    out = fin(rev_yoy_pos=0, rev_yoy_of=0, eps_pos_q=2)
    assert "0/0" not in out, out


# ── 2. 필터 ────────────────────────────────────────────────────────

def _filtered(hits, grade="all", stage="all"):
    src = (f"let _abcData = {json.dumps({'hits': hits}, ensure_ascii=False)};\n"
           f"let abcGradeFilter = {json.dumps(grade, ensure_ascii=False)};\n"
           f"let abcStageFilter = {json.dumps(stage, ensure_ascii=False)};\n" + FILTER_SRC)
    return [h["ticker"] for h in _run(src, "abcFilteredHits()")]


HITS = [
    {"ticker": "A", "grade": "A급", "c_stage": "C1 벽앞"},
    {"ticker": "B", "grade": "B급", "c_stage": "C2 진돌이"},
    {"ticker": "C", "grade": "A급", "c_stage": "C0 대기"},
    {"ticker": "D", "grade": "C급", "c_stage": "C3 이탈"},
]


def test_filters_default_to_everything():
    assert _filtered(HITS) == ["A", "B", "C", "D"]


def test_stage_prefix_does_not_leak_across_stages():
    """C0과 C3이 'C'로 시작한다고 같이 걸리면 안 된다."""
    assert _filtered(HITS, stage="C0") == ["C"]
    assert _filtered(HITS, stage="C3") == ["D"]
    assert _filtered(HITS, stage="C2") == ["B"]


def test_grade_and_stage_combine():
    assert _filtered(HITS, grade="A급", stage="C1") == ["A"]
    assert _filtered(HITS, grade="B급", stage="C1") == []
    assert _filtered(HITS, grade="C급") == ["D"]


def test_grade_chips_and_colors_cover_exactly_the_three_tiers():
    """칩·색 테이블이 등급 집합과 어긋나면 **해당 등급이 화면에서 사라진다**
    (색 테이블에 없으면 회색, 칩에 없으면 걸러낼 방법이 없다)."""
    import ast
    i = TEXT.index("const _ABC_GRADE_COLOR")
    table = TEXT[i:TEXT.index("\n", i)]
    for g in ("A급", "B급", "C급"):
        assert f"'{g}'" in table, f"{g} 색이 없다: {table}"
        assert f"setAbcGrade('{g}')" in TEXT, f"{g} 필터 칩이 없다"
    assert "A급 근접" not in TEXT, "삭제된 라벨이 남아 있다"
    assert "단타만" not in TEXT and "trading_only" not in TEXT


def test_missing_stage_does_not_crash():
    assert _filtered([{"ticker": "X", "grade": "B급", "c_stage": None}], stage="C1") == []


# ── 3. 배선·경고 ───────────────────────────────────────────────────

def test_tab_is_wired():
    assert 'data-mode="abc"' in TEXT, "탭 버튼이 없다"
    assert "if (mode === 'abc') { return loadAbc(); }" in TEXT, "load() 분기가 없다"


def test_version_badge_matches_app_version():
    import sys
    sys.path.insert(0, str(ROOT))
    import app
    m = re.search(r'id="verBadge">(v[\d.]+)<', TEXT)
    assert m and m.group(1) == app.VERSION, (m and m.group(1), app.VERSION)


def test_warning_banner_is_present_and_unambiguous():
    """이 탭은 **관심 신호**다. 문구가 사라지면 진입 근거로 오해된다."""
    assert "관심 신호. 진입 근거 없음 — 측정 전." in TEXT
    assert "초기 임의값(2026-09-18)" in TEXT


def test_star_uses_ma200_price_not_a_recomputed_percent():
    src = _extract_function("abcWatch")
    assert "h.ma200" in src and "pivot: h.ma200" in src, src
    assert "ma200_pct" not in src, "화면에서 비율로 가격을 되돌리려 한다"
