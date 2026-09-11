"""KR 시총 1000억 필터 복구 + fail-open 가시화 (v5.251, 사용자 지시).

사고: naver_kr.fetch_high_marketcap_allowed()가 finance.naver.com
sise_market_sum.naver(PC 페이지)를 정규식으로 긁었는데, 그 페이지가
2026-09-10 장마감 전후 Next.js SPA로 개편돼 0건 → app._get_mcap_allowed()
fail-open → 시총 1000억 필터가 아무 로그 없이 꺼진 채 운영됐다(v5.246이
유니버스 쪽만 고치고 이 함수는 놓침).

검증:
1. 새 구현(m.stock.naver.com marketValue API)이 문턱 이상만, 알파벳 혼용
   코드까지 담는지 / 한 시장이라도 불완전하면 부분 목록 대신 빈 집합인지
   (화이트리스트라 부분 목록 = 조용한 과잉 배제).
2. TIMING에 kr_mcap_filter_source/allowed_count/dropped_count가 매 스캔
   찍히는지(적용/미적용 둘 다, market=all 병합 포함), fail-open이면 경고
   로그가 찍히는지, _TIMING_SCHEMA_KEYS 동기화.
3. _ensure_mcap_allowed()가 0건일 때 경고를 남기고 캐시를 안 채우는지.
4. 화면 배지(_krMcapFilterBadgeHtml) — 텍스트 추출 + Node 실행.
5. /api/calendar immediate_pipeline_health 노출.
"""
import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import requests

import app
import naver_kr
from test_fetch_market_data_all_merge import FIXTURE_KR, mocked_env  # noqa: F401

ROOT = Path(__file__).resolve().parent
INDEX_PATH = ROOT / "static" / "index.html"


# ---------------------------------------------------------------------------
# 1) naver_kr.fetch_high_marketcap_allowed — 가짜 모바일 API 응답
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _fake_get_factory(pages: dict, fail: set = frozenset()):
    """pages: {(market, page): [ (itemCode, marketValue문자열), ... ]},
    totalCount는 시장별 전체 행 수로 자동 계산. fail: 예외를 낼 (market, page)."""
    totals = {}
    for (mk, _p), rows in pages.items():
        totals[mk] = totals.get(mk, 0) + len(rows)

    def fake_get(url, params=None, headers=None, timeout=None):
        mk = "KOSPI" if url.endswith("KOSPI") else "KOSDAQ"
        key = (mk, params["page"])
        if key in fail:
            raise requests.ConnectionError("boom")
        rows = pages.get(key, [])
        return _Resp({"totalCount": totals.get(mk, 0),
                      "stocks": [{"itemCode": c, "marketValue": v} for c, v in rows]})
    return fake_get


def test_mobile_api_threshold_and_alnum_codes(monkeypatch):
    pages = {
        ("KOSPI", 1): [("005930", "15,171,093"), ("0126Z0", "84,229"), ("03473K", "2,231"),
                        ("123456", "1,000"), ("999999", "999")],
        ("KOSDAQ", 1): [("0011A0", "1,268"), ("0001A0", "2,262"), ("111111", "500")],
    }
    monkeypatch.setattr(naver_kr.requests, "get", _fake_get_factory(pages))
    monkeypatch.setattr(naver_kr._time, "sleep", lambda s: None)
    allowed, stats = naver_kr.fetch_high_marketcap_allowed(1000, page_size=100)
    assert allowed == {"005930.KS", "0126Z0.KS", "03473K.KS", "123456.KS",
                       "0011A0.KQ", "0001A0.KQ"}, allowed   # 1000억 경계는 포함, 999/500 제외
    assert stats["incomplete"] is False
    assert stats["n_allowed"] == 6
    assert stats["kospi_fetched"] == 5 and stats["kosdaq_fetched"] == 3


def test_incomplete_market_returns_empty_set_not_partial(monkeypatch):
    """KOSDAQ 페이지가 연속 실패 → 부분 허용목록(KOSPI만)을 돌려주면 KOSDAQ
    대형주가 전부 '시총 미달'로 빠진다. 빈 집합(→ 호출부 fail-open + 경고)이어야."""
    pages = {("KOSPI", 1): [("005930", "15,171,093")],
             ("KOSDAQ", 1): [("0011A0", "1,268")], ("KOSDAQ", 2): [("222222", "5,000")]}
    fail = {("KOSDAQ", 1), ("KOSDAQ", 2)}
    monkeypatch.setattr(naver_kr.requests, "get", _fake_get_factory(pages, fail))
    monkeypatch.setattr(naver_kr._time, "sleep", lambda s: None)
    allowed, stats = naver_kr.fetch_high_marketcap_allowed(1000, page_size=1)
    assert allowed == set()
    assert stats["incomplete"] is True
    assert stats["n_allowed"] == 0
    assert stats["errors"], "실패 사유가 stats에 안 남음"


def test_old_pc_page_scraper_is_gone():
    """죽은 PC 페이지 파서가 다시 쓰이지 않게 — 숫자 6자리 전용 정규식이라
    알파벳 혼용 코드도 원천적으로 못 담았다."""
    assert not hasattr(naver_kr, "_parse_marketcap_rows")
    assert not hasattr(naver_kr, "_ROW_CODE_RE")


# ---------------------------------------------------------------------------
# 2) TIMING 필드 — 적용/미적용, market=all 병합, 경고 로그, 스키마
# ---------------------------------------------------------------------------

