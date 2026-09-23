"""v5.281 — 평일 장전(00:00~09:00 KST)을 "확정 거래일"로 본다.

[왜] `_market_session_key("kr")`는 평일 **00:00~20:10 전체가 None**이었다.
None이면 `_fetch_market_data_inner()`가 디스크 캐시를 **읽지도 쓰지도 않는다**
(app.py: `if daykey and not force:` 읽기 / `if daykey:` 쓰기).
그런데 장전 데이터는 **직전 거래일 확정 종가와 동일**하다 — 마감 후와 성질이
같은데 캐시만 못 쓰고 있었다. 실측으로 1,100여 종목을 통째로 다시 받는 콜드
스캔이 kr_sec 1,028~1,110초였다.

[키는 오늘이 아니라 직전 거래일] 오늘 장이 아직 안 열렸는데 오늘 날짜를 주면
"열리지도 않은 장의 데이터를 확정"이라고 부르는 셈이다. 직전 거래일 탐색은
**기존 `_last_trading_daykey()`를 재사용**한다(새 휴장일 로직 금지 — 판정
지점이 둘로 갈린다).

[장중은 그대로] 09:00~20:10은 값이 계속 바뀌므로 None 유지.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app  # noqa: E402

KST = timezone(timedelta(hours=9))


def key_at(s: str, market: str = "kr", monkeypatch=None):
    """그 시각의 `_market_session_key`. `datetime` 전체를 가로채면
    `is_trading_day()` 내부까지 MagicMock이 되어 깨진다(작성 중 겪음) —
    `app.datetime.now`만 바꾼다."""
    d = datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=KST)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return d

    orig = app.datetime
    app.datetime = _DT
    try:
        return app._market_session_key(market)
    finally:
        app.datetime = orig


# 2026-09-21 월 / 09-22 화 / 09-25 금 / 09-26 토
def test_tuesday_premarket_returns_monday():
    assert key_at("2026-09-22 07:00") == "2026-09-21"


def test_monday_premarket_returns_friday():
    """주말을 건너뛴다 — `_last_trading_daykey()` 재사용의 핵심."""
    assert key_at("2026-09-21 07:00") == "2026-09-18"


def test_intraday_is_still_none():
    """09:00~20:10은 값이 계속 바뀐다 — 확정이 아니다."""
    assert key_at("2026-09-22 10:00") is None
    assert key_at("2026-09-22 15:00") is None
    assert key_at("2026-09-22 20:00") is None


def test_after_close_returns_today():
    assert key_at("2026-09-22 20:15") == "2026-09-22"


def test_boundary_at_open():
    """09:00 정각부터는 장중 — 08:59는 아직 장전."""
    assert key_at("2026-09-22 08:59") == "2026-09-21"
    assert key_at("2026-09-22 09:00") is None


def test_weekend_unchanged():
    """주말 동작은 건드리지 않았다."""
    assert key_at("2026-09-26 12:00") == "2026-09-26"


def test_us_market_unaffected():
    """이번 변경은 KR 전용 — US 경로가 딸려 바뀌면 안 된다."""
    assert key_at("2026-09-22 07:00", market="us") == "2026-09-22"
    assert key_at("2026-09-22 05:00", market="us") is None


def test_confirmed_daykey_passes_it_through():
    """`_confirmed_daykey()`가 장전 키를 그대로 살려야 캐시가 실제로 붙는다."""
    d = datetime.strptime("2026-09-22 07:00", "%Y-%m-%d %H:%M").replace(tzinfo=KST)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return d

    orig = app.datetime
    app.datetime = _DT
    try:
        assert app._confirmed_daykey("kr") == "2026-09-21"
    finally:
        app.datetime = orig


def test_open_constant_is_not_a_literal():
    import inspect
    src = inspect.getsource(app._market_session_key)
    assert "KR_OPEN_HM" in src, "개장 시각이 리터럴로 박혔다"
    assert app.KR_OPEN_HM == 9 * 60


def test_previous_day_uses_the_existing_helper():
    """새 휴장일 로직을 만들지 않았는지 — 판정 지점이 둘로 갈리면 안 된다."""
    import inspect
    src = inspect.getsource(app._market_session_key)
    assert "_last_trading_daykey(" in src, src
    assert "holiday" not in src.lower()
