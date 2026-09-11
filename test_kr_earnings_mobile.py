"""KR 실적 소스 모바일 API 교체 + naver 개편 가시화 (v5.252, 사용자 지시).

사고: finance.naver.com PC 페이지 SPA 개편(2026-09-10 장마감 전후)으로
earnings._kr_earnings_growth()가 KR 전 종목 "실적 표 없음"을 반환했다 —
💰실적우수 배지가 KR에서 사라지고, 실적 탭 KR이 0건이 되고, 섹터 대장이
전원 "진짜 대장 아님"(회색)으로 뒤집혔는데 로그도 배지도 없었다.

검증:
1. 모바일 API 실제 응답(삼성전자 고정값)으로 기존 판정 로직이 그대로 돌아가는지
   — 3년 연속 EPS 증가/분기 EPS YoY/매출 YoY/최근 4분기 EPS 합/컨센서스 제외.
2. 알파벳 혼용 코드(0011A0)가 URL에 그대로 쓰이는지.
3. 조회 실패 시 판정불가(크래시 아님) + 실패 결과는 30분만 캐시되는지.
4. 대장 3상태(True/None/False) — 실적 판정 불가를 "탈락"으로 뭉개지 않는지.
5. TIMING kr_earnings_* + 경고 로그 + 스키마 동기화.
6. 화면 배지(_krEarningsBadgeHtml/_krEarningsUnknownHtml/_sectorLeaderCompactHtml).
7. /api/eod 거래대금 상위가 v2 캐시를 재사용하고 소스를 명시하는지.
8. PC 페이지 의존 0건(naver_kr에 죽은 스크레이퍼가 남아있지 않은지).
"""
import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import app
import earnings
import naver_kr
from test_fetch_market_data_all_merge import mocked_env  # noqa: F401

ROOT = Path(__file__).resolve().parent
INDEX_PATH = ROOT / "static" / "index.html"

# m.stock.naver.com/api/stock/005930/finance/{annual,quarter} 실제 응답
# (2026-09-12 수신, EPS·매출액 행만 남김) — 재구현이 아니라 실제 페이로드로
# 기존 판정 로직을 돌린다.
SAMSUNG_FIXTURE = {'annual': {'financeInfo': {'trTitleList': [{'isConsensus': 'N',
                                             'title': '2023.12.',
                                             'key': '202312'},
                                            {'isConsensus': 'N',
                                             'title': '2024.12.',
                                             'key': '202412'},
                                            {'isConsensus': 'N',
                                             'title': '2025.12.',
                                             'key': '202512'},
                                            {'isConsensus': 'Y',
                                             'title': '2026.12.',
                                             'key': '202612'}],
                            'rowList': [{'title': '매출액',
                                         'columns': {'202512': {'value': '3,336,059', 'cx': None},
                                                     '202612': {'value': '7,396,375', 'cx': None},
                                                     '202312': {'value': '2,589,355', 'cx': None},
                                                     '202412': {'value': '3,008,709', 'cx': None}}},
                                        {'title': 'EPS',
                                         'columns': {'202512': {'value': '6,564', 'cx': None},
                                                     '202612': {'value': '48,239', 'cx': None},
                                                     '202312': {'value': '2,131', 'cx': None},
                                                     '202412': {'value': '4,950', 'cx': None}}}]}},
 'quarter': {'financeInfo': {'trTitleList': [{'isConsensus': 'N',
                                              'title': '2025.06.',
                                              'key': '202506'},
                                             {'isConsensus': 'N',
                                              'title': '2025.09.',
                                              'key': '202509'},
                                             {'isConsensus': 'N',
                                              'title': '2025.12.',
                                              'key': '202512'},
                                             {'isConsensus': 'N',
                                              'title': '2026.03.',
                                              'key': '202603'},
                                             {'isConsensus': 'N',
                                              'title': '2026.06.',
                                              'key': '202606'},
                                             {'isConsensus': 'Y',
                                              'title': '2026.09.',
                                              'key': '202609'}],
                             'rowList': [{'title': '매출액',
                                          'columns': {'202509': {'value': '860,617', 'cx': None},
                                                      '202609': {'value': '2,063,821', 'cx': None},
                                                      '202606': {'value': '1,714,995', 'cx': None},
                                                      '202506': {'value': '745,663', 'cx': None},
                                                      '202603': {'value': '1,338,734', 'cx': None},
                                                      '202512': {'value': '938,374', 'cx': None}}},
                                         {'title': 'EPS',
                                          'columns': {'202509': {'value': '1,783', 'cx': None},
                                                      '202609': {'value': '14,323', 'cx': None},
                                                      '202606': {'value': '10,718', 'cx': None},
                                                      '202506': {'value': '733', 'cx': None},
                                                      '202603': {'value': '6,993', 'cx': None},
                                                      '202512': {'value': '2,864', 'cx': None}}}]}}}


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _patch_finance(monkeypatch, fixture=None, error=None, seen=None):
    def fake_get(url, headers=None, timeout=None, **kw):
        if seen is not None:
            seen.append(url)
        if error:
            raise error
        period = "annual" if url.endswith("annual") else "quarter"
        return _Resp((fixture or SAMSUNG_FIXTURE)[period])
    monkeypatch.setattr(earnings.requests, "get", fake_get)


