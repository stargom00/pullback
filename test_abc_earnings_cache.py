"""ABC 실적 조회 — 정렬 후 상한 / 캐시 재사용 / 배지 (v5.291, 사용자 지시).

2026-09-27 사고: 상한(`_ABC_EARNINGS_MAX`)이 **정렬 전** cands 순서에 걸려
조회분이 목록 전체에 흩어지고, 상한 초과분이 "판정 불가"로만 보여 "안 본 종목"과
"데이터 없는 종목"이 화면에서 구분되지 않았다. 세 가지를 각각 강제한다.
"""
import asyncio
import re
import time
from pathlib import Path

import pytest

import app
import abc_screener
from test_helpers import code_only

ROOT = Path(__file__).resolve().parent
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


# ── 1) 우선순위 표: 모든 단계를 덮는가 ────────────────────────────────
def test_priority_covers_every_stage():
    """`C_STAGES`에 단계를 추가하고 `_ABC_STAGE_PRIORITY`를 안 고치면 그 단계가
    `.get(..., 9)`로 맨 뒤에 조용히 밀린다 — 여기서 FAIL시킨다."""
    missing = set(abc_screener.C_STAGES) - set(app._ABC_STAGE_PRIORITY)
    assert not missing, f"_ABC_STAGE_PRIORITY에 없는 단계: {missing}"


def test_priority_order_matches_instruction():
    """강돌파 → 벽앞 → 약돌파 → 대기 → 이탈 (사용자 지시). v5.291에서 라벨이
    MA600 기준으로 바뀌어 상수 참조로 검사한다(리터럴 금지)."""
    P = app._ABC_STAGE_PRIORITY
    assert (P[abc_screener.STAGE_STRONG] < P[abc_screener.STAGE_WALL]
            < P[abc_screener.STAGE_WEAK] < P[abc_screener.STAGE_WAIT]
            < P[abc_screener.STAGE_EXIT])


# ── 2) 정렬 후 상한: 우선순위 높은 단계가 조회된다 ────────────────────
def _fake_cands(n_per_stage=12):
    """cands를 **우선순위의 역순**으로 만든다 — 정렬을 안 하면 낮은 우선순위가
    먼저 조회되므로, 정렬 여부가 결과를 가른다(순서 의존 검사 회피)."""
    cands = []
    for stage in (abc_screener.STAGE_EXIT, abc_screener.STAGE_WAIT,
                  abc_screener.STAGE_WEAK, abc_screener.STAGE_WALL,
                  abc_screener.STAGE_STRONG):
        for i in range(n_per_stage):
            cands.append((f"{abc_screener.C_STAGES.index(stage)}{i:03d}.KQ",
                          {"c_stage": stage}))
    return cands


def test_cap_is_applied_after_priority_sort(monkeypatch):
    """상한이 정렬 후에 걸리는가 — 조회된 티커의 단계 분포로 확인한다.

    v5.292: **🩷강돌파는 상한을 우회**하므로(사용자 지시) 강돌파 12건은 전건
    조회되고 상한 24는 **나머지**에만 걸린다. 즉 조회 = 강돌파 12 + 나머지 24.
    강돌파 우회 자체는 `test_abc_grade_v4.py`가 따로 강제한다.
    """
    cands = _fake_cands()
    asked = []

    def fake_axes(t):
        asked.append(t)
        return {"rev_yoy_pos": 1, "rev_yoy_of": 1, "eps_pos_q": 2, "reason": None}

    monkeypatch.setattr(app, "_abc_quarterly_axes", fake_axes)
    monkeypatch.setattr(app, "_ABC_EARNINGS_CACHE", {})
    monkeypatch.setattr(app, "_ABC_EARNINGS_MAX", 24)   # 60건 중 24건만
    fin = asyncio.run(_run_fill(cands))
    stages = {t: s["c_stage"] for t, s in cands}
    n_strong = sum(1 for t in asked if stages[t] == abc_screener.STAGE_STRONG)
    assert n_strong == 12, f"강돌파가 상한에 잘렸다(v5.292 위반): {n_strong}/12"
    assert len(asked) == 12 + 24, f"나머지 상한이 안 맞다: {len(asked)}"
    # 나머지 24건은 **우선순위 순**(벽앞 12 → 약돌파 12)으로 채워져야 한다 —
    # 정렬 전 순서로 잘리면 이탈·대기가 섞인다.
    rest = {stages[t] for t in asked if stages[t] != abc_screener.STAGE_STRONG}
    assert rest == {abc_screener.STAGE_WALL, abc_screener.STAGE_WEAK}, (
        f"정렬 전 순서로 잘렸다: {rest}")
    assert len(fin) == 36


