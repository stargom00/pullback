"""R 게이트 — "+직접 추가"(saveManualAdd) 경고줄 오표시 수정 검증
(v5.244, 사용자 지시).

배경: "+일지"와 "+직접 추가" 두 모달이 실시간 입력줄(renderCalcLine)을
공유하는데, 게이트가 막힌 상태에서 둘 다 "→ 저장은 관찰(대기)로만 가능"
문구를 보여줬다. 그런데 실제로 저장을 막는 건 saveJournal()(즉시진입,
"+일지")와 markEntered()(대기→진입) 두 곳뿐이고 saveManualAdd()
("+직접 추가")는 entryGate()를 아예 호출하지 않는다(v4.52.3에서 "이미
매수한 종목을 기록 못 함" 문제를 풀기 위해 의도적으로 게이트를 안 붙인
설계 — 이번에도 건드리지 않음). 그 결과 "+직접 추가"에서는 화면이
사실과 다른 말("저장 안 된다")을 해서 사용자가 실거래 기록을 시도조차
안 하게 됐다.

수정: `effectiveCapR(ignoreGate)`/`entryGate(newR, {ignoreMarketGate})`에
옵션 추가 — ignoreMarketGate:true면 시장게이트(조정/압박) 사유와 그로
인한 cap 축소를 건너뛰고, 월간/주간 서킷브레이커·오픈리스크 상한(사용자
자신의 포트폴리오 객관적 상태, 시장 해석과 무관)은 그대로 평가한다.
cap도 같이 무시해야 하는 이유: 안 그러면 압박(cap 1.5R)/조정(cap 0R)으로
줄어든 cap 때문에 "오픈 리스크 상한 초과"라는 이름으로 시장게이트가
다시 나타난다 — 이번에 발견한 함정, 아래 테스트가 이 결합을 직접 검증.

레시피: 텍스트 추출 + Node 실행(CLAUDE.md, test_price_basis_note.py
선례). entryGate()/effectiveCapR() 자체는 그대로 추출해 실행하고,
openRiskR()/periodR()/consecLosses()/getJournal()은 이번 수정과 무관한
기존 로직이라 테스트 쪽에서 직접 제어 가능한 스텁으로 대체한다(그
함수들 자체의 정확성은 이 테스트의 대상이 아님) — document도 최소
기능만 흉내낸 가짜 엘리먼트로 대체."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
INDEX_PATH = ROOT / "static" / "index.html"


def _extract_function(name: str) -> str:
    """static/index.html에서 top-level `function {name}(...) {...}` 선언
    하나를 텍스트 그대로 추출. 괄호/중괄호 깊이를 세어 정확한 끝을 찾는다.

    파라미터 목록의 매칭되는 ')'부터 찾은 뒤 그 뒤에서 함수 본문의 '{'를
    찾는다 — entryGate(newR = 1, { ignoreMarketGate = false } = {})처럼
    구조분해 기본값 파라미터에 '{'가 먼저 나오면, 그걸 함수 본문 시작으로
    착각해 잘못 잘라내는 버그가 있었다(단순히 첫 '{'를 찾으면 걸림)."""
    text = INDEX_PATH.read_text(encoding="utf-8")
    marker = f"function {name}("
    start = text.find(marker)
    assert start != -1, (
        f"static/index.html에서 `{marker}`를 못 찾음 — 이름이 바뀌었거나 "
        "삭제됐는지 확인 (이 테스트 자체를 같이 갱신할 것)"
    )
    paren_start = text.index("(", start)
    depth = 0
    paren_end = None
    for i in range(paren_start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                paren_end = i
                break
    assert paren_end is not None, f"`{name}` 파라미터 목록의 닫는 괄호를 못 찾음"
    brace_start = text.index("{", paren_end)
    depth = 0
    for i in range(brace_start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise AssertionError(f"`{name}` 함수의 닫는 중괄호를 못 찾음 — 파일이 잘렸을 가능성")


EFFECTIVE_CAP_R_SRC = _extract_function("effectiveCapR")
ENTRY_GATE_SRC = _extract_function("entryGate")
CALC_SHARES_SRC = _extract_function("calcShares")
ONE_R_KRW_SRC = _extract_function("oneRKrw")
RENDER_CALC_LINE_SRC = _extract_function("renderCalcLine")

DEFAULT_R_SETTINGS = {
    "equity": 100_000_000, "r_pct": 0.5, "max_open_r": 3,
    "weekly_stop_r": 3, "monthly_stop_r": 6, "gate": "confirmed",
    "usd_krw": 1400,
}


def _run_node(script: str):
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — 실행 테스트 스킵")
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=15)
    if res.returncode != 0:
        raise AssertionError(f"node 실행 실패:\n{res.stderr}")
    return res.stdout


def _entry_gate(new_r, gate_opts, r_settings=None, open_r=0, week=0, month=0):
    """entryGate()/effectiveCapR()를 실제 소스 그대로 실행. openRiskR/
    periodR는 테스트가 직접 제어하는 스텁으로 대체."""
    rs = {**DEFAULT_R_SETTINGS, **(r_settings or {})}
    script = f"""