# ---------------------------------------------------------------------------
# 1) 판정 로직 무변경 — 실제 응답으로 재확인
# ---------------------------------------------------------------------------

def test_kr_earnings_from_mobile_api(monkeypatch):
    _patch_finance(monkeypatch)
    r = earnings.get_earnings_growth("005930.KS")
    assert r["ok"] is True
    assert r["verdict"] == "pass"
    assert r["annual_eps_growing"] is True          # 2,131 < 4,950 < 6,564 (실제치 3년)
    assert r["annual_eps"] == [2131.0, 4950.0, 6564.0, 48239.0]
    assert r["annual_eps_actual_only"] == [2131.0, 4950.0, 6564.0]   # 2026.12(E) 제외
    assert r["quarterly_eps_yoy_pct"] == 1362.2     # 10,718 vs 733 (4분기 전)
    assert r["revenue_yoy_pct"] == 130.0            # 1,714,995 vs 745,663
    assert r["eps_sum_last4q"] == 1783 + 2864 + 6993 + 10718
    assert r["reasons"] == []


def test_consensus_columns_excluded(monkeypatch):
    """컨센서스(isConsensus=Y) 열이 실제치로 섞이면 판정이 통째로 틀어진다."""
    _patch_finance(monkeypatch)
    r = earnings.get_earnings_growth("005930.KS")
    assert 48239.0 not in r["annual_eps_actual_only"]
    assert r["eps_sum_last4q"] != 1783 + 2864 + 6993 + 10718 + 14323


def test_alnum_ticker_code_used_in_url(monkeypatch):
    seen = []
    _patch_finance(monkeypatch, seen=seen)
    earnings.get_earnings_growth("0011A0.KQ")
    assert any("/stock/0011A0/finance/annual" in u for u in seen), seen
    assert any("/stock/0011A0/finance/quarter" in u for u in seen), seen


def test_fetch_failure_is_unknown_not_crash(monkeypatch):
    _patch_finance(monkeypatch, error=RuntimeError("boom"))
    r = earnings.get_earnings_growth("005930.KS")
    assert r["ok"] is False and r["verdict"] == "unknown"
    assert "조회 실패" in r["reasons"][0]


def test_empty_payload_reports_missing_table(monkeypatch):
    _patch_finance(monkeypatch, fixture={"annual": {}, "quarter": {}})
    r = earnings.get_earnings_growth("005930.KS")
    assert r["ok"] is False and "실적 표 없음" in r["reasons"][0]


def test_old_pc_parser_gone():
    assert not hasattr(earnings, "_parse_kr_table")
    assert not hasattr(earnings, "_KR_MAIN_URL")


# ---------------------------------------------------------------------------
# 2) 실패 캐시는 30분(성공은 6시간)
# ---------------------------------------------------------------------------

