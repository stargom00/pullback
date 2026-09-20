"""v5.275 — 기관·외국인 순매수 5일 (표시 전용).

[소스 한계 — 이 파일이 기록하는 것] naver 모바일 API는 **외국인·기관·개인
셋만** 준다. 지시는 원래 "기관·기타법인"이었는데 **기타법인 필드가 없고**,
셋의 합으로 낸 잔차도 기타법인이 아니다 — 삼성전자 5일 잔차가 +1.7M~+2.0M,
SK하이닉스가 +575K~+663K로 **부호·크기가 거의 고정**이라 실제 순매수 계열일
수 없는 계통 오차다. 그래서 외국인으로 대체했다(사용자 확정).

[조용한 빈 결과] 없는 종목도 **200 OK에 `[]`**를 준다(실측). "못 받음"과
"받았는데 0건"과 "정상"을 전부 구분한다 — v5.246~v5.252에서 같은 벤더의 같은
실패로 세 번 당했다.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import investor_flow as F  # noqa: E402
import naver_kr  # noqa: E402


def row(biz="20260918", organ="+1", foreign="+1"):
    return {"bizdate": biz, "organPureBuyQuant": organ,
            "foreignerPureBuyQuant": foreign}


def rows(spec):
    """spec: [(organ, foreign), ...] 최신순."""
    return [row(f"2026091{8-i}", o, f) for i, (o, f) in enumerate(spec)]


# ── 세기 ────────────────────────────────────────────────────────────
def test_counts_only_net_buy_days():
    r = F.summarize(rows([("+1", "-1"), ("+1", "+1"), ("-1", "+1"),
                          ("+1", "-1"), ("-1", "-1")]))
    assert r["ok"] and r["organ"] == 3 and r["foreign"] == 2, r
    assert r["text"] == "기관 +3/5 · 외국인 +2/5", r["text"]


def test_zero_is_not_a_net_buy():
    """0은 순매수가 아니다 — `>= 0`으로 쓰면 무거래일이 매수로 잡힌다."""
    r = F.summarize(rows([("0", "0")] * 5))
    assert r["organ"] == 0 and r["foreign"] == 0


def test_comma_and_plus_are_parsed():
    r = F.summarize([row(organ="+2,746,972", foreign="-1,673,323")])
    assert r["organ"] == 1 and r["foreign"] == 0, r


def test_only_the_window_is_counted():
    """20일을 받아 캐시하되 **화면은 5일**만 쓴다(사용자 지시)."""
    r = F.summarize(rows([("+1", "+1")] * 20))
    assert r["of"] == F.WINDOW == 5, r
    assert r["organ"] == 5


# ── 분모 ────────────────────────────────────────────────────────────
def test_denominator_is_the_days_actually_counted():
    """상장 직후처럼 3일뿐이면 "+2/5"가 아니라 "+2/3"여야 한다 —
    5로 고정하면 나머지 2일이 순매도였던 것처럼 읽힌다."""
    r = F.summarize(rows([("+1", "+1"), ("+1", "-1"), ("-1", "-1")]))
    assert r["of"] == 3, r["of"]
    assert r["text"] == "기관 +2/3 · 외국인 +1/3", r["text"]


def test_days_with_no_values_drop_out_of_the_denominator():
    data = rows([("+1", "+1"), ("+1", "+1")]) + [{"bizdate": "20260916"}]
    r = F.summarize(data)
    assert r["of"] == 2, r["of"]


# ── 기준일 ──────────────────────────────────────────────────────────
def test_asof_is_the_newest_bizdate_formatted():
    """1거래일 지연이 있을 수 있어 **기준일을 반드시 같이 낸다**(사용자 지시)."""
    r = F.summarize(rows([("+1", "+1")] * 5))
    assert r["asof"] == "09-18", r["asof"]


def test_odd_bizdate_is_passed_through_not_crashed():
    r = F.summarize([row(biz="abc")])
    assert r["asof"] == "abc"


# ── 세 가지 실패를 구분 ─────────────────────────────────────────────
def test_fetch_failure_is_distinct_from_empty():
    assert F.summarize(None)["reason"] == "조회 실패"
    assert F.summarize([])["reason"] == "데이터 없음"


def test_schema_change_is_reported_not_counted_as_zero():
    """필드명이 바뀌면 **0/5가 아니라 사유**가 나와야 한다 — 0으로 뭉개면
    '아무도 안 샀다'로 읽혀 벤더 개편을 몇 달 뒤에나 안다."""
    r = F.summarize([{"bizdate": "20260918", "orgBuy": "+1"}] * 5)
    assert not r["ok"] and r["reason"] == "순매수 필드 없음", r


def test_failures_carry_no_text():
    for bad in (None, [], [{"bizdate": "20260918"}]):
        r = F.summarize(bad)
        assert r["text"] is None and not r["ok"], r


def test_keys_always_present():
    for bad in (None, []):
        for k in ("ok", "reason", "organ", "foreign", "of", "asof", "text"):
            assert k in F.summarize(bad), k


# ── 소스 계약 (네트워크) ────────────────────────────────────────────
def test_source_has_no_other_corporate_field():
    """**기타법인이 생기면 알아야 한다** — 생기면 이 테스트가 실패하고,
    그때 docs의 '별도 소스 필요' 기록을 지우면 된다."""
    r = naver_kr.fetch_investor_trend("005930.KS")
    if r is None or not r:
        pytest.skip("조회 실패(망)")
    keys = set(r[0])
    assert F.ORGAN_KEY in keys and F.FOREIGN_KEY in keys, keys
    other = [k for k in keys if "PureBuy" in k]
    assert sorted(other) == ["foreignerPureBuyQuant", "individualPureBuyQuant",
                             "organPureBuyQuant"], other


def test_source_returns_the_requested_depth():
    """20일을 받아 캐시한다(측정 E 대비) — 기본 10일이라 pageSize가 필요하다."""
    r = naver_kr.fetch_investor_trend("005930.KS", days=20)
    if not r:
        pytest.skip("조회 실패(망)")
    assert len(r) > F.WINDOW, f"{len(r)}건 — 5일 초과를 못 받으면 캐시 의미가 없다"


def test_unknown_ticker_gives_empty_not_an_exception():
    """벤더가 200 OK + [] 를 준다는 실측을 고정한다."""
    r = naver_kr.fetch_investor_trend("999999.KS")
    if r is None:
        pytest.skip("망 문제")
    assert r == [], r
    assert F.summarize(r)["reason"] == "데이터 없음"
