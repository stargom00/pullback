"""일지 "직접 추가" 탭 드롭다운(`#maTab`) 옵션 검사 (v5.289, 사용자 지시).

지키는 것 3가지:
  1. `저점` 옵션이 `#maTab` select **안에** 있다 — 파일 어딘가가 아니라
     그 select 블록 안(다른 select나 changelog 주석에 들어가도 통과하면
     안 된다, CLAUDE.md "in 존재 검사" 패턴).
  2. 기존 6개 값과 기본 선택(`재량` selected)이 **불변**이다. 값 목록을
     순서까지 고정해 하나라도 지워지거나 값이 바뀌면 실패 — `in` 검사는
     "하나 지워도 통과"하므로 쓰지 않는다.
  3. `tab` 값이 미등록일 때 통과하는 소비처들이 전부 `.get()`(None 반환)
     이라 KeyError가 안 난다 — "저점"을 실제로 넣어 호출해 확인한다.
     `CONFIRM_RULE_BY_TAB`은 `_refresh_auto_watch()` 지역 변수라 import가
     안 되므로, 같은 dict 리터럴을 텍스트로 뽑아 "저점이 키에 없고 호출부가
     `.get(`으로 읽는다"를 대조한다(사본 재구현 아님 — 원문을 그대로 읽는다).
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "app.py").read_text(encoding="utf-8")
# 실행 코드 영역만 — 모듈 docstring의 `[변경 이력]`은 변경 내용을 그대로
# 인용하므로 거기까지 세면 오탐한다(CLAUDE.md "주석·changelog 인용문에 걸려
# 오탐" 패턴 — 이 파일을 처음 쓸 때 실제로 v5.289 이력에 걸려 2로 셌다).
# `VERSION = "..."` 이후가 코드 영역이다(두 changelog 블록 모두 그 앞에 있다).
APP_CODE = APP[APP.index('VERSION = "v5.'):]

EXPECTED_TAB_VALUES = ["눌림목", "돌파", "돌파임박", "박스돌파", "추세전환", "저점", "재량"]


def _ma_tab_block() -> str:
    """`<select id="maTab" ...>` ~ 대응 `</select>`까지. 정규식으로 파일
    전체를 훑지 않고 select 블록만 잘라낸다 — changelog 주석이나 다른
    select에 같은 문자열이 있어도 오탐/오통과하지 않게."""
    start = HTML.index('<select id="maTab"')
    end = HTML.index("</select>", start)
    return HTML[start:end]


def test_jeojeom_option_exists_inside_ma_tab_select():
    block = _ma_tab_block()
    assert block.count('<option value="저점">') == 1, (
        "#maTab select 안에 value=\"저점\" option이 정확히 1개 있어야 함 — "
        f"실제 {block.count('<option value=\"저점\">')}개"
    )


def test_existing_values_and_default_unchanged():
    block = _ma_tab_block()
    values = re.findall(r'<option value="([^"]+)"', block)
    assert values == EXPECTED_TAB_VALUES, (
        f"#maTab 옵션 값이 바뀜: {values} != {EXPECTED_TAB_VALUES} — "
        "기존 값은 일지 레코드의 tab 필드로 저장돼 있어 바꾸면 과거 기록이 끊긴다."
    )
    # 기본 선택은 '재량' 하나뿐이어야 한다(새 옵션에 selected가 붙으면 실패).
    selected = re.findall(r'<option value="([^"]+)"[^>]*\bselected\b', block)
    assert selected == ["재량"], f"기본 선택이 바뀜: {selected} != ['재량']"


def test_unregistered_tab_is_none_safe_in_consumers():
    """미등록 탭을 넣어도 KeyError가 안 나고 None으로 떨어지는지 — 실제 객체로 확인."""
    import app

    assert app.PAPER_TRACK_BACKTEST_EV.get(("저점", "KR")) is None
    assert app.PAPER_TRACK_BACKTEST_EV.get(("저점", "US")) is None
    # 등록된 탭은 여전히 값이 나온다(사보타주 방지 — dict를 비워도 위 두 줄은 통과한다).
    assert app.PAPER_TRACK_BACKTEST_EV.get(("눌림목", "KR")) == 0.171
    # 스냅샷 조회: 미등록 조합은 None (get_signal_snapshot은 dict.get 기반)
    assert app.get_signal_snapshot("005930.KS", "저점") is None


def test_confirm_rule_lookup_uses_get_and_has_no_jeojeom_key():
    """`_refresh_auto_watch()`의 확인규칙 조회가 `.get()`이고 '저점' 키가 없음 —
    없으면 rule=None으로 '확인규칙 미검증' 경로(재량과 동일)를 탄다."""
    start = APP_CODE.index("CONFIRM_RULE_BY_TAB = {")
    end = APP_CODE.index("}", APP_CODE.index('"추세전환":', start))
    literal = APP_CODE[start:end]
    keys = re.findall(r'^\s*"([^"]+)":', literal, re.M)
    assert keys == ["돌파임박", "눌림목", "박스돌파", "돌파", "추세전환"], keys
    assert "저점" not in keys
    # 호출부가 대괄호 색인이 아니라 .get()이어야 KeyError가 안 난다.
    assert APP_CODE.count("CONFIRM_RULE_BY_TAB.get(tab)") == 1
    assert "CONFIRM_RULE_BY_TAB[tab]" not in APP_CODE


def _select_values(select_id: str) -> list:
    start = HTML.index(f'<select id="{select_id}"')
    block = HTML[start:HTML.index("</select>", start)]
    return re.findall(r'<option value="([^"]+)"', block)


def test_tab_field_has_no_other_select():
    """`tab` 필드를 쓰는 select는 `#maTab` 하나뿐 — 편집 폼 select는 category(`#e_cat`)다.
    두 번째 tab select가 생기면 옵션을 한쪽만 추가하는 사고가 나므로 여기서 잡는다."""
    assert HTML.count('<select id="maTab"') == 1
    assert HTML.count('<select id="e_cat"') == 1


def test_two_selects_do_not_leak_each_others_exclusive_values():
    """`#maTab`(tab)과 `#e_cat`(category)은 **서로의 전용 값을 갖지 않는다.**
    `저점`은 v5.289부터 둘 다에 정당하게 존재한다(tab 값이면서 category 값) —
    그래서 "한쪽에 없어야 한다"가 아니라 "전용 값이 새지 않는다"로 검사한다."""
    tabs = _select_values("maTab")
    cats = _select_values("e_cat")
    tab_only = {"눌림목", "돌파", "돌파임박", "박스돌파", "추세전환", "재량"}
    cat_only = {"추세추종", "단타", "대기", "관찰"}
    assert tab_only <= set(tabs), f"#maTab에서 탭 전용 값이 사라짐: {tab_only - set(tabs)}"
    assert cat_only <= set(cats), f"#e_cat에서 카테고리 전용 값이 사라짐: {cat_only - set(cats)}"
    leaked_into_cat = (set(tabs) & cat_only)
    leaked_into_tab = (set(cats) & (tab_only - {"재량"}))   # 재량은 원래 양쪽 공용
    assert not leaked_into_cat, f"#maTab에 카테고리 전용 값이 섞임: {leaked_into_cat}"
    assert not leaked_into_tab, f"#e_cat에 탭 전용 값이 섞임: {leaked_into_tab}"
    # 공용 값(양쪽에 다 있어야 하는 것)은 저점·재량 둘뿐이다.
    assert set(tabs) & set(cats) == {"저점", "재량"}, set(tabs) & set(cats)
