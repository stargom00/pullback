"""v5.379 조용하던 종목 거래량 폭발 — quiet_surge.py 판정 + app.quiet_surge_scan(장중·마감·후속·휴장).

픽스처는 실데이터 형태: 영업일 DatetimeIndex, float32 OHLCV, 거래정지 공백(달력 일수 점프), 스팩 이름.

사보타주 확인(2026-11-10, FAIL 확인 후 원복):
  ① evaluate_at의 조용함 조건(quiet > QUIET_MAX → None) 삭제 → test_busy_stock_not_quiet FAIL
  ② 장중 elapsed 환산 제거(ratio = v[i]/short) → test_intraday_scales_by_elapsed FAIL
  ③ close 모드에서 confirmed_through 자르기 제거 → test_close_mode_ignores_unconfirmed_today FAIL
  ④ 정지 공백 검사 삭제 → test_halt_resume_excluded FAIL
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts", "screens"))
import app  # noqa: E402
import quiet_surge as qs  # noqa: E402

KST = timezone(timedelta(hours=9))


def _bars(end="2026-11-10", n=120, base_vol=100_000.0, quiet_from=None, quiet_vol=40_000.0, last_vol=None,
          last_up=True, price=10_000.0):
    idx = pd.bdate_range(end=end, periods=n)
    vol = np.full(n, base_vol)
    if quiet_from is not None:
        vol[quiet_from:] = quiet_vol
    if last_vol is not None:
        vol[-1] = last_vol
    close = np.full(n, price)
    open_ = close.copy()
    high = close * 1.01
    low = close * 0.99
    if last_up:
        open_[-1], close[-1], high[-1], low[-1] = price, price * 1.08, price * 1.09, price * 0.995
    df = pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol}, index=idx).astype("float32")
    return df


def test_quiet_then_surge_hits():
    df = _bars(quiet_from=99, last_vol=300_000)   # 직전 20봉 4만(60봉 10만의 0.4배) → 오늘 30만 = 7.5배
    h = qs.evaluate_at(df, len(df) - 1, "kr")
    assert h and round(h["surge_ratio"], 1) == 7.5 and round(h["quiet_ratio"], 2) == 0.4 and h["date"] == "2026-11-10"


def test_busy_stock_not_quiet():
    df = _bars(quiet_from=None, last_vol=800_000)   # 조용한 구간 없음(20봉 = 60봉) → 8배여도 제외
    assert qs.evaluate_at(df, len(df) - 1, "kr") is None


def test_down_day_or_weak_close_excluded():
    df = _bars(quiet_from=90, last_vol=300_000, last_up=False)
    assert qs.evaluate_at(df, len(df) - 1, "kr") is None


def test_small_value_excluded():
    df = _bars(quiet_from=90, last_vol=30_000, quiet_vol=4_000, base_vol=10_000, price=1_000)   # 거래대금 ≈ 3,240만원
    assert qs.evaluate_at(df, len(df) - 1, "kr") is None


def test_halt_resume_excluded():
    df = _bars(quiet_from=90, last_vol=300_000)
    idx = list(df.index)
    idx[-1] = idx[-2] + pd.Timedelta(days=30)        # 30일 정지 뒤 재개 첫날
    df.index = pd.DatetimeIndex(idx)
    assert qs.evaluate_at(df, len(df) - 1, "kr") is None


def test_intraday_scales_by_elapsed():
    df = _bars(quiet_from=90, last_vol=80_000)       # 하루치로는 2배지만 장 1/4 지점이면 8배 속도
    assert qs.evaluate_at(df, len(df) - 1, "kr", elapsed=1.0) is None
    assert qs.evaluate_at(df, len(df) - 1, "kr", elapsed=0.25)


def test_short_history_excluded():
    df = _bars(n=70, quiet_from=50, last_vol=300_000)
    assert qs.evaluate_at(df, len(df) - 1, "kr") is None


def test_followup_reports_next_days():
    df = _bars(quiet_from=90, last_vol=300_000)
    extra = pd.DataFrame({"Open": [10_800, 10_900], "High": [11_000, 11_000], "Low": [10_700, 10_500],
                          "Close": [10_900, 10_600], "Volume": [200_000, 60_000]},
                         index=pd.DatetimeIndex(["2026-11-11", "2026-11-12"])).astype("float32")
    df2 = pd.concat([df, extra])
    f = qs.followup(df2, len(df) - 1, "kr")
    assert [round(a["vol_x"], 1) for a in f["after"]] == [5.0, 1.5]
    assert round(f["after"][1]["close_vs_surge_pct"], 1) == round((10_600 / 10_800 - 1) * 100, 1)


def _scan(data, universe, mode, now, market="kr"):
    return app.quiet_surge_scan(data, universe, market, mode, now)


def test_close_mode_ignores_unconfirmed_today():
    """KR 20:10 전이면 오늘 봉(애프터 진행 중)은 판정에 안 쓴다 — 어제가 세션."""
    df = _bars(end="2026-11-10", quiet_from=90, last_vol=300_000)
    r = _scan({"A.KQ": df}, {"A.KQ": "에이"}, "close", datetime(2026, 11, 10, 18, 0, tzinfo=KST))
    assert r["session_date"] == "2026-11-09" and r["n_hits"] == 0
    r = _scan({"A.KQ": df}, {"A.KQ": "에이"}, "close", datetime(2026, 11, 10, 20, 20, tzinfo=KST))
    assert r["session_date"] == "2026-11-10" and r["hits"][0]["ticker"] == "A.KQ"


def test_close_mode_followups_and_spac():
    df = _bars(end="2026-11-10", quiet_from=90, last_vol=300_000)
    nxt = pd.DataFrame({"Open": [10_800], "High": [11_000], "Low": [10_700], "Close": [10_900], "Volume": [250_000]},
                       index=pd.DatetimeIndex(["2026-11-11"])).astype("float32")
    d = {"A.KQ": pd.concat([df, nxt]), "B.KQ": df.copy()}
    u = {"A.KQ": "에이", "B.KQ": "하나스팩5호"}
    r = _scan(d, u, "close", datetime(2026, 11, 11, 21, 0, tzinfo=KST))
    assert r["session_date"] == "2026-11-11"
    assert [f["ticker"] for f in r["followups"]] == ["A.KQ"] and r["followups"][0]["days_after"] == 1
    assert round(r["followups"][0]["after"][0]["vol_x"], 2) == 6.25
    assert r["n_stale_excluded"] == 0                      # 스팩은 대상에서 빠져 '오늘 봉 없음'으로 세지 않는다


def test_intraday_requires_today_bar():
    df = _bars(end="2026-11-09", quiet_from=90, last_vol=300_000)
    r = _scan({"A.KQ": df}, {"A.KQ": "에이"}, "intraday", datetime(2026, 11, 10, 10, 0, tzinfo=KST))
    assert r["n_hits"] == 0 and r["reason"] == "no_session_bar"


def test_us_intraday_uses_new_york_date_and_session():
    df = _bars(end="2026-11-10", quiet_from=90, last_vol=100_000, price=50.0)   # 하루치 2.5배, 뉴욕 10:45 = 경과 1/5.2 → 13배
    now = datetime(2026, 11, 11, 0, 45, tzinfo=KST)        # = 뉴욕 11-10 10:45 (EST)
    r = _scan({"ABCD": df}, {"ABCD": "Abcd Inc"}, "intraday", now, market="us")
    assert r["session_date"] == "2026-11-10" and r["hits"][0]["ticker"] == "ABCD" and r["elapsed_pct"] < 25


def test_bot_can_read_path():
    assert "/api/quiet-surge" in app._BOT_READ_EXACT_PATHS