let rSettings = {json.dumps(rs)};
function openRiskR() {{ return {open_r}; }}
function periodR() {{ return {{ week: {week}, month: {month} }}; }}
{EFFECTIVE_CAP_R_SRC}
{ENTRY_GATE_SRC}
console.log(JSON.stringify(entryGate({new_r}, {json.dumps(gate_opts)})));
"""
    return json.loads(_run_node(script))


def _render_calc_line(el_id, entry, stop, market, gate_opts, r_settings=None, open_r=0, week=0, month=0):
    """renderCalcLine()을 실제 소스 그대로 실행. document/consecLosses/
    pendingStock은 최소 기능 스텁."""
    rs = {**DEFAULT_R_SETTINGS, **(r_settings or {})}
    script = f"""
let rSettings = {json.dumps(rs)};
function openRiskR() {{ return {open_r}; }}
function periodR() {{ return {{ week: {week}, month: {month} }}; }}
function consecLosses() {{ return 0; }}
let pendingStock = null;
class FakeClassList {{
  constructor() {{ this._s = new Set(); }}
  add(c) {{ this._s.add(c); }}
  remove(...cs) {{ cs.forEach(c => this._s.delete(c)); }}
  contains(c) {{ return this._s.has(c); }}
}}
const _el = {{ classList: new FakeClassList(), innerHTML: '' }};
const document = {{ getElementById: () => _el }};
{EFFECTIVE_CAP_R_SRC}
{ENTRY_GATE_SRC}
{ONE_R_KRW_SRC}
{CALC_SHARES_SRC}
{RENDER_CALC_LINE_SRC}
renderCalcLine({json.dumps(el_id)}, {entry}, {stop}, {json.dumps(market)}, {json.dumps(gate_opts)});
console.log(JSON.stringify({{ html: _el.innerHTML, locked: _el.classList.contains('lock') }}));
"""
    return json.loads(_run_node(script))


# ---------------------------------------------------------------------------
# entryGate()/effectiveCapR() 핵심 조합 — 사용자 지시 "이 네 조합이 이번
# 수정의 실질이다".
# ---------------------------------------------------------------------------

def test_ignore_market_gate_bypasses_correction():
    r = _entry_gate(1, {"ignoreMarketGate": True}, r_settings={"gate": "correction"}, open_r=0)
    assert r["allowed"] is True, r


def test_ignore_market_gate_bypasses_pressure():
    r = _entry_gate(1, {"ignoreMarketGate": True}, r_settings={"gate": "pressure"}, open_r=0)
    assert r["allowed"] is True, r


def test_market_gate_still_blocks_when_not_ignored_correction():
    """회귀 방지 — ignoreMarketGate를 안 넘기면("+일지") 기존처럼 그대로 막혀야 함."""
    r = _entry_gate(1, {}, r_settings={"gate": "correction"}, open_r=0)
    assert r["allowed"] is False
    assert "조정" in r["reason"]


def test_market_gate_still_blocks_when_not_ignored_pressure_cap():
    """pressure는 즉시 차단 사유가 아니라 cap 1.5R로 줄어드는 방식이라,
    open+new가 1.5R을 넘으면 "오픈 리스크 상한"으로 막혀야 한다(정상
    동작 — 이건 게이트가 변장한 게 아니라 실제 pressure cap 규칙)."""
    r = _entry_gate(1.6, {}, r_settings={"gate": "pressure"}, open_r=0)
    assert r["allowed"] is False
    assert "오픈 리스크 상한" in r["reason"]


def test_open_risk_still_blocks_with_ignore_market_gate_correction():
    """★ 핵심. 시장게이트를 무시해도 진짜 오픈리스크 초과(기본 cap 3R도
    넘김)는 여전히 막혀야 한다 — 게이트를 빼면서 진짜 리스크 경고까지
    같이 빠지면 안 된다는 요구사항."""
    r = _entry_gate(1, {"ignoreMarketGate": True}, r_settings={"gate": "correction", "max_open_r": 3}, open_r=3.5)
    assert r["allowed"] is False
    assert "오픈 리스크 상한" in r["reason"]


def test_open_risk_still_blocks_with_ignore_market_gate_pressure():
    r = _entry_gate(1, {"ignoreMarketGate": True}, r_settings={"gate": "pressure", "max_open_r": 3}, open_r=3.5)
    assert r["allowed"] is False
    assert "오픈 리스크 상한" in r["reason"]


def test_weekly_circuit_breaker_still_blocks_with_ignore_market_gate():
    r = _entry_gate(1, {"ignoreMarketGate": True}, r_settings={"gate": "correction", "weekly_stop_r": 3}, open_r=0, week=-3.5)
    assert r["allowed"] is False
    assert "주간 서킷브레이커" in r["reason"]


def test_monthly_circuit_breaker_still_blocks_with_ignore_market_gate():
    r = _entry_gate(1, {"ignoreMarketGate": True}, r_settings={"gate": "correction", "monthly_stop_r": 6}, open_r=0, month=-6.5)
    assert r["allowed"] is False
    assert "월간 서킷브레이커" in r["reason"]


def test_disguised_market_gate_trap_correction():
    """★★ 이번에 발견한 함정, 사용자가 핵심이라고 지목한 케이스.
    open=1.0R, new=1R → 합계 2.0R. 기본 cap(3R)은 안 넘지만, correction
    cap(0R)/pressure cap(1.5R)은 넘는다. ignoreMarketGate:true라면
    effectiveCapR도 같이 ignoreGate로 호출돼 기본 cap을 써야 하므로
    허용돼야 한다 — cap 전달이 빠지면(사보타지) 시장게이트가 "오픈
    리스크 상한 초과"로 변장해 다시 막힌다."""
    r = _entry_gate(1, {"ignoreMarketGate": True}, r_settings={"gate": "correction", "max_open_r": 3}, open_r=1.0)
    assert r["allowed"] is True, f"시장게이트가 오픈리스크 상한으로 변장해서 다시 막힘: {r}"


