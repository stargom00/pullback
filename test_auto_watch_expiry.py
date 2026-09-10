"""_auto_watch 만료 결함 — signal_snapshot과 공통 헬퍼로 통합 수정
(v5.238, 사용자 지시 — "두 곳을 따로 고치지 마라. 공통 헬퍼 하나로
간다"). 실측(710건, auto_watch.json 프로덕션 pull) 근거:
- APGE|돌파임박: signal_date 2026-07-17 이후 가격 데이터 자체가 없음
  (상장폐지/장기 거래정지 추정, yfinance 개별조회로 확인) — 만료
  판정이 봉 위치 차(len(df)-1 - df.index.get_loc(sig_ts))를 썼던
  탓에 이 종목은 days_since가 영원히 0으로 고정돼 2개월째 "watching".
- 006400.KS|돌파임박: signal_date 3거래일 전인데도 watching 유지 —
  경계값(days_since>=3)이 그날 EOD 틱에서 아직 안 걸린 사례.

이 파일은 "수정 전 FAIL(결함 재현) → 수정 후 PASS" 순서로 검증한다."""
import asyncio
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

import app

KST = app.KST


@pytest.fixture
def isolated_state(monkeypatch):
    """_auto_watch/_cache/_paper_track을 격리하고, 디스크 저장을 전부
    no-op으로 바꾼다 — 이 세션에서 pull해온 실제 프로덕션
    auto_watch.json(레포 루트, .gitignore 처리됨)을 테스트가 절대
    덮어쓰지 않게 하기 위함(_save_auto_watch가 원래 이 경로에 씀)."""
    aw: dict = {}
    monkeypatch.setattr(app, "_auto_watch", aw)
    monkeypatch.setattr(app, "_cache", {})
    monkeypatch.setattr(app, "_paper_track", [])
    monkeypatch.setattr(app, "_save_auto_watch", lambda data: None)
    monkeypatch.setattr(app, "_save_paper_track", lambda data: None)
    return aw


def _rec(ticker, tab, market, signal_date, signal_high=1000.0, signal_low=950.0):
    return {
        "ticker": ticker, "tab": tab, "name": ticker, "market": market,
        "sector": None, "status": "watching",
        "signal_date": signal_date, "stop": signal_low, "pivot": signal_high,
        "signal_high": signal_high, "signal_low": signal_low, "base_vol50": 10000.0,
        "rs": 90, "risk_pct": 5.0, "atr_pct": 2.0,
        "registered_at": signal_date,
        "confirmed_at": None, "confirm_close": None, "confirm_stop": None, "expired_at": None,
    }


def test_apge_style_frozen_feed_expires(isolated_state):
    """★ 결함 재현 + 수정 검증. 데이터 피드가 멈춘 종목(df 마지막 봉이
    signal_date에 고정, 그 이후로 안 늘어남) — 실제 APGE 사고를 합성
    데이터로 재현. df가 51봉 미만이라 confirm 판정은 항상 False로
    떨어지므로(_pending_watch_confirm_check 요구사항) 만료 분기만
    순수하게 검증된다.

    수정 전: 봉 위치 기반 계산이 df.index.get_loc(sig_ts)로 sig_ts를
    df의 마지막 봉에서 찾아 days_since=0 — 영원히 만료 안 됨(FAIL).
    수정 후: 달력 기반이라 실제 경과(60일 이상)를 정확히 반영해
    만료됨(PASS)."""
    aw = isolated_state
    old_date = (datetime.now(KST) - timedelta(days=60)).strftime("%Y-%m-%d")
    aw["APGE|돌파임박"] = _rec("APGE", "돌파임박", "US", old_date,
                                signal_high=134.2, signal_low=133.935)
    idx = pd.bdate_range(end=old_date, periods=40)   # 51봉 미만 — confirm 항상 False
    close = np.linspace(130.0, 133.96, 40)
    df = pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close,
                        "Volume": np.full(40, 1_000_000.0)}, index=idx)
    bundle = {"data": {"APGE": df}}
    asyncio.run(app._refresh_auto_watch(bundle, "us", datetime.now(KST).strftime("%Y-%m-%d")))
    assert aw["APGE|돌파임박"]["status"] == "expired", (
        "데이터 피드가 멈춘 종목이 여전히 watching — 만료 결함 재현됨(수정 전) "
        "또는 수정이 이 케이스를 못 잡음(수정 후라면 버그)"
    )


def test_006400_style_boundary_no_df_still_expires(isolated_state, monkeypatch):
    """★ 006400.KS 재현 — signal_date가 정확히 경계값(3거래일 전)이고,
    이번엔 df 자체가 없는(그날 fetch 실패 등) 최악의 경우까지 포함해
    만료가 걸리는지 확인. 실제 프로덕션에서 006400.KS는 df는 있었지만
    "그날 EOD 틱이 아직 안 돎"으로 설명 가능했던 반면, 이 테스트는 더
    강하게 "df가 아예 없어도" 만료 판정 자체(_trading_days_since)는
    df와 완전히 무관하게 동작함을 증명한다(3번 작업의 핵심 산출물)."""
    aw = isolated_state
    monkeypatch.setattr(app, "_trading_days_since", lambda market, date_str: 3)
    aw["006400.KS|돌파임박"] = _rec("006400.KS", "돌파임박", "KR", "2026-09-07",
                                      signal_high=554000.0, signal_low=541000.0)
    bundle = {"data": {}}   # df 없음 — 수정 전 코드였다면 "df is None: continue"로 통째로 스킵됐음
    asyncio.run(app._refresh_auto_watch(bundle, "kr", "2026-09-10"))
    assert aw["006400.KS|돌파임박"]["status"] == "expired"


def test_watching_not_expired_before_window(isolated_state, monkeypatch):
    """회귀 방지 — 2거래일 경과(AUTO_WATCH_CONFIRM_WINDOW_DAYS=3 미달)면
    아직 watching이어야 한다(너무 공격적으로 만료시키는 회귀 방지)."""
    aw = isolated_state
    monkeypatch.setattr(app, "_trading_days_since", lambda market, date_str: 2)
    aw["FAKE1|돌파임박"] = _rec("FAKE1", "돌파임박", "KR", "2026-09-08")
    bundle = {"data": {}}
    asyncio.run(app._refresh_auto_watch(bundle, "kr", "2026-09-10"))
    assert aw["FAKE1|돌파임박"]["status"] == "watching"


def test_watching_expires_exactly_at_window(isolated_state, monkeypatch):
    """회귀 방지 — 정확히 3거래일(AUTO_WATCH_CONFIRM_WINDOW_DAYS) 도달
    시점에 만료(경계값 >=, off-by-one 회귀 방지)."""
    aw = isolated_state
    monkeypatch.setattr(app, "_trading_days_since", lambda market, date_str: 3)
    aw["FAKE2|돌파임박"] = _rec("FAKE2", "돌파임박", "KR", "2026-09-07")
    bundle = {"data": {}}
    asyncio.run(app._refresh_auto_watch(bundle, "kr", "2026-09-10"))
    assert aw["FAKE2|돌파임박"]["status"] == "expired"