def test_failed_lookup_cached_shorter(monkeypatch):
    calls = []

    def fake_growth(ticker):
        calls.append(ticker)
        return {"ok": False, "verdict": "unknown", "reasons": ["조회 실패"]}

    monkeypatch.setattr(app, "_earnings_cache", {})
    monkeypatch.setattr(app.earnings_mod, "get_earnings_growth", fake_growth)
    now = [1000.0]
    monkeypatch.setattr(app.time, "time", lambda: now[0])
    app._get_earnings_cached("005930.KS")
    now[0] += app._EARNINGS_FAIL_TTL - 10       # 30분 직전 — 캐시 사용
    app._get_earnings_cached("005930.KS")
    assert len(calls) == 1
    now[0] += 20                                # 30분 경과 — 재조회
    app._get_earnings_cached("005930.KS")
    assert len(calls) == 2


def test_successful_lookup_keeps_6h_cache(monkeypatch):
    calls = []

    def fake_growth(ticker):
        calls.append(ticker)
        return {"ok": True, "verdict": "pass"}

    monkeypatch.setattr(app, "_earnings_cache", {})
    monkeypatch.setattr(app.earnings_mod, "get_earnings_growth", fake_growth)
    now = [1000.0]
    monkeypatch.setattr(app.time, "time", lambda: now[0])
    app._get_earnings_cached("005930.KS")
    now[0] += app._EARNINGS_FAIL_TTL + 60       # 실패 TTL은 지났지만 성공이라 유지
    app._get_earnings_cached("005930.KS")
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# 3) 대장 3상태
# ---------------------------------------------------------------------------

def _leader_env(monkeypatch, eps_by_ticker):
    import pandas as pd
    # 200일선 통과 데이터(종가가 평균보다 높게) — 후보 전원 MA200 통과
    df = pd.DataFrame({"Close": [100.0] * (app.LEADER_MA200_MIN_BARS - 1) + [200.0]})
    data = {t: df for t in eps_by_ticker}

    async def fake_safe(ticker):
        return eps_by_ticker[ticker]

    monkeypatch.setattr(app, "_get_earnings_safe", fake_safe)
    return data


def test_leader_unknown_eps_is_none_not_false(monkeypatch):
    eps = {"005930.KS": {"ok": False, "verdict": "unknown"},          # 조회 실패
           "000660.KS": {"ok": True, "eps_sum_last4q": 500.0},        # 통과
           "005380.KS": {"ok": True, "eps_sum_last4q": -10.0}}        # 확인된 적자
    data = _leader_env(monkeypatch, eps)
    by_sector = {"반도체|KR": {"leader_candidates": ["005930.KS", "000660.KS", "005380.KS"]}}
    stats = asyncio.run(app._refine_sector_leaders(by_sector, data))
    leaders = {l["ticker"]: l["qualifies"] for l in by_sector["반도체|KR"]["leaders"]}
    assert leaders["000660.KS"] is True
    assert leaders["005930.KS"] is None, "실적 판정 불가를 '탈락'으로 뭉개면 안 됨"
    assert leaders["005380.KS"] is False
    assert stats == {"kr_checked": 3, "kr_ok": 2}


def test_leader_order_qualified_then_unknown_then_failed(monkeypatch):
    eps = {"A.KS": {"ok": False}, "B.KS": {"ok": True, "eps_sum_last4q": 1.0},
           "C.KS": {"ok": True, "eps_sum_last4q": -1.0}}
    data = _leader_env(monkeypatch, eps)
    by_sector = {"s|KR": {"leader_candidates": ["A.KS", "C.KS", "B.KS"]}}
    asyncio.run(app._refine_sector_leaders(by_sector, data))
    assert [l["ticker"] for l in by_sector["s|KR"]["leaders"]] == ["B.KS", "A.KS", "C.KS"]


# ---------------------------------------------------------------------------
# 4) TIMING + 경고 + 스키마
# ---------------------------------------------------------------------------

def _patch_refine(monkeypatch, checked, ok):
    async def fake_refine(by_sector, data):
        return {"kr_checked": checked, "kr_ok": ok}
    monkeypatch.setattr(app, "_refine_sector_leaders", fake_refine)


