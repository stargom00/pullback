"""카드 가격 시각 표기 — _price_staleness_fields() 검증 (v5.245, 사용자
지시). 2026-09-11 "한화생명 09:15 스캔가(5,980) vs 실제가(5,840) 2.3%
괴리" 사고 후속.

배경: 스캔 결과의 close/entry는 종목별 실제 fetch 시각(data_ts)이
REUSE_TTL(30분) 이내면 재사용되는 스냅샷이라, 최대 30분까지 stale할
수 있는데 화면에 아무 표기가 없어 현재가로 오인됐다.
`_price_staleness_fields(df, data_ts_val, is_kr)`이 카드에 붙일 문구를
계산한다 — 장중/장마감 두 국면을 다르게 처리(설계 보고, 1차 설계를
"장중에만 표시하면 장마감 구간이 통째로 공백"이라는 사용자 재검토로
수정):
- 장중: data_ts_val(실제 fetch 시각) 기준 PRICE_STALE_MINUTES(=
  REUSE_TTL//2=15분) 이상이면 "📸 HH:MM 기준"(live=True — saveJournal()
  등록 시 현재가 재확인 트리거).
- 장마감 후: 항상 df 마지막 봉 날짜 기준 "📸 MM-DD 종가 기준"(live=False
  — fetch 시각이 아니라 그 종가가 속한 거래일이 핵심 정보이므로 소스가
  다름).

PRICE_STALE_MINUTES는 REUSE_TTL의 절반이라는 비례 관계로 계산되는
판단값(실측/백테스트 아님, 코드 주석에 근거 명시 — 재발방지 원칙)."""
import time
from datetime import datetime

import pandas as pd
import pytest

import app


def _patch_market_open(monkeypatch, is_open: bool):
    monkeypatch.setattr(app, "_is_market_open_now", lambda is_kr: is_open)


def _make_df(last_date: str, n=5):
    dates = pd.bdate_range(end=last_date, periods=n)
    price = [100.0] * n
    return pd.DataFrame({"Open": price, "High": price, "Low": price, "Close": price,
                          "Volume": [1000.0] * n}, index=dates)


def test_price_stale_minutes_derived_from_reuse_ttl():
    assert app.PRICE_STALE_MINUTES == app.REUSE_TTL // 60 // 2
    assert app.PRICE_STALE_MINUTES == 15


# ---------------------------------------------------------------------------
# 장중
# ---------------------------------------------------------------------------

def test_market_open_fresh_no_note(monkeypatch):
    _patch_market_open(monkeypatch, True)
    data_ts_val = time.time() - 5 * 60   # 5분 전 — 임계값(15분) 미만
    note, live = app._price_staleness_fields(_make_df("2026-09-11"), data_ts_val, is_kr=True)
    assert note is None
    assert live is False


def test_market_open_stale_shows_note(monkeypatch):
    _patch_market_open(monkeypatch, True)
    data_ts_val = time.time() - 20 * 60   # 20분 전 — 임계값(15분) 초과
    note, live = app._price_staleness_fields(_make_df("2026-09-11"), data_ts_val, is_kr=True)
    assert note is not None
    assert "📸" in note and "기준" in note and "종가" not in note
    assert live is True


def test_market_open_just_under_threshold_no_note(monkeypatch):
    """15분에 살짝 못 미치면(14분59초) 안 떠야 한다. 정확히 threshold와
    같은 순간(부동소수점 등호)은 실행 시점의 미세한 시간 경과 때문에
    테스트에서 안정적으로 재현 불가능해 1초 여유를 둔다 — 이 앱의
    목적상(연속적인 시간 흐름) 정확한 등호 경계 자체가 의미 있는
    지점도 아니다(_GAP_TRUNCATE_MIN_RUN처럼 이산적인 "일수 카운트"
    경계와는 성격이 다름)."""
    _patch_market_open(monkeypatch, True)
    data_ts_val = time.time() - (15 * 60 - 1)
    note, live = app._price_staleness_fields(_make_df("2026-09-11"), data_ts_val, is_kr=True)
    assert note is None
    assert live is False


