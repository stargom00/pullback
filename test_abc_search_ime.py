"""v5.336 — ABC 검색칸 한글 IME 자모 분리 수정("상신" → "ㅅㅏㅇㅅㅣㄴ", v5.334에서 생김).

원인(확정): 입력 이벤트마다 abcSetQuery → renderAbcPage()가 content.innerHTML을 통째로 다시 써서 **입력칸 요소를 새로
만들고**(value 속성으로 값 재설정 + focus·커서 이동) 조합 중인 IME 세션을 끊었다. 브라우저(Chromium, CDP
Input.imeSetComposition로 자모 단위 조합) 재현: 수정 전 "상신" → "ㅅ사상상ㅅ시신신".
사용자 지시: "조합 중(compositionstart~compositionend, 또는 event.isComposing)에는 필터 재계산과 재렌더를 하지 않음.
compositionend에서 한 번 적용. 입력칸 요소는 다시 만들지 않고 목록 영역만 갱신. 증상을 가리는 지연(debounce)은 금지."
구현 중 실측: oncompositionstart/oncompositionend **인라인 속성은 브라우저가 무시한다** — addEventListener로만 붙는다
(인라인으로 달았을 때 조합 끝에도 필터가 안 걸렸다).

사보타주 확인(2026-10-07, FAIL 확인 후 원복):
① abcOnSearchInput의 조합 가드 제거 → test_composing_input_is_ignored_until_compositionend FAIL
   (브라우저 검증도 "조합 중 목록 갱신 3회"로 FAIL)
② abcSetQuery가 다시 renderAbcPage()를 부름 → test_set_query_refreshes_list_only FAIL
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
HTML = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()


def _fn(name):
    start = HTML.index(f"function {name}(")
    i = HTML.index("{", HTML.index(")", start))
    d = 0
    for j in range(i, len(HTML)):
        d += {"{": 1, "}": -1}.get(HTML[j], 0)
        if d == 0:
            return HTML[start:j + 1]


def _line(prefix):
    return [l for l in HTML.splitlines() if l.startswith(prefix)][0]


# 가짜 DOM — 입력칸 하나 + 목록·안내 영역. renderAbcPage가 불리면 기록(불리면 안 된다)
FAKE = """
const listeners = {};
const searchEl = { id: 'abcSearch', value: '', addEventListener(t, f) { (listeners[t] = listeners[t] || []).push(f); } };
const listEl = { innerHTML: '' }, noteEl = { innerHTML: '' };
const document = { getElementById: id => ({ abcSearch: searchEl, abcList: listEl, abcSearchNote: noteEl })[id] || null,
                   querySelectorAll: () => [] };
let renders = 0, lists = 0;
function renderAbcPage() { renders++; }
function abcFilteredHits() { return abcQuery ? [{ q: abcQuery }] : []; }
function abcListHtml(rows) { lists++; return 'LIST:' + abcQuery; }
let abcQuery = '';
const fire = (t, value, isComposing) => { searchEl.value = value;
  const e = { type: t, isComposing: !!isComposing, target: searchEl };
  if (t === 'input') abcOnSearchInput(e); else (listeners[t] || []).forEach(f => f(e)); };
"""


def _js(body):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    src = (FAKE + _line("const ABC_SEARCH_FADE = ") + "\n" + _line("let _abcComposing = ") + "\n"
           + "\n".join(_fn(n) for n in ("abcSearching", "abcSearchNoteHtml", "abcRefreshList", "abcOnSearchCompStart",
                                        "abcOnSearchCompEnd", "abcBindSearch", "abcOnSearchInput", "abcSetQuery"))
           + "\nabcBindSearch();\n" + f"console.log(JSON.stringify((() => {{ {body} }})()));")
    p = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_composing_input_is_ignored_until_compositionend():
    """'상신' 두벌식 조합 — 브라우저 실측 이벤트 순서(compositionstart → input(isComposing) … → compositionend)."""
    got = _js("""
      const snap = [];
      fire('compositionstart', ''); fire('input', 'ㅅ', true); fire('input', '사', true); fire('input', '상', true);
      snap.push([abcQuery, lists, renders]);
      fire('compositionend', '상');
      snap.push([abcQuery, lists, renders, listEl.innerHTML]);
      fire('compositionstart', '상'); fire('input', '상ㅅ', true); fire('input', '상시', true); fire('input', '상신', true);
      snap.push([abcQuery, lists]);
      fire('compositionend', '상신');
      snap.push([abcQuery, lists, renders, listEl.innerHTML]);
      return snap;""")
    assert got == [["", 0, 0], ["상", 1, 0, "LIST:상"], ["상", 1], ["상신", 2, 0, "LIST:상신"]]


def test_composition_flag_alone_guards_even_if_isComposing_missing():
    """Safari 등 isComposing이 빠진 input도 compositionstart~end 사이면 무시."""
    got = _js("fire('compositionstart', ''); fire('input', 'ㅍ', false); const a = [abcQuery, lists];"
              " fire('compositionend', '포'); return [a, abcQuery];")
    assert got == [["", 0], "포"]


def test_non_ime_input_applies_immediately():
    got = _js("fire('input', '0', false); fire('input', '09', false); fire('input', '', false); fire('input', '', false);"
              " return [abcQuery, lists, renders];")
    assert got == ["", 3, 0]                      # 같은 값 반복은 재계산 안 함, 렌더(입력칸 재생성) 0


def test_set_query_refreshes_list_only():
    got = _js("abcSetQuery('퓨처'); return [listEl.innerHTML, renders, searchEl.value];")
    assert got == ["LIST:퓨처", 0, ""]           # 입력칸 값은 건드리지 않는다
    s = _fn("abcSetQuery")
    assert "renderAbcPage" not in s and "focus" not in s and "setTimeout" not in s


def test_wiring_and_no_debounce():
    body = _fn("renderAbcPage")
    assert body.count('id="abcSearch"') == 1 and body.count('oninput="abcOnSearchInput(event)"') == 1
    assert "oncomposition" not in body                         # 인라인 composition 속성은 브라우저가 무시(주석 오탐 피해 렌더 본문만)
    assert body.count("abcBindSearch();") == 1 and body.count('<div id="abcList">${abcListHtml(rows)}</div>') == 1
    b = _fn("abcBindSearch")
    assert b.count("addEventListener('compositionstart', abcOnSearchCompStart)") == 1
    assert b.count("addEventListener('compositionend', abcOnSearchCompEnd)") == 1
    for n in ("abcOnSearchInput", "abcOnSearchCompEnd", "abcSetQuery", "abcRefreshList"):
        assert "setTimeout" not in _fn(n) and "debounce" not in _fn(n)
