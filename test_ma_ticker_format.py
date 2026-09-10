"""saveManualAdd()("+직접 추가" 모달) 저장 시점 티커 형식 검증
(v5.243, 사용자 지시 — 2026-09-10 "한미사이언스" 오염 사고 재발 방지).

배경: journal_user.json 프로덕션 레코드 중 1건이 ticker="한미사이언스"
(심볼로 불가능한 한글), name="008930.KS"(실제 티커)로 저장돼 있었다 —
saveManualAdd()가 티커 칸 값을 형식 검증 없이 그대로 저장했기 때문
(이름 해석은 애초에 이 모달의 역할이 아님, 설계 보고 참고). 이 레코드는
매 updateTracking() 폴링마다 /api/prices → yfinance로 흘러들어가
"$한미사이언스: No data found" 로그를 반복 재현시켰다.

수정: static/index.html에 순수 함수 2개 신설 — `_isValidTickerFormat`
(형식 검증만, 이름 해석 없음 — saveManualAdd()가 티커/종목명 독립
자유입력 모달이라는 성격을 안 바꾸기로 한 설계 결정) + `_maTickerFormatCheck`
(검증 실패 시 알림 메시지 조립, name 칸이 티커 형식이면 스왑 의심
안내를 조건부로 덧붙임 — 별도 감지 상태 없이 메시지에만 얹음).
saveManualAdd()는 이 두 함수의 결과로 저장 여부만 판단한다.

레시피: 정규식이 아니라 중괄호 깊이로 함수를 텍스트 그대로 추출해
Node로 직접 실행(재구현 아님) — test_price_basis_note.py와 동일 패턴.
"""
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
        f"static/index.html에서 `{marker}`를 못 찾음 — 이름이 바뀌었거나 "
        "삭제됐는지 확인 (이 테스트 자체를 같이 갱신할 것)"
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


IS_VALID_TICKER_FORMAT_SRC = _extract_function("_isValidTickerFormat")
MA_TICKER_FORMAT_CHECK_SRC = _extract_function("_maTickerFormatCheck")
# _maTickerFormatCheck가 내부에서 _isValidTickerFormat을 호출하므로 둘 다 필요.
COMBINED_SRC = IS_VALID_TICKER_FORMAT_SRC + "\n" + MA_TICKER_FORMAT_CHECK_SRC


def _run_node(script: str):
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — 실행 테스트 스킵")
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=15)
    if res.returncode != 0:
        raise AssertionError(f"node 실행 실패:\n{res.stderr}")
    return res.stdout


def _is_valid_ticker_format_batch(tickers: list[str]) -> list[bool]:
    script = f"""
{IS_VALID_TICKER_FORMAT_SRC}
const tickers = {json.dumps(tickers)};
console.log(JSON.stringify(tickers.map(_isValidTickerFormat)));
"""
    return json.loads(_run_node(script))


def _ma_ticker_format_check(ticker: str, name_val: str) -> dict:
    script = f"""
{COMBINED_SRC}
console.log(JSON.stringify(_maTickerFormatCheck({json.dumps(ticker)}, {json.dumps(name_val)})));
"""
    return json.loads(_run_node(script))


# ---------------------------------------------------------------------------
# 1) 개별 형식 케이스 — 한글 거부, 정상 KR/US 통과, 엣지 형태(BRK-B/단일문자/
#    접미사없는 KR코드) 통과, 공백 포함 거부.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker,expected", [
    ("한미사이언스", False),
    ("아", False),
    ("AAP L", False),           # 공백 포함
    ("", False),
    ("005930.KS", True),
    ("008930.KS", True),
    ("477850", True),            # KR 접미사 없는 5~6자리 숫자코드(기존 자동보정 대상)
    ("AAPL", True),
    ("V", True),                 # 단일문자 심볼(유니버스 실존: V/F/X 등)
    ("F", True),
    ("BRK-B", True),             # 하이픈 포함 심볼(유니버스 실존)
    ("MU", True),
])
def test_is_valid_ticker_format_cases(ticker, expected):
    result = _is_valid_ticker_format_batch([ticker])[0]
    assert result == expected, f"{ticker!r} → 기대 {expected}, 실제 {result}"


