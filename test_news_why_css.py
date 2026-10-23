"""v5.356 — 카드 "🗞 왜?" CSS가 다른 규칙 안에 중첩되지 않았는지 고정.

배경: v5.346에서 `.news-why*` 규칙 6개가 `static/index.html`의 `.journal-btn{...}` 선언 블록
한가운데(`background:transparent;` 와 `border:` 사이)에 삽입됐다. CSS 파서는 선언 블록 안의
`{...}`를 무효 선언으로 버리므로 6개 규칙 전부와 `.journal-btn`의 `border` 선언까지 사라졌고,
요약 본문이 카드 기본 글자 크기로 나왔다(사용자 지적 2026-10-24).

검사 방식: 정규식이 아니라 첫 `<style>` 블록을 문자 단위로 훑으며 중괄호 깊이를 센다.
각 `.news-why*` 선택자가 **깊이 0**(최상위)에서 시작해야 한다. 주석 안의 텍스트는 제외한다.
사보타주: 블록을 다시 `.journal-btn{` 안으로 옮기면 깊이 1에서 발견돼 FAIL한다.
"""
from pathlib import Path

import pytest

SRC = Path(__file__).parent / "static" / "index.html"
SELECTORS = [
    ".news-why{",
    ".news-why-out{",
    ".news-why-out:empty{",
    ".news-why-lines div{",
    ".news-why-meta{",
    ".news-why-err{",
]


def _first_style_block(html: str) -> str:
    i = html.index("<style")
    i = html.index(">", i) + 1
    j = html.index("</style>", i)
    return html[i:j]


def _strip_comments(css: str) -> str:
    out = []
    i = 0
    while i < len(css):
        if css.startswith("/*", i):
            j = css.index("*/", i) + 2
            out.append(" " * (j - i))  # 길이 보존(위치 유지)
            i = j
        else:
            out.append(css[i])
            i += 1
    return "".join(out)


def _depth_at(css: str, pos: int) -> int:
    depth = 0
    for ch in css[:pos]:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
    return depth


@pytest.fixture(scope="module")
def css():
    return _strip_comments(_first_style_block(SRC.read_text(encoding="utf-8")))


@pytest.mark.parametrize("sel", SELECTORS)
def test_news_why_rules_are_top_level(css, sel):
    assert css.count(sel) == 1, f"{sel} 규칙이 정확히 1개여야 한다(현재 {css.count(sel)})"
    pos = css.index(sel)
    assert _depth_at(css, pos) == 0, f"{sel} 가 다른 규칙 안(depth {_depth_at(css, pos)})에 중첩돼 있다"


def test_journal_btn_block_has_no_nested_braces(css):
    start = css.index(".journal-btn{")
    end = css.index("}", start)
    body = css[start + len(".journal-btn{"):end]
    assert "{" not in body, ".journal-btn 선언 블록 안에 다른 규칙이 끼어 있다"
    assert "border:1px dashed" in body, ".journal-btn의 border 선언이 블록 안에 있어야 한다"


def test_news_why_body_font_matches_n_sub(css):
    out = css[css.index(".news-why-out{"):]
    out = out[: out.index("}")]
    assert "font-size:12px" in out, "요약 본문은 .n-sub와 같은 12px"
