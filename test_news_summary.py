"""v5.346 카드 "🗞 왜?" — news_summary.get_summary 가드·캐시·에러 구분 고정. 외부 API(뉴스·Anthropic)는 전부 mock.

사용자 지시: 캐시 히트/미스, 강제 갱신, 한도 초과, 키 미설정 각각 테스트. "사보타주: 캐시 키에서 날짜를 빼면 히트/미스 테스트가 FAIL해야 한다."
사보타주 확인(2026-10-10, FAIL 확인 후 원복): cache_key가 날짜를 빼고 종목만 쓰면 → test_next_day_is_miss FAIL.
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_call_guard  # noqa: E402
import news_summary as ns  # noqa: E402

FAKE_KEY = "FAKE-TEST-KEY-must-not-leak-0000"   # 키 형식(접두사)을 흉내 내지 않는다 — 비밀 스캐너 오탐 방지
GOOD = "1. 무슨 일: 10-08 잠정실적 발표(영업이익 급증)\n2. 성격: 실적·구조 변화\n3. 근거: 서울경제 10-08"
NEWS = [{"date": "2026-10-08 07:44", "source": "서울경제", "title": "잠정실적", "body": "영업이익 급증"}]
DISC = [{"date": "2026-10-08 07:44", "title": "연결재무제표기준영업(잠정)실적(공정공시)"}]


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(ns, "CACHE_PATH", str(tmp_path / "news_summary_cache.json"))
    monkeypatch.setattr(api_call_guard, "STATE_PATH", str(tmp_path / "api_call_guard_state.json"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.delenv("NEWS_SUMMARY_DAILY_LIMIT", raising=False)
    monkeypatch.delenv("CLAUDE_MODEL_NEWS", raising=False)
    day = {"v": "2026-10-10"}
    monkeypatch.setattr(ns, "_today", lambda: day["v"])
    calls = {"model": 0, "fetch": 0, "system": None, "user": None, "model_id": None}

    def fake_news(code, now=None):
        calls["fetch"] += 1
        return list(NEWS)
    monkeypatch.setattr(ns, "fetch_kr_news", fake_news)
    monkeypatch.setattr(ns, "fetch_kr_disclosures", lambda code, now=None: list(DISC))
    monkeypatch.setattr(ns, "fetch_us_news", fake_news)
    out = {"text": GOOD, "exc": None}

    def fake_model(system, user, model, api_key):
        calls["model"] += 1
        calls.update(system=system, user=user, model_id=model)
        assert api_key == FAKE_KEY
        if out["exc"]:
            raise out["exc"]
        return out["text"], model, 1234, 56
    monkeypatch.setattr(ns, "_call_model", fake_model)
    return {"day": day, "calls": calls, "out": out, "tmp": tmp_path}


def test_cache_miss_then_hit_same_day(env):
    r1 = ns.get_summary("005930.KS", None, "삼성전자")
    assert r1["status"] == "ok" and env["calls"]["model"] == 1
    assert r1["lines"][1] == "2. 성격: 실적·구조 변화" and r1["tokens_in"] == 1234 and r1["tokens_out"] == 56
    assert r1["model"] == ns.DEFAULT_MODEL and env["calls"]["model_id"] == "claude-haiku-4-5-20251001"
    r2 = ns.get_summary("005930", "KR", "삼성전자")                 # 접미사 없이 같은 종목 → 캐시
    assert r2["status"] == "cached" and env["calls"]["model"] == 1 and r2["lines"] == r1["lines"]
    saved = json.loads((env["tmp"] / "news_summary_cache.json").read_text(encoding="utf-8"))["entries"]["KR:005930|2026-10-10"]
    assert saved["tokens_in"] == 1234 and saved["tokens_out"] == 56 and saved["model"] == ns.DEFAULT_MODEL


def test_next_day_is_miss(env):
    """캐시 단위 = 종목 + KST 날짜. 다음 날은 다시 생성(사보타주: 키에서 날짜를 빼면 여기서 FAIL)."""
    assert ns.get_summary("005930.KS", None)["status"] == "ok"
    env["day"]["v"] = "2026-10-11"
    assert ns.get_summary("005930.KS", None)["status"] == "ok" and env["calls"]["model"] == 2
    assert ns.get_summary("005930.KS", None)["status"] == "cached" and env["calls"]["model"] == 2


def test_force_refresh_cooldown_then_regenerate(env, monkeypatch):
    assert ns.get_summary("AAPL", "US")["status"] == "ok"
    r = ns.get_summary("AAPL", "US", force=True)
    assert r["status"] == "cooldown" and 0 < r["retry_after_sec"] <= 121 and env["calls"]["model"] == 1
    t0 = ns.time.time()
    monkeypatch.setattr(ns.time, "time", lambda: t0 + ns.FORCE_COOLDOWN_SEC + 1)
    r = ns.get_summary("AAPL", "US", force=True)
    assert r["status"] == "ok" and env["calls"]["model"] == 2


def test_daily_limit_separate_bucket(env, monkeypatch):
    monkeypatch.setenv("NEWS_SUMMARY_DAILY_LIMIT", "1")
    assert ns.get_summary("005930.KS", None)["status"] == "ok"
    r = ns.get_summary("000660.KS", None)
    assert r["status"] == "limit" and r["message"] == "오늘 한도 초과" and env["calls"]["model"] == 1
    st = api_call_guard.status()
    assert st["count"] == 0 and st["buckets"]["news_summary"] == {"count": 1, "limit": 1}   # 공유 20회는 안 건드린다
    assert api_call_guard.check_and_count("money_flow_report")[0] is True                  # 기존 모듈은 그대로 통과
    assert api_call_guard.status()["count"] == 1


def test_default_limit_50_and_shared_limit_unchanged():
    assert api_call_guard.SEPARATE_BUCKETS["news_summary"] == ("NEWS_SUMMARY_DAILY_LIMIT", 50)
    assert api_call_guard.DAILY_LIMIT == 20


def test_no_key(env, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    r = ns.get_summary("005930.KS", None)
    assert r["status"] == "no_key" and r["message"] == "API 키 미설정"
    assert env["calls"]["model"] == 0 and env["calls"]["fetch"] == 0


def test_news_error_is_separate_and_not_cached(env, monkeypatch):
    def boom(code, now=None):
        raise ns.NewsFetchError("naver 뉴스 응답 형식이 목록이 아님(소스 개편 의심)")
    monkeypatch.setattr(ns, "fetch_kr_news", boom)
    r = ns.get_summary("005930.KS", None)
    assert r["status"] == "news_error" and "뉴스 수집 실패" in r["message"] and "소스 개편" in r["message"]
    assert env["calls"]["model"] == 0 and not (env["tmp"] / "news_summary_cache.json").exists()


def test_summary_error_not_swallowed_and_not_cached(env):
    env["out"]["exc"] = RuntimeError("overloaded")
    r = ns.get_summary("005930.KS", None)
    assert r["status"] == "summary_error" and "RuntimeError: overloaded" in r["message"]
    env["out"]["exc"] = None
    assert ns.get_summary("005930.KS", None)["status"] == "ok" and env["calls"]["model"] == 2      # 실패는 캐시하지 않음


def test_format_error_is_summary_error_with_raw(env):
    env["out"]["text"] = "원인은 실적입니다. 매수 추천."
    r = ns.get_summary("005930.KS", None)
    assert r["status"] == "summary_error" and r["raw"] == "원인은 실적입니다. 매수 추천." and r["tokens_in"] == 1234


def test_us_line3_gets_us_disclosure_note_kr_does_not(env):
    us = ns.get_summary("AAPL", "US")
    assert us["lines"][2].endswith("US 공시 미확인") and "[공시] US 공시는 입력에 없음" in env["calls"]["user"]
    kr = ns.get_summary("005930.KS", None)
    assert "US 공시 미확인" not in kr["lines"][2] and "[최근 7일 공시 1건]" in env["calls"]["user"]


def test_no_news_skips_api(env, monkeypatch):
    monkeypatch.setattr(ns, "fetch_kr_news", lambda code, now=None: [])
    monkeypatch.setattr(ns, "fetch_kr_disclosures", lambda code, now=None: [])
    r = ns.get_summary("005930.KS", None)
    assert r["status"] == "no_news" and env["calls"]["model"] == 0 and "뉴스상 원인 불명" in r["lines"][2]
    assert ns.get_summary("005930.KS", None)["status"] == "cached"


def test_in_progress_lock(env):
    lock = ns._key_lock("KR:005930")
    lock.acquire()
    try:
        assert ns.get_summary("005930.KS", None)["status"] == "in_progress" and env["calls"]["model"] == 0
    finally:
        lock.release()


def test_key_never_written_or_logged(env, capsys):
    ns.get_summary("005930.KS", None)
    env["out"]["exc"] = RuntimeError("boom")
    ns.get_summary("AAPL", "US")
    blob = (env["tmp"] / "news_summary_cache.json").read_text(encoding="utf-8") + capsys.readouterr().out
    assert FAKE_KEY not in blob and "FAKE-TEST-KEY" not in blob


def test_prompt_rules():
    for s in ("매수·매도·보유 의견", "추측", "정확히 3줄", "뉴스상 원인 불명", "일회성 이벤트", "실적·구조 변화", "희석"):
        assert s in ns.SYSTEM_PROMPT
    assert ns.MAX_TOKENS == 300 and ns.NEWS_MAX == 15 and ns.DISC_MAX == 10 and ns.NEWS_DAYS == 7 and ns.FORCE_COOLDOWN_SEC == 120


def test_price_rows_hand():
    idx = pd.bdate_range("2026-09-01", periods=25)
    df = pd.DataFrame({"Close": [100.0] * 24 + [110.0], "Volume": [10.0] * 24 + [30.0]}, index=idx)
    rows = ns.price_rows(df)
    assert len(rows) == 5 and rows[-1]["chg_pct"] == 10.0
    assert rows[-1]["turnover_mult"] == round(110 * 30 / (100 * 10), 2)            # 직전 20일 평균 거래대금 1,000 대비 3,300
    assert ns.price_rows(None) == []


class _Resp:
    def __init__(self, status=200, payload=None, content=b""):
        self.status_code, self._p, self.content = status, payload, content

    def json(self):
        if isinstance(self._p, Exception):
            raise self._p
        return self._p


def test_fetch_kr_news_parses_and_filters_7_days(monkeypatch):
    now = ns.datetime(2026, 10, 10, 12, 0, tzinfo=ns.KST)
    page1 = [{"total": 1, "items": [{"datetime": "202610100701", "officeName": "서울경제", "title": "t1", "titleFull": "제목1 &amp; 공시", "body": "&lt;기자&gt; " + "본문" * 200},
                                    {"datetime": "202610010900", "officeName": "옛기사", "title": "old", "body": ""}]}]
    monkeypatch.setattr(ns.requests, "get", lambda *a, **k: _Resp(200, page1))
    out = ns.fetch_kr_news("005930", now)
    assert [o["title"] for o in out] == ["제목1 & 공시"] and out[0]["body"].startswith("<기자> 본문") and len(out[0]["body"]) == ns.BODY_CHARS
    monkeypatch.setattr(ns.requests, "get", lambda *a, **k: _Resp(200, ValueError("html")))
    with pytest.raises(ns.NewsFetchError):
        ns.fetch_kr_news("005930", now)
    monkeypatch.setattr(ns.requests, "get", lambda *a, **k: _Resp(503, []))
    with pytest.raises(ns.NewsFetchError):
        ns.fetch_kr_disclosures("005930", now)


def test_fetch_us_rss_parses(monkeypatch):
    now = ns.datetime(2026, 10, 10, 12, 0, tzinfo=ns.KST)
    xml = b"""<?xml version="1.0"?><rss><channel>
      <item><title>Apple news</title><description>desc</description><pubDate>Fri, 09 Oct 2026 21:38:48 +0000</pubDate></item>
      <item><title>Old</title><description>x</description><pubDate>Thu, 01 Oct 2026 10:00:00 +0000</pubDate></item>
    </channel></rss>"""
    monkeypatch.setattr(ns.requests, "get", lambda *a, **k: _Resp(200, None, xml))
    out = ns.fetch_us_news("AAPL", now)
    assert [o["title"] for o in out] == ["Apple news"] and out[0]["date"] == "2026-10-10 06:38"


def test_route_passes_market_bars_and_strips_ts(monkeypatch):
    import app
    seen = {}

    def fake_get(ticker, market, name, bars, force):
        seen.update(ticker=ticker, market=market, name=name, bars=bars, force=force)
        return {"status": "cached", "lines": ["1.", "2.", "3."], "generated_ts": 1.0}
    monkeypatch.setattr(app.news_summary, "get_summary", fake_get)
    idx = pd.bdate_range("2026-09-01", periods=25)
    df = pd.DataFrame({"Close": [100.0] * 25, "Volume": [1.0] * 25}, index=idx)
    fetched = []
    monkeypatch.setattr(app, "_fetch", lambda t: fetched.append(t) or df)

    class Req:
        async def json(self):
            return {"ticker": "005930", "market": "KR", "name": "삼성전자", "force": True}
    res = asyncio.run(app.news_summary_route(Req()))
    body = json.loads(res.body)
    assert body == {"status": "cached", "lines": ["1.", "2.", "3."]}
    assert seen["market"] == "KR" and seen["force"] is True and len(seen["bars"]) == 5 and fetched == ["005930.KS"]