def test_timing_earnings_failed_warns(mocked_env, monkeypatch, capsys):
    _patch_refine(monkeypatch, checked=7, ok=0)
    bundle = asyncio.run(app._fetch_market_data("kr", wait_for_fresh=True))
    tm = bundle["timing"]
    assert tm["kr_earnings_source"] == "failed"
    assert tm["kr_earnings_checked"] == 7 and tm["kr_earnings_ok"] == 0
    assert "KR 실적 조회 7건 전부 실패" in capsys.readouterr().out


def test_timing_earnings_ok(mocked_env, monkeypatch):
    _patch_refine(monkeypatch, checked=7, ok=7)
    bundle = asyncio.run(app._fetch_market_data("kr", wait_for_fresh=True))
    assert bundle["timing"]["kr_earnings_source"] == "mobile_api"


def test_timing_earnings_unchecked(mocked_env, monkeypatch):
    _patch_refine(monkeypatch, checked=0, ok=0)
    bundle = asyncio.run(app._fetch_market_data("kr", wait_for_fresh=True))
    assert bundle["timing"]["kr_earnings_source"] == "unchecked"


def test_timing_us_has_no_kr_earnings(mocked_env, monkeypatch):
    _patch_refine(monkeypatch, checked=3, ok=0)
    bundle = asyncio.run(app._fetch_market_data("us", wait_for_fresh=True))
    assert bundle["timing"]["kr_earnings_source"] is None
    assert bundle["timing"]["kr_earnings_checked"] == 0


def test_all_merge_propagates_kr_earnings(mocked_env, monkeypatch):
    _patch_refine(monkeypatch, checked=5, ok=0)
    bundle = asyncio.run(app._fetch_market_data("all", wait_for_fresh=True))
    assert bundle["timing"]["kr_earnings_source"] == "failed"
    assert bundle["timing"]["kr_earnings_checked"] == 5


def test_timing_schema_keys_include_earnings_fields():
    for k in ("kr_earnings_source", "kr_earnings_ok", "kr_earnings_checked"):
        assert k in app._TIMING_SCHEMA_KEYS


# ---------------------------------------------------------------------------
# 5) 화면 — 텍스트 추출 + Node 실행
# ---------------------------------------------------------------------------

