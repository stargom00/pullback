"""v5.320 — 일지 상태 버튼 건수 = 카테고리 칩을 반영한 목록 건수 · 탭 이름 · 헤더 여백.

[조사 2026-10-04] 일지 탭에서 카테고리 칩(예: 저점) + 상태(종료) 조합 시 목록이 비는데 상태 버튼은 "종료 40"처럼
보였다. 원인: 상태 버튼 건수(tabCount)는 카테고리를 무시하고 전체를 셌고, 목록은 상태×카테고리로 걸렀다.
데이터 유실 아님 — 운영 일지(읽기 전용 GET) 163건 = CLAUDE.md 기록 건수, id 중복 없음, category='저점' 0건이라
"저점 + 종료"는 비는 게 정상이었다. 수정: 목록과 건수가 같은 순수 함수 journalRowsFor를 쓴다.

사보타주 확인(2026-10-04, FAIL 확인 후 원복): tabCount를 예전 식(카테고리 무시)으로 되돌림 →
test_tab_count_uses_same_filter_as_list FAIL
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html"), encoding="utf-8").read()


def _fn(name):
    start = SRC.index(f"function {name}(")
    i = SRC.index("{", SRC.index(")", start))
    d = 0
    for j in range(i, len(SRC)):
        d += {"{": 1, "}": -1}.get(SRC[j], 0)
        if d == 0:
            return SRC[start:j + 1]
    raise AssertionError(name)


def _js(expr):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    p = subprocess.run(["node", "-e", _fn("journalRowsFor") + f"\nconsole.log(JSON.stringify({expr}));"],
                       capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


# 실데이터 형태: category 없는 레코드(=추세추종), status 없이 result_r만 있는 옛 종료, 관찰
ROWS = [
    {"id": 1, "category": "추세추종", "status": "closed"},
    {"id": 2, "status": "closed"},                                  # category 없음 → 추세추종
    {"id": 3, "result_r": "1.2"},                                   # status 없음 + result_r → closed
    {"id": 4, "category": "재량", "status": "pending"},
    {"id": 5, "category": "재량", "status": "closed"},
    {"id": 6, "category": "저점", "status": "entered"},
    {"id": 7, "category": "관찰", "status": "closed"},              # 관찰은 상태 탭에 안 센다
    {"id": 8, "category": "추세추종", "status": "missed"},
]
TABS = ["entered", "pending", "closed", "missed"]


@pytest.mark.parametrize("cat,closed_ids", [("all", [1, 2, 3, 5]), ("추세추종", [1, 2, 3]), ("재량", [5]), ("저점", [])])
def test_rows_filter_by_status_and_category(cat, closed_ids):
    assert _js(f"journalRowsFor({json.dumps(ROWS)}, 'closed', '{cat}').map(r => r.id)") == closed_ids


@pytest.mark.parametrize("cat", ["all", "추세추종", "재량", "저점"])
def test_counts_match_list_for_every_chip_and_tab(cat):
    """건수 = 그 상태·카테고리로 실제 보이는 목록 길이 — 저점 + 종료는 0."""
    got = _js(f"[{', '.join(repr(t) for t in TABS)}].map(t => journalRowsFor({json.dumps(ROWS)}, t, '{cat}').length)")
    expect = [sum(1 for r in ROWS if (r.get("category") or "추세추종") != "관찰"
                  and (r.get("status") or ("closed" if r.get("result_r") else "entered")) == t
                  and (cat == "all" or (r.get("category") or "추세추종") == cat)) for t in TABS]
    assert got == expect
    if cat == "저점":
        assert got == [1, 0, 0, 0]


def test_watch_view_lists_only_watch():
    assert _js(f"journalRowsFor({json.dumps(ROWS)}, 'closed', '관찰').map(r => r.id)") == [7]


def test_tab_count_uses_same_filter_as_list():
    fn = _fn("renderJournal")
    assert "let j = journalRowsFor(all, journalTab, journalCatFilter);" in fn
    assert "const tabCount = key => journalRowsFor(all, key, journalCatFilter === '관찰' ? 'all' : journalCatFilter).length;" in fn
    assert "all.filter(r => stOf(r) === key && !isWatch(r)).length" not in fn, "예전 카테고리 무시 건수"


# ── 탭 이름 · 헤더 여백 ───────────────────────────────────────────
def test_tab_labels_renamed():
    util = SRC[SRC.index('id="utilTabs"'):SRC.index("</nav>", SRC.index('id="utilTabs"'))]
    labels = [re.sub(r"<[^>]+>", "", b).strip() for b in re.findall(r"<button\b[^>]*>(.*?)</button>", util, re.S)]
    assert labels == ["업종/테마", "마감정리", "추추일지", "저점일지"], labels
    assert 'data-mode="journal">추추일지</button>' in SRC and ">저점일지</button>" in SRC


def test_header_tab_padding_trimmed_without_font_change():
    """이름이 길어져 1440px에서 35px 넘친 것을 여백만 줄여 맞췄다(실측: 1440·1280px에서 저점일지까지 보임,
    오른쪽 묶음까지 24px). 글자 크기·탭 높이는 그대로."""
    assert ".hdr-main .tab{padding:10px 12px;font-size:14px;" in SRC
    assert ".hdr-util .tab{padding:10px 9px;font-size:13px;" in SRC
    assert "min-height:44px" in SRC[SRC.index(".hdr .tab{"):SRC.index("}", SRC.index(".hdr .tab{"))]
