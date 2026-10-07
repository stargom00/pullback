"""v5.338 — POST /api/prices 500(매매 기록 [현재가 갱신] 보유 6건 전부 "조회 실패") 회귀 테스트.

원인(운영 로그 2026-10-07 13:48 NZDT 확정): app.py batch_prices의 JSONResponse →
`ValueError: Out of range float values are not JSON compliant: nan`. yfinance가 장 마감 뒤 집계 전 당일 봉을
**OHLC = NaN, Volume만** 채워 준다(JBGS·RDW 10-06 봉 실측 — test_fixtures/yf_nan_ohlc_20261006.json.gz). US 분기가
그 봉의 High(NaN)를 highs에 넣어 요청 전체가 500이 됐다. KR·UPBIT 종목이 섞여 있어도 한 종목 때문에 전부 실패.
v5.337 변경(관찰 단계·출발일 모양·일봉 조회 확대)과 무관 — /api/prices는 그 코드를 거치지 않는다. 저점 관찰·추적·관심·평가는
harness._fetch_us_batch(_downcast가 Close NaN 봉 제거)를 써서 영향 없음.

사보타주 확인(2026-10-07, FAIL 확인 후 원복): US 분기를 수정 전 코드(row.get("High") 그대로 float)로 되돌림 →
test_mixed_holdings_with_real_nan_row · test_nan_row_without_last_price FAIL
"""
from __future__ import annotations

import gzip
import json
import math
import os

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app

ROOT = os.path.dirname(os.path.abspath(__file__))
FX = json.load(gzip.open(os.path.join(ROOT, "test_fixtures", "yf_nan_ohlc_20261006.json.gz"), "rt", encoding="utf-8"))["tickers"]


def _df(rows):
    idx = pd.to_datetime([r["date"] for r in rows]).tz_localize("America/New_York")
    return pd.DataFrame({k: [float("nan") if r[k] is None else r[k] for r in rows]
                         for k in ("Open", "High", "Low", "Close", "Volume")}, index=idx)


class _FakeTicker:
    def __init__(self, t, last_price=True):
        self.t, self._lp = t, last_price

    @property
    def fast_info(self):
        return type("FI", (), {"last_price": FX[self.t]["last_price"] if self._lp else None})()

    def history(self, period="5d", interval="1d"):
        return _df(FX[self.t]["rows"])


@pytest.fixture
def client(monkeypatch):
    import naver_kr
    import upbit
    kr = pd.DataFrame({"Open": [7380.0], "High": [7760.0], "Low": [7380.0], "Close": [7700.0], "Volume": [11469.0]},
                      index=pd.to_datetime(["2026-10-07"]))
    monkeypatch.setattr(naver_kr, "fetch_history", lambda tk, days=10: kr)
    monkeypatch.setattr(upbit, "fetch_ticker", lambda tk: {"trade_price": 453.0, "high_price": 460.0, "acc_trade_volume": 127207.4})
    return TestClient(app.app, raise_server_exceptions=False)


def test_fixture_is_the_real_failing_shape():
    """전제: 실데이터 그대로 — JBGS·RDW 마지막 봉은 OHLC 결측·거래량만, AAPL은 정상 봉."""
    for t in ("JBGS", "RDW"):
        last = FX[t]["rows"][-1]
        assert [last[k] for k in ("Open", "High", "Low", "Close")] == [None] * 4 and last["Volume"] > 0
    assert FX["AAPL"]["rows"][-1]["High"] is not None


def test_mixed_holdings_with_real_nan_row(client, monkeypatch):
    """운영 보유 6건과 같은 구성(KR 3 · UPBIT 1 · US 2) → 200, 각 현재가."""
    monkeypatch.setattr(app.yf, "Ticker", lambda t: _FakeTicker(t))
    tickers = ["008040.KS", "475560.KS", "KRW-CYBER", "JBGS", "RDW", "0013V0.KQ"]
    r = client.post("/api/prices", json={"tickers": tickers})
    assert r.status_code == 200, r.text
    j = r.json()
    assert set(j["prices"]) == set(tickers)
    assert j["prices"]["JBGS"] == FX["JBGS"]["last_price"] and j["prices"]["RDW"] == FX["RDW"]["last_price"]
    assert j["prices"]["008040.KS"] == 7700.0 and j["prices"]["KRW-CYBER"] == 453.0
    # 당일 고가는 결측 → 기존 규칙(봉 없을 때와 같음)대로 현재가로 대신, 거래량은 당일 값 그대로
    assert j["highs"]["JBGS"] == FX["JBGS"]["last_price"] and j["volumes"]["JBGS"] == FX["JBGS"]["rows"][-1]["Volume"]
    for k in ("prices", "highs", "volumes"):
        assert all(math.isfinite(v) for v in j[k].values()), k


def test_nan_row_without_last_price(client, monkeypatch):
    """현재가(last_price)도 없으면 마지막 **유효** 봉 하나에서 종가·고가·거래량을 같이 쓴다(결측 봉과 섞지 않는다)."""
    monkeypatch.setattr(app.yf, "Ticker", lambda t: _FakeTicker(t, last_price=False))
    r = client.post("/api/prices", json={"tickers": ["JBGS"]})
    assert r.status_code == 200, r.text
    prev = FX["JBGS"]["rows"][-2]
    j = r.json()
    assert (j["prices"]["JBGS"], j["highs"]["JBGS"], j["volumes"]["JBGS"]) == (prev["Close"], prev["High"], prev["Volume"])


def test_normal_us_row_unchanged(client, monkeypatch):
    monkeypatch.setattr(app.yf, "Ticker", lambda t: _FakeTicker(t))
    j = client.post("/api/prices", json={"tickers": ["AAPL"]}).json()
    last = FX["AAPL"]["rows"][-1]
    assert (j["prices"]["AAPL"], j["highs"]["AAPL"], j["volumes"]["AAPL"]) == (FX["AAPL"]["last_price"], last["High"], last["Volume"])


def test_kr_nan_close_is_missing_not_500(monkeypatch):
    import naver_kr
    bad = pd.DataFrame({"Open": [1.0], "High": [float("nan")], "Low": [1.0], "Close": [float("nan")], "Volume": [5.0]},
                       index=pd.to_datetime(["2026-10-07"]))
    monkeypatch.setattr(naver_kr, "fetch_history", lambda tk, days=10: bad)
    r = TestClient(app.app, raise_server_exceptions=False).post("/api/prices", json={"tickers": ["008040.KS"]})
    assert r.status_code == 200 and r.json()["prices"] == {}             # 화면은 "조회 실패"(그 종목만)
