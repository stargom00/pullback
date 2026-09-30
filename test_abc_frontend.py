"""v5.267 — 🔺 ABC 탭 프론트 판정 로직.

CLAUDE.md의 "텍스트 추출 + Node 실행" 레시피 그대로다 — 재구현이 아니라
`static/index.html`의 production 함수를 **그대로 꺼내 실행**한다.

이 파일이 지키는 것:
  1. `abcFinCell` — **"판정 불가"를 "미달"로 보이게 하면 안 된다.**
     naver 모바일이 분기를 6개만 줘서 최근 4분기 YoY를 못 채우는 게 흔하고,
     서버도 그럴 땐 감점하지 않는다(test_abc_route.py). 화면이 그걸 "매출 1/4"
     처럼 보여주면 사용자가 **없는 근거로 종목을 버린다**.
  2. `abcFilteredHits` — 필터 칩이 실제로 거르는가(단계 하나가 그 단계만).
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

def _filtered(hits, grade="all", stage="all", gate_break_only=False):
    """v5.276에서 필터가 `abcGateBreakOnly`를 읽기 시작해 하네스에 추가했다 —
    전역을 안 넘기면 ReferenceError로 **전 필터 테스트가 한꺼번에 깨진다**
    (실제로 그렇게 잡혔다). 의존이 늘면 여기도 같이 늘려야 한다."""
    src = (f"let _abcData = {json.dumps({'hits': hits}, ensure_ascii=False)};\n"
           f"let abcGradeFilter = {json.dumps(grade, ensure_ascii=False)};\n"
           f"let abcStageFilter = {json.dumps(stage, ensure_ascii=False)};\n"
           f"let abcGateBreakOnly = {json.dumps(gate_break_only)};\n" + FILTER_SRC)
    return [h["ticker"] for h in _run(src, "abcFilteredHits()")]


HITS = [
    {"ticker": "A", "grade": "A급", "c_stage": "벽앞"},
    {"ticker": "B", "grade": "B급", "c_stage": "🩷 강돌파"},
    {"ticker": "C", "grade": "A급", "c_stage": "대기"},
    {"ticker": "D", "grade": "C급", "c_stage": "이탈"},
]


def test_filters_default_to_everything():
    assert _filtered(HITS) == ["A", "B", "C", "D"]


def test_stage_filter_matches_exactly_one_stage():
    """v5.291: 라벨이 접두어 체계가 아니게 됐고 필터도 `startsWith` →
    **정확 일치**로 바뀌었다. 한 단계를 고르면 그 단계만 남아야 한다."""
    assert _filtered(HITS, stage="대기") == ["C"]
    assert _filtered(HITS, stage="이탈") == ["D"]
    assert _filtered(HITS, stage="🩷 강돌파") == ["B"]
    assert _filtered(HITS, stage="벽앞") == ["A"]


def test_grade_and_stage_combine():
    assert _filtered(HITS, grade="A급", stage="벽앞") == ["A"]
    assert _filtered(HITS, grade="B급", stage="벽앞") == []
    assert _filtered(HITS, grade="C급") == ["D"]


def test_grade_chips_and_colors_cover_exactly_the_three_tiers():
    """칩·색 테이블이 등급 집합과 어긋나면 **해당 등급이 화면에서 사라진다**
    (색 테이블에 없으면 회색, 칩에 없으면 걸러낼 방법이 없다)."""
    i = TEXT.index("const _ABC_GRADE_COLOR")
    # v5.292: 등급이 4개가 되며 테이블이 두 줄로 나뉘었다 — 줄 끝이 아니라
    # **선언 끝(`;`)까지** 잘라야 한다(한 줄 가정은 조용히 B급을 놓쳤다).
    table = TEXT[i:TEXT.index(";", i)]
    import abc_screener as _A
    for g in _A.GRADES:
        assert f"'{g}'" in table, f"{g} 색이 없다: {table}"
    # 필터 칩은 A/B/C 셋만 — `A급 보류`는 칩이 없어 "전체"에서만 보인다
    # (v5.292: 보류는 등급이 아니라 "아직 모른다"라 필터 축으로 안 뺐다).
    for g in ("A급", "B급", "C급"):
        assert f"setAbcGrade('{g}')" in TEXT, f"{g} 필터 칩이 없다"
    assert "A급 근접" not in TEXT, "삭제된 라벨이 남아 있다"
    assert "단타만" not in TEXT and "trading_only" not in TEXT


def test_gate_break_chip_was_absorbed_into_the_strong_stage():
    """v5.291(사용자 지시): 🩷600돌파 **토글이 제거**되고 `🩷 강돌파` 단계로
    흡수됐다. 같은 사건을 단계와 별도 토글 두 곳에서 거르면 어긋난다.
    v5.276의 "gate_break은 C단계와 독립"은 이제 의도적으로 성립하지 않는다."""
    code = "\n".join(ln for ln in TEXT.splitlines()
                      if not ln.strip().startswith("//"))
    assert "toggleAbcGateBreak" not in code, "토글이 되살아났다"
    assert "abcGateBreakOnly" not in code, "죽은 변수가 되살아났다"
    # 대신 단계 칩으로 같은 일을 한다
    assert "setAbcStage('🩷 강돌파')" in TEXT


def test_gate_break_column_is_rendered():
    src = _extract_function("abcGateBreakHtml")
    assert "gate_break" in src, src
    assert "ABC_MA_COLOR" in src, "강돌파 충족을 핫핑크로 구분하지 않는다"
    # v5.291(사용자 지시): 돌파봉 등락률·vol배수 + D+1/2/3 거래량을 같이 띄운다
    assert "day_pct" in src and "vol_mult" in src and "dplus" in src, src


def test_missing_stage_does_not_crash():
    assert _filtered([{"ticker": "X", "grade": "B급", "c_stage": None}], stage="벽앞") == []


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


def test_star_uses_the_gate_baseline_price():
    """트리거는 **MA600(게이트선) 가격**. v5.268 MA600 → v5.272 MA200 →
    v5.291 다시 MA600(사용자 지시 — 방법론이 MA600 돌파이므로 알림도 그 선).
    필드명은 v5.278부터 `my_trigger_price`다.
    """
    src = _extract_function("abcWatch")
    assert "my_trigger_price: h.ma_gate," in src, src
    # ⚠️ 주석 제거 후 검사한다 — 교체를 설명하는 주석에 `ma_stage`가 들어 있어
    # 원문 그대로 보면 오탐한다(CLAUDE.md 패턴 1, 이 세션에서 다섯 번째).
    code = "\n".join(ln for ln in src.splitlines()
                      if not ln.strip().startswith("//"))
    assert "ma_stage" not in code, "★가 MA200을 트리거로 쓴다"
    # 등록 값은 **가격만** 쓴다 — 비율(h.stage_pct)은 메모 문구에만 허용한다.
    # `_pct`로 통째 금지하면 레코드의 `risk_pct: ''` 필드에 걸려 오탐한다
    # (실제로 그랬다) — 읽는 대상(`h.`)으로 좁힌다.
    body = src[:src.index("note:")] if "note:" in src else src
    assert "h.stage_pct" not in body, "가격 대신 비율을 쓴다"
    assert "h.gate_pct" not in body


def test_both_baselines_are_shown_with_their_roles():
    """v5.272: 두 선이 **역할과 함께** 보여야 한다 — 어느 선 때문에 그 단계가
    됐는지 화면에서 바로 읽히지 않으면 사용자가 판정을 재구성할 수 없다."""
    i = TEXT.index("function abcRowHtml")
    row = TEXT[i:TEXT.index("\nfunction abcFilteredHits")]
    gate = row.index("h.gate_pct")
    stage = row.index("h.stage_pct")
    assert gate < stage, "MA600 열이 MA200 열보다 뒤에 있다"
    assert "ABC_MA_COLOR" in row[:gate], "MA600 열에 핫핑크 표시가 없다"
    # 헤더가 역할을 말하는가 — v5.291에서 역할이 뒤바뀌었다(MA600=단계기준)
    assert "(단계기준)" in TEXT and "(참고)" in TEXT, "열 이름에 역할 표시가 없다"
    assert "장기&gt;중기 역전" in TEXT, "역배열 라벨이 v5.272 문구가 아니다"


def test_baseline_label_comes_from_the_server_not_a_literal():
    """라벨을 'MA600'으로 박으면 기간을 바꿔도 화면만 옛 이름으로 남는다."""
    src = _extract_function("_abcMaLabel")
    assert "_abcData.ma_label" in src, src


def test_hot_pink_is_defined_once():
    assert TEXT.count("const ABC_MA_COLOR") == 1
    from test_helpers import resolve_colors
    i = TEXT.index("const ABC_MA_COLOR")
    # v5.304: 색은 토큰(var(--…))으로 옮겨졌다 — 풀어서 실제 hex가 나오는지 본다
    assert re.search(r"#[0-9a-f]{6}", resolve_colors(TEXT[i:i + 80], TEXT)), "색이 상수로 안 잡혀 있다"


def test_unavailable_baseline_count_is_surfaced():
    """봉 부족으로 빠진 종목 수가 화면에 없으면 '왜 안 보이지'가 미궁이 된다."""
    assert "_abcMaLabel() + ' 불가'" in TEXT, "카운트 노출이 없다"
