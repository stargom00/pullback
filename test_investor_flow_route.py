"""v5.275 — /api/abc의 수급 배선: 범위·상한·캐시·방어 3종."""
import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app  # noqa: E402


@pytest.fixture(autouse=True)
def clean():
    app._FLOW_CACHE.clear()
    yield
    app._FLOW_CACHE.clear()


def _stub(monkeypatch, calls):
    def fake(ticker, *a, **k):
        calls.append(ticker)
        return [{"bizdate": "20260918", "organPureBuyQuant": "+1",
                 "foreignerPureBuyQuant": "-1"}] * 5
    monkeypatch.setattr(app.naver_kr, "fetch_investor_trend", fake)


def test_cap_limits_new_fetches(monkeypatch):
    """상한을 넘긴 몫은 **조회하지 않고 센다** — 조용히 빠지면 '수급 없음'과
    '안 본 종목'이 화면에서 같아 보인다."""
    calls = []
    _stub(monkeypatch, calls)
    monkeypatch.setattr(app, "_FLOW_MAX_PER_SCAN", 3)
    tickers = [f"{i:06d}.KQ" for i in range(10)]
    asyncio.run(app._flow_fill(tickers))
    assert len(calls) == 3, len(calls)
    assert app._flow_source["capped"] == 7, app._flow_source


def test_cache_prevents_refetch(monkeypatch):
    calls = []
    _stub(monkeypatch, calls)
    asyncio.run(app._flow_fill(["005930.KS"]))
    asyncio.run(app._flow_fill(["005930.KS"]))
    assert len(calls) == 1, calls
    assert app._flow_source["cached"] == 1


def test_cache_expires_after_ttl(monkeypatch):
    calls = []
    _stub(monkeypatch, calls)
    asyncio.run(app._flow_fill(["005930.KS"]))
    ts, val = app._FLOW_CACHE["005930.KS"]
    app._FLOW_CACHE["005930.KS"] = (ts - app._FLOW_TTL - 1, val)
    asyncio.run(app._flow_fill(["005930.KS"]))
    assert len(calls) == 2, calls


def test_ttl_is_four_hours():
    assert app._FLOW_TTL == 4 * 3600


def test_us_tickers_are_never_fetched(monkeypatch):
    """naver 소스라 US는 대상이 아니다."""
    calls = []
    _stub(monkeypatch, calls)
    asyncio.run(app._flow_fill(["AAPL", "005930.KS"]))
    assert calls == ["005930.KS"], calls


def test_status_field_records_each_outcome(monkeypatch):
    def mixed(ticker, *a, **k):
        if ticker.startswith("1"):
            return None                 # 조회 실패
        if ticker.startswith("2"):
            return []                   # 200 OK + 0건
        return [{"bizdate": "20260918", "organPureBuyQuant": "+1",
                 "foreignerPureBuyQuant": "+1"}]
    monkeypatch.setattr(app.naver_kr, "fetch_investor_trend", mixed)
    asyncio.run(app._flow_fill(["100000.KQ", "200000.KQ", "300000.KQ"]))
    st = app._flow_source
    assert st["ok"] == 1 and st["empty"] == 1 and st["failed"] == 1, st
    assert st["source"] == "mobile_api"
    assert st["asof"] == "09-18"


def test_total_failure_logs_a_warning(monkeypatch, capsys):
    """전량 실패를 조용히 넘기면 벤더 개편을 몇 달 뒤에나 안다(v5.246 교훈)."""
    monkeypatch.setattr(app.naver_kr, "fetch_investor_trend", lambda *a, **k: None)
    asyncio.run(app._flow_fill(["100000.KQ", "200000.KQ"]))
    out = capsys.readouterr().out
    assert "전량 실패" in out and "naver 개편 의심" in out, out
    assert app._flow_source["source"] == "failed"


def test_partial_success_does_not_warn(monkeypatch, capsys):
    def half(ticker, *a, **k):
        return None if ticker.startswith("1") else [
            {"bizdate": "20260918", "organPureBuyQuant": "+1",
             "foreignerPureBuyQuant": "+1"}]
    monkeypatch.setattr(app.naver_kr, "fetch_investor_trend", half)
    asyncio.run(app._flow_fill(["100000.KQ", "300000.KQ"]))
    assert "전량 실패" not in capsys.readouterr().out


def test_an_exception_does_not_break_the_batch(monkeypatch):
    def boom(ticker, *a, **k):
        if ticker.startswith("1"):
            raise RuntimeError("naver 개편")
        return [{"bizdate": "20260918", "organPureBuyQuant": "+1",
                 "foreignerPureBuyQuant": "+1"}]
    monkeypatch.setattr(app.naver_kr, "fetch_investor_trend", boom)
    out = asyncio.run(app._flow_fill(["100000.KQ", "300000.KQ"]))
    assert out["300000.KQ"]["ok"] is True
    assert out["100000.KQ"]["ok"] is False


def test_route_only_fetches_c1_c2(monkeypatch):
    """C0 대기·C3 이탈·다른 셋업은 제외 — 요청 수를 절반으로(사용자 지시)."""
    src = Path(app.__file__).read_text(encoding="utf-8")
    i = src.index('@app.get("/api/abc")')
    body = src[i:src.index('@app.get("/api/debug/memory")')]
    assert 'startswith(("C1", "C2"))' in body, "범위 제한이 없다"
    assert "_flow_fill(flow_targets)" in body


def test_isolated_pool_is_not_the_earnings_pool():
    """실적 조회가 느릴 때 수급이 같이 막히면 안 된다(v5.17과 같은 구조)."""
    assert app._flow_executor is not app._earnings_executor


def test_flow_is_display_only_never_a_gate():
    """**필터가 아니라 표시**다(사용자 지시). 등급·정렬이 읽으면 안 된다."""
    import inspect
    for fn in (app.abc_screener.grade, app.abc_screener.company_axis,
               app.abc_screener.analyze_abc):
        src = inspect.getsource(fn)
        assert "flow" not in src, f"{fn.__name__}가 수급을 읽는다"
    src = Path(app.__file__).read_text(encoding="utf-8")
    i = src.index('    order = {"C1 벽앞"')
    assert "flow" not in src[i:i + 400], "정렬이 수급을 읽는다"