def test_timing_fail_open_when_no_allowlist(mocked_env, capsys):
    # mocked_env가 _mcap_allowed_cache={}로 초기화 → 허용목록 없음
    bundle = asyncio.run(app._fetch_market_data("kr", wait_for_fresh=True))
    tm = bundle["timing"]
    assert tm["kr_mcap_filter_source"] == "fail_open"
    assert tm["kr_mcap_allowed_count"] == 0
    assert tm["kr_mcap_dropped_count"] == 0
    assert set(bundle["universe"]) == set(FIXTURE_KR)   # fail-open — 전부 스캔
    assert "시총 필터 없이 스캔(fail-open)" in capsys.readouterr().out


def test_timing_applied_drops_small_caps(mocked_env, monkeypatch):
    keep = set(FIXTURE_KR[:-1])
    dropped = FIXTURE_KR[-1]
    monkeypatch.setattr(app, "_mcap_allowed_cache", {"slotkey": app._kr_cache_slot(), "tickers": keep})
    bundle = asyncio.run(app._fetch_market_data("kr", wait_for_fresh=True))
    tm = bundle["timing"]
    assert tm["kr_mcap_filter_source"] == "mobile_api"
    assert tm["kr_mcap_allowed_count"] == len(keep)
    assert tm["kr_mcap_dropped_count"] == 1
    assert dropped not in bundle["universe"]


def test_timing_us_has_no_mcap_info(mocked_env):
    bundle = asyncio.run(app._fetch_market_data("us", wait_for_fresh=True))
    assert bundle["timing"]["kr_mcap_filter_source"] is None
    assert bundle["timing"]["kr_mcap_allowed_count"] == 0
    assert bundle["timing"]["kr_mcap_dropped_count"] == 0


def test_all_merge_propagates_mcap_source(mocked_env):
    bundle = asyncio.run(app._fetch_market_data("all", wait_for_fresh=True))
    assert bundle["timing"]["kr_mcap_filter_source"] == "fail_open"


def test_timing_schema_keys_include_mcap_fields():
    for k in ("kr_mcap_filter_source", "kr_mcap_allowed_count", "kr_mcap_dropped_count"):
        assert k in app._TIMING_SCHEMA_KEYS


# ---------------------------------------------------------------------------
# 3) _ensure_mcap_allowed — 0건이면 경고 + 캐시 안 채움
# ---------------------------------------------------------------------------

def test_ensure_mcap_allowed_empty_warns_and_keeps_fail_open(monkeypatch, capsys):
    monkeypatch.setattr(app, "_mcap_allowed_cache", {})
    monkeypatch.setattr(app, "_mcap_fetch_in_progress", False)
    monkeypatch.setattr(app.naver_kr, "fetch_high_marketcap_allowed",
                        lambda min_eok: (set(), {"incomplete": True, "n_allowed": 0, "errors": ["x"]}))
    asyncio.run(app._ensure_mcap_allowed())
    assert app._mcap_allowed_cache == {}
    assert app._kr_mcap_filter_info()["kr_mcap_filter_source"] == "fail_open"
    assert "필터 미적용(fail-open)" in capsys.readouterr().out


def test_ensure_mcap_allowed_success_fills_cache(monkeypatch):
    monkeypatch.setattr(app, "_mcap_allowed_cache", {})
    monkeypatch.setattr(app, "_mcap_fetch_in_progress", False)
    monkeypatch.setattr(app.naver_kr, "fetch_high_marketcap_allowed",
                        lambda min_eok: ({"005930.KS", "0011A0.KQ"}, {"incomplete": False, "n_allowed": 2}))
    asyncio.run(app._ensure_mcap_allowed())
    info = app._kr_mcap_filter_info()
    assert info == {"kr_mcap_filter_source": "mobile_api", "kr_mcap_allowed_count": 2}


# ---------------------------------------------------------------------------
# 4) 화면 배지 — 텍스트 추출 + Node 실행
# ---------------------------------------------------------------------------

def _extract_function(name: str) -> str:
    text = INDEX_PATH.read_text(encoding="utf-8")
    start = text.find(f"function {name}(")
    assert start != -1, f"static/index.html에서 `function {name}(`를 못 찾음"
    brace_start = text.index("{", text.index(")", start))
    depth = 0
    for i in range(brace_start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise AssertionError(f"`{name}` 닫는 중괄호 못 찾음")


def _badge(tm):
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — 실행 테스트 스킵")
    src = _extract_function("_krMcapFilterBadgeHtml")
    res = subprocess.run(["node", "-e", f"{src}\nconsole.log(JSON.stringify(_krMcapFilterBadgeHtml({json.dumps(tm)})));"],
                         capture_output=True, text=True, timeout=15)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_badge_shown_only_on_fail_open():
    html = _badge({"kr_mcap_filter_source": "fail_open"})
    assert "시총 필터 미적용" in html and "⚠️" in html
    assert _badge({"kr_mcap_filter_source": "mobile_api"}) == ""
    assert _badge({"kr_mcap_filter_source": None}) == ""
    assert _badge(None) == ""


def test_status_bar_template_calls_mcap_badge():
    text = INDEX_PATH.read_text(encoding="utf-8")
    assert "${_krMcapFilterBadgeHtml(_tm)}" in text


# ---------------------------------------------------------------------------
# 5) /api/calendar immediate_pipeline_health
# ---------------------------------------------------------------------------

def test_calendar_pipeline_health_exposes_mcap_state(mocked_env):
    r = asyncio.run(app.get_calendar())
    health = json.loads(r.body)["immediate_pipeline_health"]
    assert health["kr_mcap_filter_source"] == "fail_open"
    assert health["kr_mcap_allowed_count"] == 0
