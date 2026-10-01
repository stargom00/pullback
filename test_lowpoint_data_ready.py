"""v5.309 — US 데이터 준비 선체크(기준 3종목) + `data_not_ready` 상태 구분.

배경(2026-10-01 조사): 10:48 KST 월봉 실행에서 US 15종목이 "정지추정"으로 탈락했는데
SITC·ADEA·HUBB·SHEL·UA·WLY 등 **대형주**가 섞여 있었다 — 거래정지가 아니라 야후가 09-30
일봉을 아직 안 올린 것이었다(12:02 실행 4종목, 13시 0종목). 월말·주말 직후 실행이
비재현적이고 그 사실이 "정지추정 제외"로 조용히 묻히던 문제다. KR엔 확정 시각 규칙
(KR_CLOSE_CONFIRMED_HM)이 있었지만 US엔 없었다.

설계: US 파트 **시작 전에** AAPL·MSFT·NVDA의 최신 일봉이 목표 거래일에 도달했는지 보고,
미달이면 `DataNotReady`로 이번 시도를 실패시킨다 → 기존 재시도 규칙(60분×3, KR 장중 차단)
재사용. 새 대기시간·임계값 없음. "정지추정" 로직 자체는 **변경하지 않았다**.

사보타주 확인(2026-10-01, 전부 FAIL 확인 후 원복):
① `screen_market`의 US 분기에서 `check_us_data_ready` 호출 제거 → 2건 FAIL
② `check_us_data_ready`가 미달이어도 통과(raise 제거) → 4건 FAIL
③ `_maybe_run_lowpoint`가 data_not_ready를 "failed"로만 기록 → 2건 FAIL
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

import app

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_ROOT, "scripts", "screens"))
import lowpoint as lp  # noqa: E402

KST = timezone(timedelta(hours=9))


def _k(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=KST)


def _df(last_day: str) -> pd.DataFrame:
    idx = pd.bdate_range(end=pd.Timestamp(last_day), periods=20)
    return pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 1},
                        index=idx)


# ── 목표 거래일 계산 ────────────────────────────────────────────────

def test_expected_session_uses_injected_trading_calendar():
    """2027-05-31은 메모리얼데이(월말 공휴일) — 목표는 그 전 거래일 05-28이어야 한다.
    휴장일 판정은 app.is_trading_day를 **주입**받아 쓴다(사본 금지)."""
    assert lp.expected_us_session(date(2027, 5, 31), app.is_trading_day) == date(2027, 5, 28)
    # 폴백(주말만 보는 CLI 경로)은 공휴일을 모른다 — 그 한계를 고정해둔다
    assert lp.expected_us_session(date(2027, 5, 31)) == date(2027, 5, 31)


def test_expected_session_walks_back_over_weekend():
    assert lp.expected_us_session(date(2026, 10, 3), app.is_trading_day) == date(2026, 10, 2)
    assert lp.expected_us_session(date(2026, 10, 4)) == date(2026, 10, 2)


def test_expected_session_keeps_trading_day():
    assert lp.expected_us_session(pd.Timestamp("2026-09-30"), app.is_trading_day) == date(2026, 9, 30)


# ── 선체크 판정 ─────────────────────────────────────────────────────

def test_data_ready_passes_when_all_reference_tickers_current(monkeypatch):
    import harness
    monkeypatch.setattr(harness, "_fetch_us_batch",
                        lambda tickers, period="2y": {t: _df("2026-09-30") for t in tickers})
    got = lp.check_us_data_ready("month", pd.Timestamp("2026-09-30"), app.is_trading_day)
    assert got["expected"] == "2026-09-30"
    assert set(got["seen"]) == set(lp.US_DATA_CHECK_TICKERS)


def test_data_not_ready_when_one_reference_ticker_lags(monkeypatch):
    """실제 사고 재현 — 09-30 봉이 아직 안 올라온 상태."""
    import harness

    def fake(tickers, period="2y"):
        return {t: _df("2026-09-29" if t == "NVDA" else "2026-09-30") for t in tickers}

    monkeypatch.setattr(harness, "_fetch_us_batch", fake)
    with pytest.raises(lp.DataNotReady) as ei:
        lp.check_us_data_ready("month", pd.Timestamp("2026-09-30"), app.is_trading_day)
    assert "NVDA=2026-09-29" in str(ei.value) and "2026-09-30" in str(ei.value)


def test_data_not_ready_when_all_lag(monkeypatch):
    import harness
    monkeypatch.setattr(harness, "_fetch_us_batch",
                        lambda tickers, period="2y": {t: _df("2026-09-29") for t in tickers})
    with pytest.raises(lp.DataNotReady):
        lp.check_us_data_ready("week", pd.Timestamp("2026-09-30"), app.is_trading_day)


def test_data_not_ready_when_reference_ticker_missing(monkeypatch):
    """기준 종목 자체가 안 오면(차단·장애) 통과시키지 않는다 — 조용한 진행 금지."""
    import harness
    monkeypatch.setattr(harness, "_fetch_us_batch",
                        lambda tickers, period="2y": {t: _df("2026-09-30")
                                                     for t in tickers if t != "AAPL"})
    with pytest.raises(lp.DataNotReady) as ei:
        lp.check_us_data_ready("month", pd.Timestamp("2026-09-30"), app.is_trading_day)
    assert "AAPL=None" in str(ei.value)


def test_reference_tickers_are_the_three_megacaps():
    assert lp.US_DATA_CHECK_TICKERS == ("AAPL", "MSFT", "NVDA")


# ── 선체크가 US 경로에 실제로 걸려 있는가 ───────────────────────────

def test_screen_market_us_runs_precheck_before_fetch(monkeypatch):
    """선체크가 실패하면 유니버스·일봉 조회를 **시작하지 않아야** 한다
    (지연 상태로 5,618종목을 받아봤자 결과가 비재현적이다)."""
    calls = []
    monkeypatch.setattr(lp, "check_us_data_ready",
                        lambda *a, **k: (_ for _ in ()).throw(lp.DataNotReady("미도착")))
    monkeypatch.setattr(lp, "us_universe", lambda **k: calls.append("universe") or ({}, {}))
    monkeypatch.setattr(lp, "fetch_us", lambda *a, **k: calls.append("fetch") or ({}, [], {}))
    with pytest.raises(lp.DataNotReady):
        lp.screen_market("us", "month", _k("2026-10-01 10:48"))
    assert calls == [], f"선체크 실패인데 {calls}가 실행됐다"


def test_screen_market_kr_does_not_run_us_precheck(monkeypatch):
    """KR 파트는 기존대로 — 선체크를 타지 않는다(사용자 지시)."""
    monkeypatch.setattr(lp, "check_us_data_ready",
                        lambda *a, **k: pytest.fail("KR이 US 선체크를 호출했다"))
    monkeypatch.setattr(lp, "kr_universe", lambda board: ({"005930.KS": "삼성전자"},
                                                         {"admin_excluded": {}, "kind_total": 1,
                                                          "kind_rows": 1, "admin_snapshot": 1}))
    monkeypatch.setattr(lp, "fetch_kr", lambda *a, **k: ({}, []))
    res = lp.screen_market("kospi", "month", _k("2026-10-01 10:48"))
    assert res["market"] == "kospi"


def test_stale_filter_logic_untouched():
    """"정지추정" 제외 로직은 변경 금지 대상 — 최빈 거래일 비교가 그대로 있는지 확인."""
    src = open(os.path.join(_ROOT, "scripts", "screens", "lowpoint.py"), encoding="utf-8").read()
    body = src.split("def screen_market(")[1].split("\ndef ")[0]
    assert "last_dates = Counter(c.index[-1].normalize() for c in data.values())" in body
    assert "stale = {t: str(c.index[-1].date()) for t, c in data.items()" in body


# ── data_not_ready 상태 기록·재시도 ─────────────────────────────────

def _run(job, now, state_path, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "LOWPOINT_STATE_PATH", str(state_path))
    monkeypatch.setattr(app, "_lowpoint_running", False)
    return asyncio.run(app._maybe_run_lowpoint(now, _job=job))


def test_data_not_ready_recorded_distinctly(tmp_path, monkeypatch, capsys):
    def job(tf, now):
        raise lp.DataNotReady("US 일봉 미도착 — 목표 거래일 2026-09-30, 기준 종목 NVDA=2026-09-29")

    rec = _run(job, _k("2026-10-01 08:00"), tmp_path / "state.json", tmp_path, monkeypatch)
    assert rec["status"] == "data_not_ready", "일반 실패와 구분돼야 한다"
    assert "DataNotReady" in rec["error"]
    out = capsys.readouterr().out
    assert "데이터 미도착" in out and "rss" in out


def test_other_failures_still_recorded_as_failed(tmp_path, monkeypatch):
    def job(tf, now):
        raise RuntimeError("네이버 응답 0건")

    rec = _run(job, _k("2026-10-01 08:00"), tmp_path / "state.json", tmp_path, monkeypatch)
    assert rec["status"] == "failed"


def test_data_not_ready_is_retried_under_existing_rules():
    """새 대기시간을 만들지 않았다 — 기존 규칙(60분 간격·KR 장중 차단·최대 3회)을 따른다."""
    st = {"month": {"target": "2026-09-30", "status": "data_not_ready", "attempts": 1,
                    "started_at": "2026-10-01T08:00:00+09:00"}}
    assert app._lowpoint_due("month", _k("2026-10-01 08:30"), st) is None   # 간격 미경과
    assert app._lowpoint_due("month", _k("2026-10-01 10:00"), st) is None   # KR 장중
    assert app._lowpoint_due("month", _k("2026-10-01 15:40"), st) == "2026-09-30"
    st["month"]["attempts"] = app.LOWPOINT_MAX_ATTEMPTS
    assert app._lowpoint_due("month", _k("2026-10-01 20:00"), st) is None   # 소진


def test_error_class_lookup_matches_lowpoint():
    assert app._lowpoint_data_not_ready_error() is lp.DataNotReady


def test_no_new_wait_constants_introduced():
    """선체크 때문에 새 대기시간/임계값을 만들지 않았는지(사용자 지시)."""
    src = open(os.path.join(_ROOT, "app.py"), encoding="utf-8").read()
    names = [l.split("=")[0].strip() for l in src.splitlines()
             if l.startswith("LOWPOINT_") and "=" in l]
    assert set(names) == {
        "LOWPOINT_DATA_PATH", "LOWPOINT_STATE_PATH", "LOWPOINT_US_LISTINGS_PATH",
        "LOWPOINT_SCHEDULE_HM", "LOWPOINT_RETRY_MIN", "LOWPOINT_MAX_ATTEMPTS",
        "LOWPOINT_RUNNING_STALE_MIN", "LOWPOINT_US_LISTINGS_MAX_AGE_DAYS",
        "LOWPOINT_CATCHUP_HOURS", "LOWPOINT_RETRY_BLOCK_HM",
        "LOWPOINT_LATEST_PATH", "LOWPOINT_TFS",   # v5.293 표시용(레포 폴백 경로·tf 목록)
    }, f"저점 상수 집합이 바뀌었다: {sorted(names)}"
