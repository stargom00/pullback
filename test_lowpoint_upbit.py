"""v5.313 — 저점 탭 업비트 코인 기록(KRW-BTC 형식).

서버: 종목 확인(업비트 공개 ticker로 실재 판정, korean_name) · 레코드 검증(UPBIT ⇔ KRW-XXX) ·
/api/prices의 업비트 분기(소수점 그대로, closed=false) · 접두사 없는 입력은 기존 경로 그대로.
프론트(node로 production 원문 실행): 표시 자릿수 · ₩ 합계 포함/$ 합계 불포함 · tvUrl UPBIT:BTCKRW.

네트워크 테스트의 skip 판정은 **원천(upbit.fetch_ticker)을 직접** 찍어서 한다 — 검사 대상
(lp_resolve) 결과로 skip을 정하면 확인 분기를 지워도 skip으로 빠져 사보타주가 통과한다
(v5.310 test_lowpoint_resolve_fallback.py에서 실제로 겪은 결함).

사보타주 확인(2026-10-02, FAIL 확인 후 원복):
① lp_resolve의 업비트 실재 확인 제거(KRW- 형식이면 무조건 ok) →
   test_unknown_market_fails_with_hint[fake]·[live] + test_empty_ticker_response_is_failure 3건 FAIL
② _lptNameLink에 UPBIT용 URL 사본 주입 → test_tv_link_upbit_lowpoint_tab_uses_shared_helper +
   test_lowpoint_name_link.py::test_no_url_copy_in_lowpoint_link 2건 FAIL
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess

import pytest

import app
import upbit

SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html"),
           encoding="utf-8").read()


def _b(r):
    return json.loads(r.body)


def _resolve(q):
    return _b(asyncio.run(app.lp_resolve(q)))


class _Req:
    def __init__(self, body):
        self._body = body
        self.headers = {"user-agent": "pytest"}
        self.client = type("C", (), {"host": "127.0.0.1"})()

    async def json(self):
        return self._body


def _live_or_skip():
    """원천(업비트 공개 API) 자체가 되는지 — 우리 코드가 아니라 원천으로 판정."""
    if not upbit.fetch_ticker("KRW-BTC"):
        pytest.skip("업비트 공개 API 응답 없음(네트워크/차단) — 우리 코드 판정 불가")


# ── 입력 형식 ───────────────────────────────────────────────────────
@pytest.mark.parametrize("q,ok", [("KRW-BTC", True), ("krw-xrp", True), ("KRW-1INCH", True),
                                  ("BTC", False), ("BTC-KRW", False), ("USDT-BTC", False),
                                  ("042000", False), ("EVLV", False), ("KRW-", False)])
def test_is_upbit_format_only_krw_prefix(q, ok):
    assert upbit.is_upbit(q) is ok


# ── 종목 확인(결정적: 업비트 응답을 가짜로) ─────────────────────────
class _Resp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


@pytest.fixture
def fake_upbit(monkeypatch):
    markets = {"KRW-BTC": ("비트코인", 116028000.0), "KRW-XRP": ("엑스알피(리플)", 3245.5),
               "KRW-SHIB": ("시바이누", 0.01234)}

    def get(url, params=None, timeout=None):
        assert timeout == upbit._TIMEOUT, "새 대기시간 금지 — 기존 값 재사용"
        if url == upbit._TICKER_URL:
            m = (params or {}).get("markets")
            if m not in markets:
                return _Resp(404, {"error": {"name": 404, "message": "Code not found"}})
            return _Resp(200, [{"market": m, "trade_price": markets[m][1], "high_price": markets[m][1],
                                "acc_trade_volume": 12.5}])
        if url == upbit._MARKETS_URL:
            return _Resp(200, [{"market": k, "korean_name": v[0]} for k, v in markets.items()])
        raise AssertionError(url)
    monkeypatch.setattr(upbit.requests, "get", get)
    return markets


def test_register_krw_btc_resolves_name_and_market(fake_upbit):
    r = _resolve("KRW-BTC")
    assert r == {"ok": True, "ticker": "KRW-BTC", "name": "비트코인", "mkt": "UPBIT", "source": "upbit"}
    assert _resolve("krw-btc")["ticker"] == "KRW-BTC"   # 소문자 입력도 마켓코드로


@pytest.mark.parametrize("mode", ["fake", "live"])
def test_unknown_market_fails_with_hint(mode, request):
    if mode == "fake":
        request.getfixturevalue("fake_upbit")
    else:
        _live_or_skip()
    r = _resolve("KRW-ZZZZ")
    assert r["ok"] is False and r["upbit"] is True and r["reason"] == "not_found"
    assert "코인은 KRW-BTC 형식" in r["hint"]


def test_empty_ticker_response_is_failure(monkeypatch):
    monkeypatch.setattr(upbit.requests, "get", lambda *a, **k: _Resp(200, []))
    assert upbit.resolve("KRW-BTC") is None
    assert _resolve("KRW-BTC")["ok"] is False


def test_live_register_btc():
    _live_or_skip()
    r = _resolve("KRW-BTC")
    assert r["ok"] and r["name"] == "비트코인" and r["mkt"] == "UPBIT" and r["ticker"] == "KRW-BTC"


def test_hint_mentions_coin_format_for_stock_failures_too():
    assert "코인은 KRW-BTC 형식" in app.LP_RESOLVE_HINT
    assert app.LP_RESOLVE_HINT.startswith("6자리 코드(KR) 또는 티커(US)")   # v5.310 문구 유지
    assert "t.upbit ?" in SRC and "업비트 원화(KRW) 마켓에 없어요" in SRC


# ── 기존 KR/US 경로 불변 ────────────────────────────────────────────
@pytest.mark.parametrize("q,ticker,mkt", [("042000", "042000.KQ", "KR"), ("카페24", "042000.KQ", "KR"),
                                          ("EVLV", "EVLV", "US")])
def test_stock_paths_never_touch_upbit(monkeypatch, q, ticker, mkt):
    def boom(*a, **k):
        raise AssertionError("KRW- 접두사 없는 입력이 업비트로 갔다")
    monkeypatch.setattr(upbit, "resolve", boom)
    monkeypatch.setattr(upbit, "fetch_ticker", boom)
    r = _resolve(q)
    if not r.get("ok"):
        pytest.skip(f"{q}: 원천/목록 조회 실패(네트워크) — 업비트 미호출은 이미 확인됨")
    assert (r["ticker"], r["mkt"]) == (ticker, mkt)


# ── 레코드 검증 ─────────────────────────────────────────────────────
BASE = {"id": 1, "kind": "단기", "buyDate": "2026-10-02", "buyPrice": 116000000, "qty": 0.0123}


@pytest.mark.parametrize("rec,err", [
    ({"mkt": "UPBIT", "code": "KRW-BTC"}, None),
    ({"mkt": "UPBIT", "code": "BTC"}, "KRW-XXX"),
    ({"mkt": "KR", "code": "KRW-BTC"}, "KRW-XXX"),
    ({"mkt": "US", "code": "KRW-BTC"}, "KRW-XXX"),
    ({"mkt": "KR", "code": "042000.KQ"}, None),
    ({"mkt": "US", "code": "EVLV"}, None),
    ({"mkt": "BINANCE", "code": "BTCUSDT"}, "mkt는"),
])
def test_record_validation(rec, err):
    got = app._lp_trade_invalid({**BASE, **rec})
    assert (got is None) if err is None else (err in got), got


def test_fractional_coin_qty_saved(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "LP_TRADES_PATH", str(tmp_path / "t.json"))
    monkeypatch.setattr(app, "LP_TRADES_DELETE_LOG_PATH", str(tmp_path / "d.log"))
    rec = {**BASE, "mkt": "UPBIT", "code": "KRW-XRP", "name": "엑스알피(리플)", "buyPrice": 3245.5, "qty": 12.345678}
    r = asyncio.run(app.lp_trade_put(1, _Req({"record": rec, "base_rev": None})))
    assert r.status_code == 200, _b(r)
    saved = json.loads((tmp_path / "t.json").read_text(encoding="utf-8"))[0]
    assert saved["buyPrice"] == 3245.5 and saved["qty"] == 12.345678 and saved["mkt"] == "UPBIT"


# ── 현재가 ─────────────────────────────────────────────────────────
def _prices(tickers):
    return _b(asyncio.run(app.batch_prices(_Req({"tickers": tickers}))))


def test_prices_upbit_keeps_decimals_and_never_closed(fake_upbit):
    d = _prices(["KRW-BTC", "KRW-SHIB", "KRW-ZZZZ"])
    assert d["prices"]["KRW-BTC"] == 116028000.0
    assert d["prices"]["KRW-SHIB"] == 0.01234, "소수점 가격이 깎였다"
    assert "KRW-ZZZZ" not in d["prices"], "없는 마켓이 가격을 받았다"
    assert d["closed"]["KRW-BTC"] is False and d["closed"]["KRW-SHIB"] is False


def test_prices_live_btc_xrp():
    _live_or_skip()
    d = _prices(["KRW-BTC", "KRW-XRP"])
    assert d["prices"]["KRW-BTC"] > 1_000_000 and d["prices"]["KRW-XRP"] > 0
    assert isinstance(d["prices"]["KRW-XRP"], float)


# ── 프론트(node, production 원문 실행) ──────────────────────────────
def _fn(name):
    start = SRC.index(f"function {name}(")
    i = SRC.index("{", SRC.index(")", start))
    depth = 0
    for j in range(i, len(SRC)):
        depth += {"{": 1, "}": -1}.get(SRC[j], 0)
        if depth == 0:
            return SRC[start:j + 1]
    raise AssertionError(name)


def _node(expr, *fns):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    layout = [l for l in SRC.splitlines() if l.strip().startswith("const TV_LAYOUT_ID")]
    src = (layout[0] + "\nfunction _escapeHtml(s){return String(s).replace(/&/g,'&amp;')"
           ".replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
           + "\n".join(_fn(f) for f in fns) + f"\nconsole.log(JSON.stringify({expr}));")
    p = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@pytest.mark.parametrize("v,want", [(0.01234, "0.01234원"), (3245.5, "3,245.5원"),
                                    (116028000, "116,028,000원"), (0.1 + 0.2, "0.3원")])
def test_upbit_price_format_keeps_exchange_digits(v, want):
    assert _node(f"_lptFmt({v}, 'UPBIT')", "_lptFmt") == want


def test_kr_us_format_unchanged():
    assert _node("[_lptFmt(15070.4, 'KR'), _lptFmt(35.5, 'US')]", "_lptFmt") == ["15,070원", "$35.50"]


CLOSED = [
    {"sellDate": "2026-10-02", "mkt": "KR", "buyPrice": 1000, "sellPrice": 1100, "qty": 10},        # +1,000원
    {"sellDate": "2026-10-02", "mkt": "UPBIT", "buyPrice": 3000, "sellPrice": 3245.5, "qty": 2},   # +491원
    {"sellDate": "2026-10-02", "mkt": "US", "buyPrice": 10, "sellPrice": 12, "qty": 5},             # +$10
]


def test_won_total_includes_upbit_dollar_total_does_not():
    rs = _node(f"lpRealizedSummary({json.dumps(CLOSED)}, '2026-10-02')",
               "lpRealizedPnl", "lpCurrencyBucket", "lpRealizedSummary")
    assert rs["month"]["KR"] == 1491 and rs["year"]["KR"] == 1491
    assert rs["month"]["US"] == 10 and rs["n"]["month"]["KR"] == 2 and rs["n"]["month"]["US"] == 1


def test_monthly_summary_won_row_lists_upbit():
    rows = _node(f"lpMonthlySummary({json.dumps(CLOSED)})",
                 "lpRealizedPnl", "lpCurrencyBucket", "lpReturnPct", "lpMonthlySummary")
    won = [r for r in rows if r["mkt"] == "KR"][0]
    usd = [r for r in rows if r["mkt"] == "US"][0]
    assert won["pnl"] == 1491 and won["n"] == 2 and won["markets"] == ["KR", "UPBIT"]
    assert usd["pnl"] == 10 and usd["markets"] == ["US"]
    assert "m.markets.length ? m.markets : [m.mkt]).join(' · ')" in SRC   # 화면 표기


def test_currency_bucket_is_single_shared_function():
    code = "\n".join(l for l in SRC.split("\n") if not l.strip().startswith("//"))
    assert code.count("function lpCurrencyBucket(") == 1
    assert _fn("lpRealizedSummary").count("lpCurrencyBucket(r.mkt)") == 1
    assert _fn("lpMonthlySummary").count("lpCurrencyBucket(r.mkt)") == 1
    assert "r.mkt === 'US' ? 'US' : 'KR'" not in code, "버킷 판정 사본"


UP = {"code": "KRW-BTC", "name": "비트코인", "mkt": "UPBIT"}


def test_tv_link_upbit_format():
    url = _node("tvUrl('KRW-BTC', 'UPBIT')", "tvSymbolUrl", "tvUrl")
    assert url == _node("tvSymbolUrl('UPBIT:BTCKRW')", "tvSymbolUrl")
    assert "UPBIT%3ABTCKRW" in url


def test_tv_link_upbit_lowpoint_tab_uses_shared_helper():
    html = _node(f"_lptNameLink({json.dumps(UP, ensure_ascii=False)})", "tvSymbolUrl", "tvUrl", "_lptNameLink")
    href = re.search(r'href="([^"]+)"', html).group(1)
    assert href == _node("tvUrl('KRW-BTC', 'UPBIT')", "tvSymbolUrl", "tvUrl")
    code = "\n".join(l for l in SRC.split("\n") if not l.strip().startswith("//"))
    assert code.count("tradingview.com/chart") == 2, "트레이딩뷰 URL 생성이 tvSymbolUrl 밖에 생겼다(사본)"
    assert code.count("UPBIT:") == 1, "UPBIT 심볼 조립이 tvUrl 밖에 생겼다(사본)"


def test_tv_link_kr_us_unchanged():
    kr = _node("tvUrl('042000.KQ', 'KR')", "tvSymbolUrl", "tvUrl")
    us = _node("tvUrl('EVLV', 'US')", "tvSymbolUrl", "tvUrl")
    assert "KRX%3A042000" in kr and us.endswith("symbol=EVLV")
