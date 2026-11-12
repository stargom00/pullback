"""v5.381 KR 전체 120·240일선 상향 돌파 — ma_cross.cross_up + app.ma_cross_scan(장중·마감·휴장·스팩).

사보타주 확인(2026-11-12, FAIL 확인 후 원복):
  ① cross_up의 c[-2] < ma_y 를 <= 로 → test_equality_boundaries FAIL
  ② close 모드의 confirmed_through 자르기 제거 → test_close_mode_ignores_unconfirmed_today FAIL
  ③ 장중 거래량 배수의 elapsed 환산 제거 → test_intraday_vol_scaled FAIL
"""
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

import app
import ma_cross as mc

KST = timezone(timedelta(hours=9))
FIX = os.path.join(os.path.dirname(__file__), "test_fixtures", "nvda_daily_250602_261110.txt")


def real_df():
    rows = [x.split(",") for x in open(FIX).read().strip().split(";")]
    idx = pd.DatetimeIndex([pd.Timestamp(2000 + int(r[0][:2]), int(r[0][2:4]), int(r[0][4:6])) for r in rows])
    vals = [[int(r[k]) / 100 for k in (1, 2, 3, 4)] + [float(r[5])] for r in rows]
    return pd.DataFrame(vals, index=idx, columns=["Open", "High", "Low", "Close", "Volume"]).astype("float32")


def test_real_cross_dates_match_bot_definition():
    df = real_df()
    hits = [(str(df.index[i - 1].date()), w) for i in range(242, len(df) + 1) for w in mc.WINDOWS if mc.cross_up(df.iloc[:i], w)]
    # 얼마냐봇 v2.58 test_ma_cross.py와 같은 집합(같은 데이터·같은 정의 — 두 레포 정의가 어긋나면 여기서 갈린다)
    assert hits == [("2026-06-29", 120), ("2026-07-06", 120), ("2026-07-30", 240),
                    ("2026-07-31", 120), ("2026-10-19", 120), ("2026-10-26", 120)]


def _frame(closes, end="2026-11-10", vols=None, dtype="float32"):
    idx = pd.bdate_range(end=end, periods=len(closes))
    c = np.array(closes, dtype=float)
    v = np.array(vols if vols is not None else [100_000.0] * len(closes))
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": v}, index=idx).astype(dtype)


def test_equality_boundaries():
    base = [120.0] * 120 + [1.0]                # 어제 1 < 어제 이평. 오늘 x = (118×120 + 1)/119 = 119 = 오늘 120일선(정확히 표현되는 값)
    x = 119.0
    f = _frame(base + [x], dtype="float64")     # 정확히 같은 값을 만들려고 float64(실데이터 float32 경로는 위 실데이터 테스트가 본다)
    assert f["Close"].iloc[-1] == pytest.approx(f["Close"].iloc[-120:].mean(), abs=1e-12)
    assert mc.cross_up(f, 120)
    assert not mc.cross_up(_frame([10.0] * 121 + [11.0], dtype="float64"), 120)   # 어제 종가 = 어제 이평 → 아래가 아님
    assert mc.cross_up(_frame([10.0] * 120), 120) is None         # 봉 w+1개 미만


def _cross_frame(end, vols=None):
    return _frame([10_000.0] * 239 + [9_000.0, 10_600.0], end=end, vols=vols)


def test_close_mode_ignores_unconfirmed_today():
    d = {"A.KQ": _cross_frame("2026-11-10")}
    r = app.ma_cross_scan(d, {"A.KQ": "에이"}, "close", datetime(2026, 11, 10, 18, 0, tzinfo=KST))
    assert r["session_date"] == "2026-11-09" and r["n_hits"] == {"120": 0, "240": 0}
    r = app.ma_cross_scan(d, {"A.KQ": "에이"}, "close", datetime(2026, 11, 10, 20, 20, tzinfo=KST))
    assert r["session_date"] == "2026-11-10" and r["n_hits"] == {"120": 1, "240": 1}
    assert r["hits"]["240"][0]["ticker"] == "A.KQ" and r["hits"]["240"][0]["name"] == "에이"


def test_spac_and_stale_and_short():
    d = {"A.KQ": _cross_frame("2026-11-10"), "B.KQ": _cross_frame("2026-11-10"),
         "C.KS": _cross_frame("2026-11-06"), "D.KQ": _frame([1.0] * 50, end="2026-11-10"),
         "E.KQ": _frame([5.0] * 241, end="2026-11-10")}      # 돌파 없음 — 세션 다수결(11-10)용
    u = {"A.KQ": "에이", "B.KQ": "하나스팩5호", "C.KS": "씨", "D.KQ": "디", "E.KQ": "이"}
    r = app.ma_cross_scan(d, u, "close", datetime(2026, 11, 10, 21, 0, tzinfo=KST))
    assert [h["ticker"] for h in r["hits"]["240"]] == ["A.KQ"]
    assert r["n_stale_excluded"] == 1 and r["n_short_history"] == 1 and r["n_checked"] == 2


def test_intraday_requires_today_bar():
    r = app.ma_cross_scan({"A.KQ": _cross_frame("2026-11-09")}, {}, "intraday", datetime(2026, 11, 10, 14, 0, tzinfo=KST))
    assert r["n_hits"] == {"120": 0, "240": 0} and r["reason"] == "no_session_bar"


def test_intraday_vol_scaled():
    vols = [100_000.0] * 240 + [100_000.0]       # 오늘 지금까지 = 하루 평균만큼, 경과 50%면 2배
    r = app.ma_cross_scan({"A.KQ": _cross_frame("2026-11-10", vols)}, {}, "intraday",
                          datetime(2026, 11, 10, 12, 15, tzinfo=KST))
    h = r["hits"]["120"][0]
    assert r["elapsed_pct"] == pytest.approx(50, abs=3) and h["vol_x"] == pytest.approx(100 / r["elapsed_pct"], rel=0.01)


def test_bot_can_read_path():
    assert "/api/ma-cross" in app._BOT_READ_EXACT_PATHS
