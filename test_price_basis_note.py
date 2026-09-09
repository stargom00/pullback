"""🔴 즉시행동 카드 "가격 기준" 표기 커버리지 테스트 (v5.233, 사용자 지시).

배경: v5.232에서 카드 가격이 "지금 가격"이 아닐 때 보여주는 표기
("(전일 종가 기준)"/"(전 거래일 종가 · 장전)")를 클라이언트 시간판정
(isUsMarketOpen 등, 삭제됨)에서 서버 계산(is_live/market_open_now 두
불리언, get_calendar())으로 옮기면서, 판정 로직을 static/index.html의
`priceBasisNoteText(item)` 순수 함수로 분리했다. 그런데 이 함수를 실제로
타는 production 데이터는 지금 US 눌림목 즉시진입(entry_method="즉시")
카드뿐이다 — KR 쪽은 이 entry_method를 쓰는 소스가 현재 하나도 없어서
(app.py 확인 완료) 함수 자체는 market을 아예 안 보고 판정하도록 만들어
뒀지만("이 판정은 시장과 무관"이라는 설계 의도), 그 의도가 실제로 맞는지
KR 데이터로는 한 번도 검증된 적이 없다.

이건 test_trace_parity.py가 겪었던 것과 같은 종류의 구멍이다: 그 테스트도
"셋업별 커버리지 0건"이 그냥 조용히 통과로 표시되다가(v5.64 이전) 나중에
발견됐다(MIN_COVERAGE hard FAIL로 전환한 이유). 여기서는 애초에 실데이터로
그 커버리지를 못 만드니(KR 즉시진입 카드가 production에 없음), 가짜
데이터로 KR×US · 장중(라이브)×장전×장중-stale 조합을 직접 만들어 실제
production 함수(재구현 아님 — static/index.html에서 텍스트 그대로 추출해
Node로 실행)를 검증한다.

추출 방식: 정규식이 아니라 중괄호를 직접 세서 함수 끝을 찾는다(정규식만
으로는 중첩 중괄호가 있는 함수 본문을 안전하게 못 자름) — test_trace_
const_audit.py가 AST를 쓰는 것과 같은 이유로, "텍스트 매칭이 우연히 맞는"
상황을 피한다."""
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
    하나를 텍스트 그대로 추출. 중괄호 깊이를 세어 정확한 끝을 찾는다."""
    text = INDEX_PATH.read_text(encoding="utf-8")
    marker = f"function {name}("
    start = text.find(marker)
    assert start != -1, (
        f"static/index.html에서 `{marker}`를 못 찾음 — priceBasisNoteText가 "
        "삭제됐거나 이름이 바뀌었는지 확인 (이 테스트 자체를 같이 갱신할 것)"
    )
    brace_start = text.index("{", start)
    depth = 0
    for i in range(brace_start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise AssertionError(f"`{name}` 함수의 닫는 중괄호를 못 찾음 — 파일이 잘렸을 가능성")


PRICE_BASIS_NOTE_TEXT_SRC = _extract_function("priceBasisNoteText")

# v5.233(사용자 지시): KR×장중, KR×장전, US×장중, US×장전 네 조합 +
# "장중인데 카드가 stale"(장전과 문구가 다름, v5.232 [4]의 핵심 확장분)
# 을 KR/US 양쪽에 추가 — is_live/market_open_now 두 불리언의 실질적
# 조합(라이브/장전/장중-stale)을 KR·US 각각에서 전부 밟는다.
# (설명, market, is_live, market_open_now, 기대 결과)
CASES = [
    ("KR 장중(라이브)",              "KR", True,  True,  None),
    ("KR 장전(휴장/개장 전)",         "KR", False, False, "(전 거래일 종가 · 장전)"),
    ("KR 장중인데 카드가 stale",      "KR", False, True,  "(전일 종가 기준)"),
    ("US 장중(라이브)",              "US", True,  True,  None),
    ("US 장전(휴장/개장 전)",         "US", False, False, "(전 거래일 종가 · 장전)"),
    ("US 장중인데 카드가 stale",      "US", False, True,  "(전일 종가 기준)"),
    # is_live 필드 자체가 없는 소스(종가진입류, jongga 등) — 표기 대상 아님.
    ("종가진입류(is_live 필드 없음)", "KR", None,  None,  None),
]


def _run_price_basis_note(cases):
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — priceBasisNoteText 실행 테스트 스킵")
    items = []
    for _desc, market, is_live, market_open_now, _expected in cases:
        item = {"market": market}
        # None은 "필드 자체가 없음"(JS의 undefined)을 의미 — 키를 아예
        # 안 넣어야 JSON 직렬화 시 필드가 빠지고, JS에서 진짜 undefined가 된다.
        if is_live is not None:
            item["is_live"] = is_live
        if market_open_now is not None:
            item["market_open_now"] = market_open_now
        items.append(item)
    script = f"""
{PRICE_BASIS_NOTE_TEXT_SRC}
const items = {json.dumps(items)};
console.log(JSON.stringify(items.map(priceBasisNoteText)));
"""
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=15)
    if res.returncode != 0:
        raise AssertionError(f"node 실행 실패:\n{res.stderr}")
    return json.loads(res.stdout)


def test_price_basis_note_all_combinations():
    """KR×US · 장중(라이브)×장전×stale 7조합(가짜 데이터) 전부 실제
    production 함수(priceBasisNoteText)로 직접 검증 — 하나라도 문구가
    어긋나면 FAIL(경고 아님, README/CLAUDE.md "실패로 만든다" 원칙)."""
    results = _run_price_basis_note(CASES)
    assert len(results) == len(CASES), (
        f"결과 개수({len(results)})가 케이스 개수({len(CASES)})와 다름 — node 스크립트 자체가 깨졌을 가능성"
    )
    mismatches = [
        f"  {desc} (market={market}, is_live={is_live}, market_open_now={market_open_now}): "
        f"기대 {expected!r}, 실제 {actual!r}"
        for (desc, market, is_live, market_open_now, expected), actual in zip(CASES, results)
        if actual != expected
    ]
    assert not mismatches, "priceBasisNoteText 불일치:\n" + "\n".join(mismatches)


def test_price_basis_note_market_agnostic():
    """market 필드는 판정에 영향을 주면 안 된다(설계 의도 — get_calendar()의
    _calendar_default_market_session() 옆 주석 참고). 같은 is_live/
    market_open_now 조합에서 KR과 US 결과가 다르면, 누군가 나중에 market
    분기를 실수로 다시 끼워넣었다는 신호 — 그 회귀를 여기서 직접 잡는다."""
    kr_live, kr_premarket, kr_stale = CASES[0], CASES[1], CASES[2]
    us_live, us_premarket, us_stale = CASES[3], CASES[4], CASES[5]
    results = _run_price_basis_note([kr_live, us_live, kr_premarket, us_premarket, kr_stale, us_stale])
    pairs = [
        ("장중(라이브)", results[0], results[1]),
        ("장전", results[2], results[3]),
        ("장중-stale", results[4], results[5]),
    ]
    mismatches = [
        f"  {label}: KR={kr_result!r} vs US={us_result!r}"
        for label, kr_result, us_result in pairs
        if kr_result != us_result
    ]
    assert not mismatches, (
        "priceBasisNoteText가 market에 의존하게 됨(회귀) — 결과가 KR/US 사이에 달라짐:\n"
        + "\n".join(mismatches)
    )
