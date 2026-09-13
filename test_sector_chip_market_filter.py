"""v5.256 섹터 칩이 시장 필터를 따라가는지.

[버그] 칩은 서버 sector_summary(market=all 전체 히트)를 로드 시 한 번만
렌더했고, 시장 필터는 프론트 renderCards()에서만 걸렸다 → 미국→한국을 눌러도
칩이 미국 섹터 그대로. 눌림목만이 아니라 칩을 쓰는 전 탭 공통이었다.

CLAUDE.md "텍스트 추출 + Node 실행" 레시피로 production 함수를 그대로 돌린다.
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


def summary_of(hits):
    src = (extract_function("sectorSummaryOf") + "\n"
           + f"console.log(JSON.stringify(sectorSummaryOf({json.dumps(hits)})));")
    out = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


HITS = [
    {"ticker": "A", "market": "US", "sector": "Biotechnology"},
    {"ticker": "B", "market": "US", "sector": "Biotechnology"},
    {"ticker": "C", "market": "US", "sector": "Gold"},
    {"ticker": "D", "market": "US", "sector": "Gold"},
    {"ticker": "E", "market": "KR", "sector": "반도체"},
    {"ticker": "F", "market": "KR", "sector": "반도체"},
    {"ticker": "G", "market": "KR", "sector": "반도체"},
    {"ticker": "H", "market": "KR", "sector": "조선"},   # 1건 — 하한 미만
]


def _by_market(m):
    return [h for h in HITS if m is None or h["market"] == m]


def test_chips_follow_market_filter():
    """이 테스트가 버그의 핵심 — 시장별로 칩 집계가 갈려야 한다."""
    us = [s["sector"] for s in summary_of(_by_market("US"))]
    kr = [s["sector"] for s in summary_of(_by_market("KR"))]
    assert us == ["Biotechnology", "Gold"], us
    assert kr == ["반도체"], kr          # 조선은 1건이라 하한(n>=2) 미만
    assert not set(us) & set(kr), "시장을 바꿨는데 칩이 겹친다"


def test_counts_are_of_filtered_list_not_all():
    """전체(market=all) 기준으로 세면 반도체가 3이 아니라 3 그대로지만,
    Biotechnology가 KR 목록에 남아 있으면 안 된다."""
    kr = summary_of(_by_market("KR"))
    assert kr == [{"sector": "반도체", "count": 3}]
    allm = {s["sector"]: s["count"] for s in summary_of(_by_market(None))}
    assert allm == {"Biotechnology": 2, "Gold": 2, "반도체": 3}


def test_sort_desc_by_count_then_name():
    hits = ([{"sector": "B"}] * 2) + ([{"sector": "A"}] * 2) + ([{"sector": "C"}] * 5)
    assert [s["sector"] for s in summary_of(hits)] == ["C", "A", "B"]


def test_min_count_two_matches_server_rule():
    assert summary_of([{"sector": "X"}]) == []
    assert summary_of([{"sector": "X"}, {"sector": "X"}]) == [{"sector": "X", "count": 2}]


def test_missing_sector_is_skipped():
    hits = [{"sector": None}, {"sector": ""}, {}, {"sector": "Z"}, {"sector": "Z"}]
    assert summary_of(hits) == [{"sector": "Z", "count": 2}]


# ── 소스 구조 검증(순서가 깨지면 버그가 되살아난다) ────────────────────
def _render_cards_src() -> str:
    return extract_function("renderCards")


def test_sector_filter_applied_after_chip_aggregation():
    """집계가 섹터 필터 **앞**이어야 한다 — 뒤면 칩 하나 누르는 순간 그 칩만
    남아 다른 섹터로 옮겨갈 수 없다."""
    body = _render_cards_src()
    agg = body.index("sectorSummaryOf(list)")
    apply_ = body.index("list.filter(h => h.sector === sectorFilter)")
    assert agg < apply_, "섹터 필터가 칩 집계보다 먼저 적용된다"
    # 순서만 보면 "앞에 하나 더 거는" 사보타주를 못 잡는다(실제로 못 잡았다):
    # **목록을 실제로 교체하는** 섹터 필터는 정확히 한 곳이어야 한다.
    # (갇힘 방지용 `const n = list.filter(...).length`는 목록을 안 바꾸므로 제외.)
    assert body.count("list = list.filter(h => h.sector === sectorFilter)") == 1, \
        "섹터로 목록을 거르는 지점이 여러 곳 — 칩 집계 전에 이미 걸러졌을 수 있다"
    # 시작 목록은 아무 섹터 필터도 안 걸린 원본이어야 한다.
    first = body[body.index("let list ="):body.index("\n", body.index("let list ="))]
    assert "sectorFilter" not in first, f"시작 목록부터 섹터가 걸려 있다: {first.strip()}"


def test_chip_aggregation_after_market_filter():
    """시장 필터 **뒤**여야 칩이 시장을 따라간다(버그의 직접 원인)."""
    body = _render_cards_src()
    mkt = body.index("h.market === 'KR'")
    agg = body.index("sectorSummaryOf(list)")
    assert mkt < agg, "칩 집계가 시장 필터보다 먼저다 — v5.255 버그 그대로"


def test_no_stale_server_summary_render_on_load():
    """로드 시 서버 summary로 칩을 그리던 호출이 남아 있으면 한 프레임 번쩍인다."""
    src = IDX.read_text(encoding="utf-8")
    assert "renderSectors(data.sector_summary" not in src


def test_chip_click_delegates_to_render_cards():
    """칩 클릭이 옛 summary로 직접 다시 그리면 시장 필터 반영 전 상태가 남는다."""
    body = extract_function("renderSectors")
    click = body[body.index("data-sec]').forEach"):]
    assert "renderCards()" in click
    assert "renderSectors(summary)" not in click, "옛 summary로 재렌더하고 있다"


def test_stuck_filter_is_released_or_kept_visible():
    """시장을 바꿔 선택 섹터가 사라지면 자동 해제(갇힘 방지),
    하한 미만으로 남아 있으면 칩을 끼워 해제 가능하게."""
    body = _render_cards_src()
    assert "if (n === 0) sectorFilter = null;" in body
    assert "secSummary.push({ sector: sectorFilter, count: n })" in body