def test_disguised_market_gate_trap_pressure():
    r = _entry_gate(1, {"ignoreMarketGate": True}, r_settings={"gate": "pressure", "max_open_r": 3}, open_r=1.0)
    assert r["allowed"] is True, f"시장게이트가 오픈리스크 상한으로 변장해서 다시 막힘: {r}"


# ---------------------------------------------------------------------------
# renderCalcLine() — 실제 화면 문구(+일지 vs +직접 추가) 검증.
# ---------------------------------------------------------------------------

def test_ma_calc_line_hides_market_gate_reason_during_correction():
    """"+직접 추가"(maCalc, ignoreMarketGate:true) — 조정 상태에서 시장게이트
    문구도, "저장은 관찰(대기)로만 가능" 강제 문구도 나오면 안 된다."""
    r = _render_calc_line("maCalc", 100, 90, "KR", {"ignoreMarketGate": True},
                           r_settings={"gate": "correction"}, open_r=0)
    assert "시장 게이트" not in r["html"]
    assert "조정" not in r["html"]
    assert "관찰(대기)로만 가능" not in r["html"]
    assert r["locked"] is False


def test_jm_calc_line_still_shows_market_gate_reason_during_correction():
    """"+일지"(jmCalc, 옵션 없음) — 기존처럼 시장게이트 문구와 강제
    문구가 그대로 나와야 한다(회귀 방지). 강제 문구의 "관찰(대기)"는
    <b> 태그로 감싸여 있어(HTML) "로만 가능"과 붙어있지 않음 — 태그
    경계를 감안해 두 조각을 각각 확인."""
    r = _render_calc_line("jmCalc", 100, 90, "KR", {},
                           r_settings={"gate": "correction"}, open_r=0)
    assert "시장 게이트" in r["html"]
    assert "저장은" in r["html"] and "관찰(대기)" in r["html"] and "로만 가능" in r["html"]
    assert r["locked"] is True


