"""v5.337 — "+직접 추가" 종목코드 칸 한글 IME 수정(ABC 검색칸 v5.336과 같은 방식).

사용자 지시: "ABC 검색칸과 같은 방식(조합 중 조회·값 덮어쓰기 금지, compositionend에서 한 번)."
원인: oninput마다 onMaTickerOrTabChange()가 한글이면 바로 /api/lookup을 불렀고, 결과(티커)가 _maApplyTicker로 **조합 중인
칸 값을 덮어써** IME 세션을 끊었다. 또 _maNameResolving 잠금이 진행 중 조회가 있으면 새 조회를 버려, "상"을 조회하는 사이
"상신"이 완성되면 "상신"은 조회되지 않았다. 수정: 조합 중엔 조회 안 함·compositionend에서 한 번, 조회 순번으로 마지막 조회만
반영, 결과가 올 때 칸 값이 바뀌었거나 다시 조합 중이면 덮어쓰지 않는다.

사보타주 확인(2026-10-07, FAIL 확인 후 원복):
① maOnTickerInput의 조합 가드 제거 → test_no_lookup_while_composing FAIL
② _maLookupCurrent가 항상 true(낡은 결과로 덮어쓰기) → test_stale_result_never_overwrites_composing_value ·
   test_out_of_order_results_only_latest_applied FAIL
브라우저(headless Chrome CDP Input.imeSetComposition 자모 조합 "세코닉스", 조회 응답은 스텁 — 로컬 KR 유니버스가 비어 있어서):
수정본은 칸 값이 자모 그대로 이어지고 조합이 끝난 뒤 053450.KQ로 바뀜. ①+② 사보타주본은 조합 중 덮어써 "999999.KQ세ㅋ"처럼
깨지고 엉뚱한 티커로 끝남(재현 확인). 주의: 같은 URL에 해시만 다른 Page.navigate는 새로고침이 아니라 옛 JS가 그대로 돈다 —
처음엔 그래서 사보타주가 "통과"했다(Page.reload ignoreCache로 다시 확인).
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
    if HTML[start - 6:start] == "async ":
        start -= 6
    i = HTML.index("{", HTML.index(")", start))
    d = 0
    for j in range(i, len(HTML)):
        d += {"{": 1, "}": -1}.get(HTML[j], 0)
        if d == 0:
            return HTML[start:j + 1]
    raise AssertionError(name)


def _line(prefix):
    return [l for l in HTML.splitlines() if l.startswith(prefix)][0]


# 가짜 DOM + 손으로 푸는 /api/lookup(응답 순서를 테스트가 정한다)
FAKE = """
const listeners = {};
const mk = (id, v) => ({ id, value: v, textContent: '', innerHTML: '',
  addEventListener(t, f) { (listeners[t] = listeners[t] || []).push(f); } });
const els = { maTicker: mk('maTicker', ''), maMarket: mk('maMarket', 'US'), maTab: mk('maTab', '재량'),
              maSnapHint: mk('maSnapHint', ''), maName: mk('maName', '') };
const document = { getElementById: id => els[id] || null };
const calls = [];
function apiJson(url) { return new Promise(res => calls.push({ q: decodeURIComponent(url.split('/').pop()), res })); }
const fire = (t, value, isComposing) => { els.maTicker.value = value;
  const e = { type: t, isComposing: !!isComposing, target: els.maTicker };
  if (t === 'input') maOnTickerInput(e); else (listeners[t] || []).forEach(f => f(e)); };
