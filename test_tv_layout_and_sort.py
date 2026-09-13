"""v5.254 프론트 순수 함수 — tvUrl(레이아웃 ID) · sortThemeRows.

CLAUDE.md "텍스트 추출 + Node 실행" 레시피: production 코드를 그대로 실행한다.
tvUrl은 기존 카드·일지 링크가 전부 쓰는 단일 진입점이라, 레이아웃 ID를 넣은 뒤에도
KR/US 분기가 안 깨졌는지 확인하는 게 핵심이다.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

IDX = Path(__file__).resolve().parent / "static" / "index.html"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node 미설치")


def extract_function(name: str) -> str:
    src = IDX.read_text(encoding="utf-8")
    start = src.index(f"function {name}(")
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError(f"{name}: 닫는 중괄호를 못 찾음")


def layout_const() -> str:
    src = IDX.read_text(encoding="utf-8")
    line = [l for l in src.splitlines() if l.strip().startswith("const TV_LAYOUT_ID")]
    assert len(line) == 1, f"TV_LAYOUT_ID 정의가 {len(line)}개 — 한 곳이어야 한다"
    return line[0]


def run_js(snippet: str, layout_override: str | None = None):
    const = layout_const() if layout_override is None else f'const TV_LAYOUT_ID = "{layout_override}";'
    src = const + "\n" + extract_function("tvSymbolUrl") + "\n" + extract_function("tvUrl") + "\n" + extract_function("sortThemeRows") + "\n" + snippet
    out = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_layout_id_is_defined_once_and_used_by_tvurl():
    assert '"0zrxNaq0"' in layout_const()
    assert "TV_LAYOUT_ID" in extract_function("tvSymbolUrl"), "tvSymbolUrl이 상수를 안 쓴다"
    assert "tvSymbolUrl" in extract_function("tvUrl"), "tvUrl이 공용 헬퍼를 안 쓴다 — 링크가 갈라진다"
    # URL 문자열 하드코딩된 사본이 없어야 한다
    src = IDX.read_text(encoding="utf-8")
    assert src.count("tradingview.com/chart") <= 3, "차트 URL 사본이 늘었다 — tvSymbolUrl 한 곳만 써야 한다"


def test_kr_url_uses_layout():
    got = run_js(
        "console.log(JSON.stringify({"
        " kr: tvUrl('005930.KS'), krObj: tvUrl({ticker:'053260.KQ', market:'KR'}),"
        " us: tvUrl('AAPL', 'us')}));"
    )
    assert got["kr"] == "https://www.tradingview.com/chart/0zrxNaq0/?symbol=KRX%3A005930"
    assert got["krObj"] == "https://www.tradingview.com/chart/0zrxNaq0/?symbol=KRX%3A053260"
    assert got["us"] == "https://www.tradingview.com/chart/0zrxNaq0/?symbol=AAPL"


def test_empty_layout_falls_back_to_original_format():
    """상수를 비우면 레이아웃 없는 기존 형식으로 되돌아가야 한다(되돌리기 보장).

    단 하나 달라진 점: KR 심볼의 콜론이 v5.254부터 %3A로 인코딩된다
    (지수 링크와 공용 헬퍼를 쓰면서 encodeURIComponent를 타게 됨).
    트레이딩뷰는 둘 다 받고, 사용자가 실제 브라우저에서 확인한 URL도
    인코딩된 형태(?symbol=KRX%3A053260)라 의도된 동작이다.
    """
    got = run_js(
        "console.log(JSON.stringify({kr: tvUrl('005930.KS'), us: tvUrl('AAPL','us')}));",
        layout_override="")
    assert got["kr"] == "https://www.tradingview.com/chart/?symbol=KRX%3A005930"
    assert got["us"] == "https://www.tradingview.com/chart/?symbol=AAPL"


def test_sort_puts_nulls_last_regardless_of_key():
    rows = [{"ret20": 1.0, "rs": 50}, {"ret20": None, "rs": 99},
            {"ret20": 5.0, "rs": None}, {"ret20": -2.0, "rs": 70}]
    got = run_js(f"const rows={json.dumps(rows)};"
                 "console.log(JSON.stringify({"
                 " byRet: sortThemeRows(rows,'ret20').map(r=>r.ret20),"
                 " byRs: sortThemeRows(rows,'rs').map(r=>r.rs)}));")
    assert got["byRet"] == [5.0, 1.0, -2.0, None]
    assert got["byRs"] == [99, 70, 50, None]


def test_sort_does_not_mutate_input():
    got = run_js("const rows=[{ret20:1},{ret20:9}];"
                 "const out=sortThemeRows(rows,'ret20');"
                 "console.log(JSON.stringify({orig:rows.map(r=>r.ret20), out:out.map(r=>r.ret20)}));")
    assert got["orig"] == [1, 9], "원본 배열이 정렬로 훼손됐다"
    assert got["out"] == [9, 1]


# ── v5.255: "유니버스 밖" 배지·링크 렌더 ──────────────────────────────
def run_row_js(rows):
    """themeRowHtml을 production 코드 그대로 실행해 렌더 결과를 본다."""
    src = (layout_const() + "\n"
           + extract_function("tvSymbolUrl") + "\n"
           + extract_function("tvUrl") + "\n"
           + extract_function("_tmNum") + "\n"
           + extract_function("themeRowHtml") + "\n"
           + f"const rows={json.dumps(rows)};"
           + "console.log(JSON.stringify(rows.map(r=>{const h=themeRowHtml(r);"
             "return {badge: h.includes('유니버스 밖'), link: h.includes('tradingview.com'), crown: h.includes('👑')};})));")
    out = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_badge_only_when_explicitly_false():
    """v5.254 버그: 업종 층 members엔 in_universe가 없어(undefined) 전부 배지가
    붙고 링크가 사라졌다. undefined/null은 배지 없음이어야 한다."""
    base = {"ticker": "005930.KS", "name": "삼성전자", "price": 100, "ret20": 1.0,
            "ret60": 2.0, "rs": 90, "above_ma200": True, "theme_leader": False}
    rows = [
        {**base},                                # in_universe 없음(업종 층 과거 형태)
        {**base, "in_universe": None},           # 캐시 콜드
        {**base, "in_universe": True},           # 정상
        {**base, "in_universe": False},          # 진짜 유니버스 밖
    ]
    got = run_row_js(rows)
    assert [g["badge"] for g in got] == [False, False, False, True]


def test_link_present_in_every_state():
    """2번 버그: 차트 링크가 사라지면 안 된다 — 유니버스 밖이어도 건다."""
    base = {"ticker": "005930.KS", "name": "삼성전자", "price": None, "ret20": None,
            "ret60": None, "rs": None, "above_ma200": None, "theme_leader": False}
    got = run_row_js([{**base}, {**base, "in_universe": False}, {**base, "in_universe": True}])
    assert all(g["link"] for g in got), got


def test_crown_rendered():
    base = {"ticker": "005930.KS", "name": "삼성전자", "price": 1, "ret20": 1.0,
            "ret60": 1.0, "rs": 99, "above_ma200": True, "in_universe": True}
    got = run_row_js([{**base, "theme_leader": True}, {**base, "theme_leader": False}])
    assert [g["crown"] for g in got] == [True, False]


def test_table_columns_are_fixed_width():
    """3번: width:100%만 주면 화면이 넓을수록 이름과 숫자가 벌어진다."""
    body = extract_function("themeTableHtml")
    assert "table-layout:fixed" in body and "max-width:560px" in body
    assert "<colgroup>" in body