def test_capped_count_is_measured(monkeypatch):
    cands = _fake_cands()
    monkeypatch.setattr(app, "_abc_quarterly_axes",
                        lambda t: {"rev_yoy_pos": 1, "rev_yoy_of": 1, "eps_pos_q": 2, "reason": None})
    monkeypatch.setattr(app, "_ABC_EARNINGS_CACHE", {})
    monkeypatch.setattr(app, "_ABC_EARNINGS_MAX", 24)
    asyncio.run(_run_fill(cands))
    # v5.292: capped는 **비강돌파 몫만** 센다(강돌파 12는 상한 밖에서 전건 조회)
    assert app._abc_earnings_source["capped"] == (60 - 12) - 24
    assert app._abc_earnings_source["ok"] == 36
    assert app._abc_earnings_source["source"] == "mobile_api"


# ── 3) 캐시: 두 번째 호출은 재조회하지 않는다 ─────────────────────────
def test_cache_is_reused_across_calls(monkeypatch):
    cands = _fake_cands(n_per_stage=4)     # 20건
    calls = []
    monkeypatch.setattr(app, "_abc_quarterly_axes",
                        lambda t: (calls.append(t),
                                   {"rev_yoy_pos": 1, "rev_yoy_of": 1, "eps_pos_q": 2,
                                    "reason": None})[1])
    monkeypatch.setattr(app, "_ABC_EARNINGS_CACHE", {})
    monkeypatch.setattr(app, "_ABC_EARNINGS_MAX", 8)
    asyncio.run(_run_fill(cands))
    first = len(calls)
    # v5.292: 강돌파 4건은 상한 밖에서 전건 + 나머지 상한 8건 = 12
    assert first == 4 + 8, first
    asyncio.run(_run_fill(cands))          # 두 번째 로드
    assert len(calls) == first + 8, "캐시된 12건은 재조회하지 않고 새 8건을 채워야 함"
    assert app._abc_earnings_source["cached"] == 12
    # 커버리지가 누적된다 — 이게 캐시를 넣은 이유다(20건 전부 채워짐)
    assert len(app._ABC_EARNINGS_CACHE) == 20


def test_cache_expires_after_ttl(monkeypatch):
    monkeypatch.setattr(app, "_ABC_EARNINGS_CACHE",
                        {"A.KQ": (time.time() - app._ABC_EARNINGS_TTL - 1, {"rev_yoy_pos": 1})})
    assert app._abc_earnings_peek("A.KQ") is None, "TTL 지난 항목은 None이어야 함"
    monkeypatch.setattr(app, "_ABC_EARNINGS_CACHE", {"A.KQ": (time.time(), {"rev_yoy_pos": 1})})
    assert app._abc_earnings_peek("A.KQ") == {"rev_yoy_pos": 1}


def test_ttl_is_24h():
    assert app._ABC_EARNINGS_TTL == 24 * 3600, "사용자 지시 TTL 24h"