const answer = async (q, body) => { calls.find(c => c.q === q).res(body); await new Promise(r => setTimeout(r, 0)); };
"""
FNS = ("maOnTickerCompStart", "maOnTickerCompEnd", "maBindTicker", "maOnTickerInput", "_maLookupCurrent", "_maResolveName",
       "_maApplyTicker", "_maShowNameCandidates", "_maPickName", "_maHideNameDropdown", "_maInferMarket", "_isKrCodeBody",
       "onMaTickerOrTabChange")


def _js(body):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    src = (FAKE + "let _maSnapshotFound = false;\nlet _maSnapTimer = null;\n" + _line("let _maComposing = ") + "\n"
           + _line("let _maLookupSeq = ") + "\n" + _line("let _maNameCandidates = ") + "\n"
           + "\n".join(_fn(n) for n in FNS) + "\nmaBindTicker();\n"
           + f"(async () => {{ const out = await (async () => {{ {body} }})(); console.log(JSON.stringify(out)); }})();")
    p = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_no_lookup_while_composing():
    """'꿈비' 두벌식 — 조합 중 input은 조회하지 않고, 음절이 확정되는 compositionend에서만 한 번씩."""
    got = _js("""
      fire('compositionstart', ''); fire('input', 'ㄲ', true); fire('input', '꾸', true); fire('input', '꿈', true);
      const a = calls.map(c => c.q);
      fire('compositionend', '꿈');
      fire('compositionstart', '꿈'); fire('input', '꿈ㅂ', true); fire('input', '꿈비', true);
      const b = calls.map(c => c.q);
      fire('compositionend', '꿈비');
      return [a, b, calls.map(c => c.q)];""")
    assert got == [[], ["꿈"], ["꿈", "꿈비"]]


def test_composition_flag_guards_even_if_isComposing_missing():
    got = _js("fire('compositionstart', ''); fire('input', 'ㅅ', false); fire('input', '상', false);"
              " const a = calls.length; fire('compositionend', '상'); return [a, calls.map(c => c.q)];")
    assert got == [0, ["상"]]


def test_stale_result_never_overwrites_composing_value():
    """'꿈' 조회 결과가 '비'를 조합하는 중에 와도 칸 값을 덮어쓰지 않는다 — 조합이 끝난 '꿈비' 조회 결과만 반영."""
    got = _js("""
      fire('compositionstart', ''); fire('input', '꿈', true); fire('compositionend', '꿈');
      fire('compositionstart', '꿈'); fire('input', '꿈ㅂ', true);
      await answer('꿈', { ticker: '999999.KQ', name: '꿈엉뚱' });
      const during = [els.maTicker.value, els.maName.value, els.maMarket.value];
      fire('input', '꿈비', true); fire('compositionend', '꿈비');
      await answer('꿈비', { ticker: '407400.KQ', name: '꿈비' });
      return [during, els.maTicker.value, els.maName.value, els.maMarket.value];""")
    assert got == [["꿈ㅂ", "", "US"], "407400.KQ", "꿈비", "KR"]


def test_out_of_order_results_only_latest_applied():
    """예전 잠금은 진행 중이면 새 조회를 버렸다 — 이제 둘 다 보내고, 늦게 온 낡은 결과는 무시."""
    got = _js("""
      fire('compositionstart', ''); fire('input', '상', true); fire('compositionend', '상');
      fire('compositionstart', '상'); fire('input', '상신', true); fire('compositionend', '상신');
      const sent = calls.map(c => c.q);
      await answer('상신', { candidates: [{ ticker: '091580.KQ', name: '상신이디피' }, { ticker: '263810.KQ', name: '상신전자' }] });
      const hint = els.maSnapHint.innerHTML;
      await answer('상', { ticker: '111111.KS', name: '상엉뚱' });
      return [sent, hint.includes('상신이디피'), els.maTicker.value, els.maSnapHint.innerHTML === hint];""")
    assert got == [["상", "상신"], True, "상신", True]


def test_non_ime_input_still_immediate():
    got = _js("fire('input', '005930', false); return [calls.length, els.maMarket.value];")
    assert got == [0, "KR"]                                     # 코드 입력은 조회 없이 바로 시장 판정(기존 흐름)


def test_wiring():
    assert HTML.count('<input id="maTicker" oninput="maOnTickerInput(event)"') == 1
    code = "\n".join(l for l in HTML.splitlines() if not l.lstrip().startswith("//"))   # 주석의 경위 인용은 제외
    assert 'id="maTicker" oninput="onMaTickerOrTabChange()"' not in code and "_maNameResolving" not in code
    assert _fn("openManualAdd").count("maBindTicker();") == 1
    b = _fn("maBindTicker")
    assert b.count("addEventListener('compositionstart', maOnTickerCompStart)") == 1
    assert b.count("addEventListener('compositionend', maOnTickerCompEnd)") == 1
    for n in ("maOnTickerInput", "maOnTickerCompEnd", "_maResolveName", "_maLookupCurrent"):
        assert "setTimeout" not in _fn(n) and "debounce" not in _fn(n)
    assert _fn("_maResolveName").count("_maLookupCurrent(seq, query)") == 2   # 성공·실패 둘 다 낡은 결과 무시