def test_ma_calc_line_still_shows_open_risk_warning_without_enforcement_note():
    """"+직접 추가"에서도 진짜 오픈리스크 초과는 참고 정보로 보여야
    하지만, "저장은 관찰(대기)로만 가능"이라는 (사실과 다른) 강제 문구는
    붙으면 안 된다."""
    r = _render_calc_line("maCalc", 100, 90, "KR", {"ignoreMarketGate": True},
                           r_settings={"gate": "correction", "max_open_r": 3}, open_r=3.5)
    assert "오픈 리스크 상한" in r["html"]
    assert "관찰(대기)로만 가능" not in r["html"]
    assert r["locked"] is True   # 시각적 강조는 유지(정보로서 유효, 설계 보고 3번 판단)


def test_jm_calc_line_shows_open_risk_warning_with_enforcement_note():
    """gate='confirmed'(시장게이트는 통과)인데 오픈리스크만 초과된
    경우 — entryGate()는 시장게이트를 먼저 보고 통과하면 그다음
    오픈리스크를 본다(correction이면 시장게이트 사유가 항상 먼저
    나와 오픈리스크 사유를 가리므로, 이 케이스는 gate='confirmed'로
    분리해서 확인)."""
    r = _render_calc_line("jmCalc", 100, 90, "KR", {},
                           r_settings={"gate": "confirmed", "max_open_r": 3}, open_r=3.5)
    assert "오픈 리스크 상한" in r["html"]
    assert "저장은" in r["html"] and "관찰(대기)" in r["html"] and "로만 가능" in r["html"]


# ---------------------------------------------------------------------------
# saveJournal()/markEntered() 차단 다이얼로그 — 우회로 안내 문구 포함 확인.
# 두 곳 다 DOM/confirm/alert 의존이라 전체 함수를 실행하지 않고, 공유
# 상수 _ENTRY_GATE_BYPASS_HINT를 추출해 두 곳의 실제 소스 문자열 안에
# 그대로 박혀 있는지(재구현 아님, grep 방식) 확인한다.
# ---------------------------------------------------------------------------

def test_bypass_hint_constant_exists():
    text = INDEX_PATH.read_text(encoding="utf-8")
    assert "_ENTRY_GATE_BYPASS_HINT" in text
    m = re.search(r'const _ENTRY_GATE_BYPASS_HINT = "([^"]+)"', text)
    assert m, "_ENTRY_GATE_BYPASS_HINT 상수 선언을 못 찾음"
    hint = m.group(1)
    assert "+직접 추가" in hint and "진입" in hint


def test_save_journal_dialog_includes_bypass_hint():
    text = INDEX_PATH.read_text(encoding="utf-8")
    start = text.index("async function saveJournal()")
    end = text.index("async function", start + 10)
    body = text[start:end]
    assert "_ENTRY_GATE_BYPASS_HINT" in body, "saveJournal()의 차단 confirm에 안내 문구가 없음"


def test_mark_entered_alert_includes_bypass_hint():
    text = INDEX_PATH.read_text(encoding="utf-8")
    start = text.index("async function markEntered(")
    end = text.index("\n}\n", start)
    body = text[start:end]
    assert "_ENTRY_GATE_BYPASS_HINT" in body, "markEntered()의 차단 alert에 안내 문구가 없음"