def _extract_function(name: str) -> str:
    text = INDEX_PATH.read_text(encoding="utf-8")
    start = text.find(f"function {name}(")
    assert start != -1, f"`function {name}(`를 못 찾음"
    brace_start = text.index("{", text.index(")", start))
    depth = 0
    for i in range(brace_start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise AssertionError("닫는 중괄호 못 찾음")


def _run_js(src: str, call: str):
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — 실행 테스트 스킵")
    res = subprocess.run(["node", "-e", f"{src}\nconsole.log(JSON.stringify({call}));"],
                         capture_output=True, text=True, timeout=15)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_kr_earnings_badge_only_on_failed():
    src = _extract_function("_krEarningsBadgeHtml")
    assert "KR 실적 조회 실패" in _run_js(src, '_krEarningsBadgeHtml({kr_earnings_source:"failed"})')
    assert _run_js(src, '_krEarningsBadgeHtml({kr_earnings_source:"mobile_api"})') == ""
    assert _run_js(src, '_krEarningsBadgeHtml({kr_earnings_source:"unchecked"})') == ""
    assert _run_js(src, "_krEarningsBadgeHtml(null)") == ""


def test_earnings_tab_unknown_count():
    src = _extract_function("_krEarningsUnknownHtml")
    all_failed = _run_js(src, "_krEarningsUnknownHtml({kr_earnings_checked:10, kr_earnings_unknown:10})")
    assert "10/10" in all_failed and "⚠️" in all_failed
    partial = _run_js(src, "_krEarningsUnknownHtml({kr_earnings_checked:10, kr_earnings_unknown:2})")
    assert "2/10" in partial and "⚠️" not in partial
    assert _run_js(src, "_krEarningsUnknownHtml({kr_earnings_checked:10, kr_earnings_unknown:0})") == ""
    assert _run_js(src, "_krEarningsUnknownHtml(null)") == ""


def test_sector_leader_tri_state_rendering():
    src = "function tvUrl(t){return 'x';}\n" + _extract_function("_sectorLeaderCompactHtml")
    ok = _run_js(src, '_sectorLeaderCompactHtml([{ticker:"005930.KS",name:"삼성전자",qualifies:true}],"KR")')
    unknown = _run_js(src, '_sectorLeaderCompactHtml([{ticker:"005930.KS",name:"삼성전자",qualifies:null}],"KR")')
    bad = _run_js(src, '_sectorLeaderCompactHtml([{ticker:"005930.KS",name:"삼성전자",qualifies:false}],"KR")')
    assert "⚠️" not in ok and "❔" not in ok
    assert "❔" in unknown and "⚠️" not in unknown and "판정 불가" in unknown
    assert "⚠️" in bad and "진짜 대장 아님" in bad


def test_status_bar_calls_new_badges():
    text = INDEX_PATH.read_text(encoding="utf-8")
    assert "${_krEarningsBadgeHtml(_tm)}" in text
    assert "_krEarningsUnknownHtml(data.diag)" in text


# ---------------------------------------------------------------------------
# 6) EOD — v2 캐시 재사용 + 소스 명시
# ---------------------------------------------------------------------------

def test_eod_uses_turnover_v2_cache(mocked_env, monkeypatch):
    from test_fetch_market_data_all_merge import FIXTURE_KR
    monkeypatch.setattr(app, "load_kr_dynamic", lambda: {t: t for t in FIXTURE_KR})
    monkeypatch.setattr(app, "_eod_cache", {})
    asyncio.run(app._fetch_market_data("kr", wait_for_fresh=True))   # 번들 워밍(콜드면 pending 응답)
    r = asyncio.run(app.eod_summary())
    body = json.loads(r.body)
    assert body["top_value_source"] == "turnover_v2_cache"
    assert body["top_value_count"] == len(body["top_value"]) > 0


def test_eod_reports_unavailable_source(mocked_env, monkeypatch):
    monkeypatch.setattr(app, "load_kr_dynamic", lambda: {})
    monkeypatch.setattr(app, "_eod_cache", {})
    asyncio.run(app._fetch_market_data("kr", wait_for_fresh=True))
    body = json.loads(asyncio.run(app.eod_summary()).body)
    assert body["top_value_source"] == "unavailable"
    assert body["top_value"] == [] and body["top_value_count"] == 0


# ---------------------------------------------------------------------------
# 7) PC 페이지 의존 0건
# ---------------------------------------------------------------------------

def test_no_pc_page_scrapers_left():
    for name in ("fetch_top_value", "fetch_top_marketcap", "_parse_quant_page",
                 "_parse_marketcap_rows", "_QUANT_URL", "_MARKETSUM_URL"):
        assert not hasattr(naver_kr, name), f"죽은 PC 스크레이퍼 잔존: {name}"


def test_no_finance_naver_html_pages_in_source():
    """finance.naver.com 의존은 JSON API(api.finance.naver.com/siseJson)와
    Referer 헤더만 남아야 한다 — PC HTML 페이지(sise_*.naver/item/main.naver)를
    새로 긁기 시작하면 같은 개편에 또 조용히 죽는다(CLAUDE.md naver 의존 목록)."""
    bad = []
    for path in ("app.py", "naver_kr.py", "universe.py", "earnings.py", "fundamentals.py"):
        for i, line in enumerate((ROOT / path).read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#") or '"""' in line:
                continue
            # URL 리터럴만 — 주석·docstring의 서술, JSON API인
            # api.finance.naver.com/siseJson, Referer 헤더(스크래핑 아님)는 제외.
            if "Referer" in line:
                continue
            if '"https://finance.naver.com/' in line:
                bad.append(f"{path}:{i}")
    assert not bad, f"PC HTML 페이지 의존 잔존: {bad}"
