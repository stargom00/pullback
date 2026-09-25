"""v5.287 — /api/opening-surge는 **오늘 봉이 있는 종목만** 본다.

[사고] 2026-09-25(추석 휴장) 얼마냐봇 조사에서, 이 엔드포인트가 마지막 봉이
09-23인 데이터를 "오늘 10분치 누적 거래량"으로 해석해 1,483종목 중
**1,006종목**을 급증으로 통과시켰다. 원인은 계산 전제 자체다 — 09:00부터
지금까지 쌓인 거래량을 `_kr_session_elapsed_ratio`(10분 ≈ 1/39)로 나누는데,
하루치가 통째로 들어오면 배수가 **약 39배** 부풀려진다.

판정은 휴장일 목록이 아니라 **데이터 날짜**로 한다 — 캐시가 낡은 경우,
종목별로 마지막 봉이 다른 경우(거래정지 등)까지 같은 규칙으로 걸린다.
"""
import asyncio
import json
from datetime import timedelta

import pandas as pd
import pytest

import app


def _df(last_day, n=60, vol=1_000_000, last_vol=None):
    idx = pd.date_range(end=last_day, periods=n, freq="D")
    return pd.DataFrame({
        "Open": [100.0] * n, "High": [101.0] * n, "Low": [99.0] * n,
        "Close": [100.0] * n,
        "Volume": [float(vol)] * (n - 1) + [float(last_vol if last_vol is not None else vol)],
    }, index=idx)


@pytest.fixture
def surge_env(monkeypatch):
    now = app.datetime.now(app.KST)
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    monkeypatch.setattr(app, "_kr_session_elapsed_ratio", lambda n: 1 / 39)
    return today, yesterday


def _run(monkeypatch, data, universe=None):
    async def fake_fetch(market, wait_for_fresh=False, force=False):
        return {"data": data, "universe": universe or {t: t for t in data}}
    monkeypatch.setattr(app, "_fetch_market_data", fake_fetch)
    return json.loads(asyncio.run(app.opening_surge()).body)


def test_yesterday_last_bar_is_excluded(surge_env, monkeypatch):
    """**핵심 재현**: 어제 확정봉을 오늘 10분치로 읽으면 ~39배가 나온다."""
    today, yesterday = surge_env
    body = _run(monkeypatch, {"005930.KS": _df(yesterday)})
    assert body["hits"] == [], body
    assert body["n_stale_excluded"] == 1
    assert body["reason"] == "no_today_bar", body


def test_today_last_bar_is_included(surge_env, monkeypatch):
    today, _ = surge_env
    # 오늘 봉 거래량이 평소의 10배 → 시간보정(1/39) 후에도 급증
    body = _run(monkeypatch, {"005930.KS": _df(today, last_vol=10_000_000)})
    assert [h["ticker"] for h in body["hits"]] == ["005930.KS"], body
    assert body["n_stale_excluded"] == 0
    assert "reason" not in body


def test_mixed_universe_keeps_only_todays_bars(surge_env, monkeypatch):
    today, yesterday = surge_env
    body = _run(monkeypatch, {
        "005930.KS": _df(today, last_vol=10_000_000),
        "000660.KS": _df(yesterday, last_vol=10_000_000),
    })
    assert [h["ticker"] for h in body["hits"]] == ["005930.KS"]
    assert body["n_stale_excluded"] == 1
    assert "reason" not in body, "일부라도 오늘 봉이 있으면 no_today_bar가 아니다"


def test_no_reason_when_there_is_simply_nothing_surging(surge_env, monkeypatch):
    """0건이라고 다 no_today_bar가 아니다 — 오늘 봉은 있는데 급증이 없는 경우."""
    today, _ = surge_env
    body = _run(monkeypatch, {"005930.KS": _df(today)})
    assert body["hits"] == [] and body["n_stale_excluded"] == 0
    assert "reason" not in body, body


def test_unparseable_index_is_excluded_not_assumed_today(monkeypatch, surge_env):
    df = _df(surge_env[0]).reset_index(drop=True)   # 날짜 인덱스 없음
    body = _run(monkeypatch, {"005930.KS": df})
    assert body["hits"] == [] and body["n_stale_excluded"] == 1