def test_abc_constants_are_not_shadowed_by_same_named_globals():
    """이 검사는 실제로 사고를 잡았다: 처음 `_EARNINGS_TTL`로 썼는데 app.py
    14864행의 동명 상수(6h)가 **나중에 정의되며 조용히 덮어써** 캐시가 6h로
    돌았다. 19k줄 파일에서 전역 이름 충돌은 눈에 안 띈다 — 모듈 소스에서
    대입 횟수를 세어 중복 정의를 FAIL시킨다."""
    import re
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    for name in ("_ABC_EARNINGS_TTL", "_ABC_EARNINGS_CACHE", "_ABC_EARNINGS_MAX",
                 "_ABC_STAGE_PRIORITY"):
        n = len(re.findall(rf"^{name}\s*(?::[^=]+)?=", src, re.M))
        assert n == 1, f"{name}이 모듈 최상위에서 {n}번 정의됨 — 뒤 정의가 앞을 덮는다"
    # 기존 상수는 그대로여야 한다(우리가 남의 것을 덮지 않았는지)
    assert app._EARNINGS_TTL == 6 * 3600, "v5.05 실적 성장 캐시 TTL을 건드렸다"


# ── 4) 화면 배지 ─────────────────────────────────────────────────────
def test_response_exposes_earnings_source():
    import inspect
    src = inspect.getsource(app.api_abc)
    assert '"earnings_source": dict(_abc_earnings_source)' in src


def test_frontend_renders_earnings_badge():
    assert "function _abcEarningsSourceHtml(" in HTML
    body = code_only(HTML[HTML.index("function _abcEarningsSourceHtml("):
                           HTML.index("function renderAbcPage()")])
    assert "상한 초과" in body and "미조회" in body, body
    assert "데이터 없음" in body, "미조회와 데이터 없음을 구분해야 한다"
    # 렌더에 실제로 꽂혀 있어야 한다 — 함수만 있고 안 부르면 화면엔 안 나온다
    page = _block_render()
    assert "_abcEarningsSourceHtml(d.earnings_source)" in page
    assert "_abcFlowSourceHtml(d.flow_source)" in page   # 수급 배지 회귀 방지


def _block_render() -> str:
    start = HTML.index("function renderAbcPage()")
    i = HTML.index("{", start)
    depth = 0
    for j in range(i, len(HTML)):
        if HTML[j] == "{":
            depth += 1
        elif HTML[j] == "}":
            depth -= 1
            if depth == 0:
                return HTML[start:j + 1]
    raise AssertionError("renderAbcPage")


def test_unjudged_tooltip_says_capped_not_just_missing():
    """`판정 불가` 기본 툴팁이 상한 때문임을 말해야 한다 — 예전 '실적 미조회'는
    "데이터가 없다"로도 읽혔다."""
    cell = code_only(HTML[HTML.index("function abcFinCell("):
                           HTML.index("function _abcBadges(")])
    assert "판정 불가" in cell
    m = re.search(r"h\.fin_reason \|\| '([^']+)'", cell)
    assert m, cell
    assert "미조회" in m.group(1) and "상한" in m.group(1), m.group(1)


# ── 헬퍼: api_abc의 실적 채우기 블록만 떼어 실행 ──────────────────────
async def _run_fill(cands):
    """`api_abc()` 안의 실적 블록과 **같은 코드**를 돌린다. 번들·유니버스 없이
    테스트하려면 이 부분만 떼어낼 필요가 있는데, 사본을 만들면 갈라지므로
    `inspect`로 원문을 잘라 `exec`한다(재구현 금지 — CLAUDE.md)."""
    import inspect
    import textwrap
    src = inspect.getsource(app.api_abc)
    begin = src.index("    loop = asyncio.get_event_loop()")
    # ⚠️ 구간 끝 마커는 **바로 다음 단계의 첫 줄**을 쓴다. v5.291에서 수급 주석이
    # v5.275→v5.291로 바뀌며 이 마커가 깨졌다(ValueError) — 주석 문구에 의존하는
    # 마커는 개편마다 깨지므로 코드 줄을 쓴다.
    end = src.index("    _FLOW_STAGES = {")
    block = textwrap.dedent(src[begin:end])
    ns = {"asyncio": asyncio, "time": time, "cands": cands,
          # v5.292: 블록이 `abc_screener.STAGE_STRONG`을 참조한다(강돌파 상한 우회)
          "abc_screener": abc_screener,
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