# ---------------------------------------------------------------------------
# 2) 전체 KR/US 유니버스 전수 — 거짓 배제 0건 확인. 프로덕션 journal의 실제
#    거래 종목(개인정보 성격이라 커밋 대상 아님)은 이 유니버스의 부분집합임을
#    이미 별도로 확인(설계 보고 — 150건 전수 거짓배제 0건, 세션 로그 참고)
#    — 여기서는 공개 시장 데이터인 universe.py 전체(KR 1505 + US 2120)로
#    같은 것을 재현·영구 테스트화한다(유니버스가 이 서브셋의 상위집합).
# ---------------------------------------------------------------------------

def test_full_universe_no_false_rejects():
    from universe import get_universe

    kr_tickers = list(get_universe("kr").keys())
    us_tickers = list(get_universe("us").keys())
    all_tickers = kr_tickers + us_tickers
    assert len(all_tickers) > 3000, "유니버스 로드가 비정상적으로 작음 — get_universe() 확인 필요"

    results = _is_valid_ticker_format_batch(all_tickers)
    false_rejects = [t for t, ok in zip(all_tickers, results) if not ok]
    assert not false_rejects, (
        f"정상 유니버스 티커 {len(false_rejects)}건이 거짓 배제됨(형식 검증이 "
        f"너무 공격적) — 예: {false_rejects[:20]}"
    )


# ---------------------------------------------------------------------------
# 3) saveManualAdd() 저장 게이트 — _maTickerFormatCheck() 반환값(ok/message)
# ---------------------------------------------------------------------------

def test_korean_ticker_rejected_with_raw_value_shown():
    r = _ma_ticker_format_check("한미사이언스", "")
    assert r["ok"] is False
    assert "한미사이언스" in r["message"], "사용자가 입력한 원문이 알림에 그대로 보여야 함"
    assert "티커만 입력하세요" in r["message"]


def test_normal_kr_ticker_passes():
    assert _ma_ticker_format_check("005930.KS", "삼성전자")["ok"] is True
    assert _ma_ticker_format_check("008930.KS", "")["ok"] is True


def test_normal_us_ticker_passes():
    for tk in ("AAPL", "V", "F", "BRK-B"):
        r = _ma_ticker_format_check(tk, "")
        assert r["ok"] is True, f"{tk} 이 거부됨: {r}"


def test_swap_case_rejected_with_swap_hint():
    """★ 실제 프로덕션 사고 재현. ticker 칸에 한글, name 칸에 티커 형식
    값이 들어간 경우 — 거부는 물론, name 칸이 티커처럼 보인다는 힌트도
    함께 떠야 한다."""
    r = _ma_ticker_format_check("한미사이언스", "008930.KS")
    assert r["ok"] is False
    assert "한미사이언스" in r["message"]
    assert "바뀌지 않았나요" in r["message"]
    assert "008930.KS" in r["message"]


def test_swap_hint_absent_when_name_also_not_ticker_shaped():
    """name 칸도 티커 형식이 아니면(정상적인 종목명 텍스트) 스왑 힌트를
    붙이지 않아야 한다 — 오탐이어도 해가 없어야 한다는 요구사항의 반대
    방향(불필요한 힌트를 남발하지 않는지) 확인."""
    r = _ma_ticker_format_check("한미사이언스", "비자")
    assert r["ok"] is False
    assert "바뀌지 않았나요" not in r["message"]


def test_swap_hint_never_blocks_when_ticker_itself_valid():
    """name 칸 값이 우연히 티커처럼 보여도, ticker 칸 자체가 유효하면
    저장을 막으면 안 된다(사용자 지시 — 스왑 감지가 추가로 저장을
    막아선 안 됨)."""
    r = _ma_ticker_format_check("AAPL", "MSFT")   # name도 티커 형식이지만 무관
    assert r["ok"] is True
    assert r["message"] is None
