"""v5.305 — 시선 둘 곳(강조) 규칙.

원칙(사용자 지시): 강조색(앰버)은 "행동·현재 위치"에만, 의미색(빨강/노랑/초록)은 "상태"에만.
여기서는 그 배치가 토큰으로만 이뤄졌는지와, 새 강조가 글자 대비 4.5:1을 깨지 않는지를 본다.
실제 화면 대비는 Playwright 순회로 따로 쟀다(라이트 0건).

사보타주 확인(2026-09-30): 라이트 블록에서 --n-hit-tint 줄 삭제 →
test_hit_rows_use_hit_tint_token_in_both_themes FAIL(+ test_theme_tokens 키 집합 FAIL) — 원복.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_theme_tokens import DARK_SEL, LIGHT_SEL, TEXT, _cr, _fn, _vars


def _composite(v, under):
    m = re.fullmatch(r"rgba\((\d+),(\d+),(\d+),([\d.]+)\)", v.replace(" ", ""))
    if not m:
        return v
    r, g, b, a = int(m[1]), int(m[2]), int(m[3]), float(m[4])
    u = [int(under.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(c * a + uu * (1 - a)):02x}" for c, uu in zip((r, g, b), u))


@pytest.mark.parametrize("sel", [DARK_SEL, LIGHT_SEL])
def test_primary_button_white_text_contrast(sel):
    v = _vars(sel)
    for bg in ("--n-primary-bg", "--n-primary-bg-hover"):
        assert _cr(v["--n-primary-fg"], v[bg]) >= 4.5, (sel, bg)


def test_refresh_is_the_primary_button():
    i = TEXT.index(".hdr .refresh{height:44px")
    rule = TEXT[i:TEXT.index("}", i)]
    assert "background:var(--n-primary-bg)" in rule and "color:var(--n-primary-fg)" in rule
    # 주 버튼 토큰은 다시 스캔에만 — 다른 곳에 퍼지면 "유일한 주 버튼"이 깨진다
    assert TEXT.count("var(--n-primary-bg)") == 2   # background + border-color, 같은 규칙


def test_active_tab_is_underline_not_box():
    i = TEXT.index(".hdr-main .tab.active,.hdr-util .tab.active,.hdr-more.has-active{")
    rule = TEXT[i:TEXT.index("}", i)]
    assert "background:none" in rule and "inset 0 -3px 0 var(--n-accent)" in rule and "font-weight:700" in rule


@pytest.mark.parametrize("sel", [DARK_SEL, LIGHT_SEL])
def test_hit_rows_use_hit_tint_token_in_both_themes(sel):
    v = _vars(sel)
    assert "--n-hit-tint" in v, "도달 행 틴트 토큰이 없다"
    card = v["--n-card"]
    tint = _composite(v["--n-hit-tint"], card)
    # 라이트만 4.5 강제 — 다크 글자색은 v5.304 이전 값 그대로라 원래 카드 대비가 기준
    if sel == LIGHT_SEL:
        for fg in ("--n-text", "--n-muted", "--n-accent-fg", "--c-red-fg3", "--c-blue-fg4", "--px-up", "--px-down"):
            assert _cr(v[fg], tint) >= 4.5, (fg, tint, round(_cr(v[fg], tint), 2))
    assert TEXT.count("background:var(--n-hit-tint)") == 2   # 내 추적 행 + 저점 보유 행


def test_lowpoint_hit_tint_beats_jr_table_even_row_reset():
    """jr-table은 짝수 행 td를 background:none으로 되돌린다(선택자 table.jr-table tbody
    tr:nth-child(even) td = 클래스 2·태그 4). 도달 틴트가 그보다 약하면 짝수 행에서 사라진다."""
    i = TEXT.index("tr.lpt-hit td{background:var(--n-hit-tint)}")
    sel = TEXT[TEXT.rfind("\n", 0, i) + 1:i + len("tr.lpt-hit td")]
    classes = sel.count(".") + sel.count(":")
    tags = len(re.findall(r"(?:^|[\s>])(?:table|tbody|tr|td)", sel))
    assert (classes, tags) > (2, 4) or classes > 2, sel


def test_hit_classes_are_wired_to_reached_state():
    assert "track-row${st.state === 'reached' ? ' hit' : ''}" in TEXT
    assert "<tr class=\"${hit ? 'lpt-hit' : ''}\">" in TEXT


@pytest.mark.parametrize("sel", [DARK_SEL, LIGHT_SEL])
def test_rs_chip_and_sector_chip_contrast(sel):
    v = _vars(sel)
    assert _cr(v["--n-rs-hi-fg"], v["--n-rs-hi-bg"]) >= 4.5
    if sel == LIGHT_SEL:
        assert _cr(v["--px-up"], _composite(v["--n-good-tint"], v["--n-card"])) >= 4.5


def test_tiles_get_state_stripe_class():
    assert TEXT.count('class="tile lv-${') == 2   # 지수 타일 3개(같은 템플릿) + 시장 게이트 타일
    for lv in ("good", "neutral", "bad", "unknown"):
        assert f".tile.lv-{lv}{{box-shadow:inset 4px 0 0 var(--" in TEXT


@pytest.mark.parametrize("rs,want", [(95, " rs-hi"), (99, " rs-hi"), (94, ""), (94.9, ""), ("null", ""), ("'95'", "")])
def test_rs_hi_class(rs, want):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    js = f"{_fn('rsHiClass')}\nconsole.log(JSON.stringify(rsHiClass({rs})));"
    p = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout) == want


def test_collapsed_card_uses_rs_hi_class():
    fn = _fn("collapsedRowHtml")
    assert 'class="cc-rs mono${rsHiClass(s.rs)}"' in fn


def test_no_emoji_added_in_v5305_rules():
    """이모지 부활 금지 — v5.305 CSS 주석·규칙 줄에 이모지가 없어야 한다."""
    lines = [l for l in TEXT.split("\n") if "v5.305" in l]
    assert lines
    emoji = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
    assert not [l for l in lines if emoji.search(l)]
