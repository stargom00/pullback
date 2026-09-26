"""v5.287 — /api/dist 응답에 bar_date(평가한 마지막 봉 날짜).

봇이 "언제 자료로 판정한 값인지"를 응답만 보고 알 수 있어야 한다. 같은
번들에서 나온 값이라도 휴장일·거래정지·캐시 낡음이면 마지막 봉이 오늘이
아닐 수 있는데, 예전 응답에는 그 단서가 전혀 없었다 — opening-surge가
어제 봉을 오늘 10분치로 읽어 배수를 ~39배 부풀린 사고(v5.287 [1])와 같은
종류의 오해가 이쪽에서도 가능했다.
"""
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app

LAST_DAY = "2026-09-23"


def _df(n=80, last_day=LAST_DAY):
    idx = pd.date_range(end=last_day, periods=n, freq="D")
    rng = np.random.default_rng(7)
    close = pd.Series(100 + rng.normal(0, 1, n).cumsum(), index=idx)
    return pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close,
        "Volume": pd.Series(rng.integers(1e5, 1e6, n).astype(float), index=idx),
    }, index=idx)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app, "APP_PASSWORD", "", raising=False)
    monkeypatch.setattr(app, "_data_cache", {"data:kr": {"data": {"005930.KS": _df()}}})
    return TestClient(app.app)


def test_dist_reports_the_evaluated_bar_date(client):
    body = client.get("/api/dist/005930.KS").json()
    assert body["ok"] is True, body
    assert body["bar_date"] == LAST_DAY, body


def test_existing_fields_are_untouched(client):
    """추가만 한다 — 봇 파서가 쓰던 키가 사라지거나 값이 바뀌면 안 된다."""
    body = client.get("/api/dist/005930.KS").json()
    import scanner as scanner_mod
    df = app._data_cache["data:kr"]["data"]["005930.KS"]
    expected = scanner_mod.distribution_check(df["Close"], df["High"], df["Low"], df["Volume"])
    for k, v in expected.items():
        assert body[k] == v, (k, body.get(k), v)
    assert body["ticker"] == "005930.KS"


def test_bar_date_follows_the_data_not_the_clock(client, monkeypatch):
    """시계가 아니라 **데이터**를 따라야 한다 — 다른 마지막 봉이면 다른 값."""
    other = "2026-08-14"
    monkeypatch.setattr(app, "_data_cache", {"data:kr": {"data": {"005930.KS": _df(last_day=other)}}})
    assert client.get("/api/dist/005930.KS").json()["bar_date"] == other