def test_market_open_just_over_threshold_shows_note(monkeypatch):
    """15분을 살짝 넘으면(15분1초) 떠야 한다 — 위 테스트와 짝으로,
    비교 방향(< vs >)이 뒤집히는 사보타지를 같이 잡는다(실제 확인:
    비교 방향을 뒤집으면 이 두 테스트가 정확히 FAIL함)."""
    _patch_market_open(monkeypatch, True)
    data_ts_val = time.time() - (15 * 60 + 1)
    note, live = app._price_staleness_fields(_make_df("2026-09-11"), data_ts_val, is_kr=True)
    assert note is not None
    assert live is True


def test_market_open_missing_data_ts_no_note(monkeypatch):
    """data_ts가 없으면(구형 캐시 등) 크래시 없이 조용히 표기 생략."""
    _patch_market_open(monkeypatch, True)
    note, live = app._price_staleness_fields(_make_df("2026-09-11"), None, is_kr=True)
    assert note is None
    assert live is False


# ---------------------------------------------------------------------------
# 장마감 — 항상 표시, data_ts가 아니라 df 마지막 봉 날짜 기준.
# ---------------------------------------------------------------------------

def test_market_closed_always_shows_note_even_when_data_ts_fresh(monkeypatch):
    """★ 핵심 재검토 사항. 장마감 후엔 data_ts가 방금이어도(0분 전)
    표기가 떠야 한다 — "오늘 종가인지 어제 종가인지"는 fetch 시각과
    무관하게 항상 필요한 정보(KST 22시 KR 카드 조회 혼란 실사례)."""
    _patch_market_open(monkeypatch, False)
    data_ts_val = time.time()   # 방금 fetch — 그래도 떠야 함
    note, live = app._price_staleness_fields(_make_df("2026-09-11"), data_ts_val, is_kr=True)
    assert note is not None
    assert "📸" in note and "종가 기준" in note
    assert live is False   # 장마감 후엔 재조회 트리거 아님


def test_market_closed_uses_last_bar_date_not_data_ts(monkeypatch):
    """★ 핵심. 날짜 소스가 data_ts(fetch 시각)가 아니라 df의 마지막
    봉 날짜여야 한다 — 다음날 개장 전 fetch해도 데이터는 여전히
    전날 종가일 수 있어서."""
    _patch_market_open(monkeypatch, False)
    # data_ts는 9/12(다음날) 오전 오전 8시 근처로 설정해도, df의 마지막
    # 봉은 여전히 9/11(전날 종가)이라는 상황을 재현.
    fake_fetch_ts = datetime(2026, 9, 12, 8, 50, tzinfo=app.KST).timestamp()
    note, live = app._price_staleness_fields(_make_df("2026-09-11"), fake_fetch_ts, is_kr=True)
    assert note is not None
    assert "09-11" in note, f"df 마지막 봉 날짜(09-11)가 아니라 fetch 시각(09-12) 기반으로 표기됨: {note}"
    assert "09-12" not in note


def test_market_closed_data_ts_none_still_shows_note(monkeypatch):
    """장마감 후엔 data_ts가 아예 없어도(df만 있으면) 표기 가능해야 한다."""
    _patch_market_open(monkeypatch, False)
    note, live = app._price_staleness_fields(_make_df("2026-09-10"), None, is_kr=True)
    assert note is not None
    assert "09-10" in note
    assert live is False


def test_market_closed_broken_df_fails_open(monkeypatch):
    """df에서 날짜를 못 뽑으면(빈 df 등) 크래시 없이 조용히 생략."""
    _patch_market_open(monkeypatch, False)
    empty_df = pd.DataFrame()
    note, live = app._price_staleness_fields(empty_df, time.time(), is_kr=True)
    assert note is None
    assert live is False
