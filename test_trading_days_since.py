"""_trading_days_since() 단위 테스트 (v5.238) — 순수 날짜 연산, price
데이터/monkeypatch 없이 `today` 인자 주입만으로 결정론적 테스트 가능."""
from datetime import datetime

import app

KST = app.KST


def _d(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d")


def test_same_day_is_zero():
    assert app._trading_days_since("kr", "2026-09-10", today=_d("2026-09-10")) == 0


def test_future_date_is_zero():
    assert app._trading_days_since("kr", "2026-09-15", today=_d("2026-09-10")) == 0


def test_none_date_is_zero():
    assert app._trading_days_since("kr", None, today=_d("2026-09-10")) == 0


def test_weekend_only_us():
    """US, 2026-09-08(화)~2026-09-14(월) 사이 주말(09-12/13)만 끼고 공휴일 없음.
    09-09,10,11,14 = 4거래일."""
    assert app._trading_days_since("us", "2026-09-08", today=_d("2026-09-14")) == 4


def test_weekend_and_holiday_us():
    """US, 2026-09-04(금)~2026-09-08(화). 09-05/06 주말, 09-07 Labor Day(NYSE
    공휴일) — 실제 거래일은 09-08 하루뿐."""
    assert app._trading_days_since("us", "2026-09-04", today=_d("2026-09-08")) == 1


def test_weekend_and_holiday_kr_chuseok():
    """KR, 2026-09-23(수)~2026-09-28(월). 09-24/25 추석 연휴, 09-26/27 주말 —
    실제 거래일은 09-28 하루뿐."""
    assert app._trading_days_since("kr", "2026-09-23", today=_d("2026-09-28")) == 1


def test_market_case_insensitive():
    assert (app._trading_days_since("KR", "2026-09-08", today=_d("2026-09-10"))
            == app._trading_days_since("kr", "2026-09-08", today=_d("2026-09-10")))


def test_default_today_is_real_now():
    """today 인자를 생략하면 실제 datetime.now(KST) 기준으로 동작 — 과거
    고정 날짜 대비 0 이상만 확인(회귀 방지용 최소 가드, 날짜 하드코딩 없음)."""
    assert app._trading_days_since("kr", "2020-01-01") > 0
