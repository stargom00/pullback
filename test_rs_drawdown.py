"""v5.385 지수 하락 구간 상대강도 — rs_drawdown + app.rs_drawdown_scan.

실데이터 형태: 스캐너 bundle의 KR df는 float32 OHLCV, 지수는 naver fetch_index_history의 Close(float). 종목 중 고점일 이후 상장,
50봉 미만이 섞인다(→ None, n_short_history로 센다).

사보타주 확인(2026-11-19, FAIL 확인 후 원복): ① low_ok의 < 를 <= 로 ② 정렬 방향 반대 ③ 종목 df 확정 봉 자르기 제거 — 셋 다 FAIL.
"""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

import app
import rs_drawdown as rd

KST = timezone(timedelta(hours=9))
END = "2026-11-18"


def _ser(vals, end=END):
    return pd.Series(np.array(vals, dtype=float), index=pd.bdate_range(end=end, periods=len(vals)))


def _df(vals, end=END):
    c = np.array(vals, dtype=float)
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1e5},
                        index=pd.bdate_range(end=end, periods=len(c))).astype("float32")


# 지수: 100봉 평탄 100 → 10봉 올라 110(고점) → 15봉 빠져 88(저점, −20%) → 10봉 반등 96
IDX = [100.0] * 100 + list(np.linspace(101, 110, 10)) + list(np.linspace(108, 88, 15)) + list(np.linspace(89, 96, 10))


def test_index_window():
    w = rd.index_window(_ser(IDX))
    assert abs(w["peak"] - 110) < 1e-9 and abs(w["trough"] - 88) < 1e-9
    assert abs(w["dd"] - (88 / 110 - 1)) < 1e-9 and abs(w["reb"] - (96 / 88 - 1)) < 1e-9
    assert w["meaningful"] is True
    assert rd.index_window(_ser([100.0] * 70 + [101.0]))["meaningful"] is False     # 오늘이 고점 → dd 0


def test_stock_rows_order_and_flags():
    w = rd.index_window(_ser(IDX))
    # 강한 종목: 지수 고점 때 110 → 지수 저점일에 104(−5.5%), 최저는 그 전(100) — 지수 신저가 때 저점 안 깸, 50일선 위
    strong = [100.0] * 100 + list(np.linspace(101, 110, 10)) + list(np.linspace(108, 100, 8)) + list(np.linspace(101, 104, 7)) + list(np.linspace(105, 115, 10))
    # 약한 종목: 지수와 같이 −30%, 지수 저점일에 최저
    weak = [100.0] * 100 + list(np.linspace(101, 110, 10)) + list(np.linspace(108, 77, 15)) + list(np.linspace(78, 80, 10))
    s, k = rd.stock_row(_df(strong), w), rd.stock_row(_df(weak), w)
    assert s["rel_dd"] > 0 > k["rel_dd"]
    assert s["low_ok"] is True and k["low_ok"] is False
    assert s["ma50_ok"] is True and k["ma50_ok"] is False
    assert rd.stock_row(_df([100.0] * 30), w) is None                 # 50봉 미만
    # 고점일 이후 상장(첫 봉이 고점일보다 늦음) → 비교 불가. 길이는 50봉 이상이게 만든다(길이 때문에 None이 되지 않게).
    late = _df([100.0] * 60, end=END)
    late.index = pd.bdate_range(start=pd.Timestamp(w["peak_date"]) + pd.Timedelta(days=1), periods=60)
    assert len(late) >= rd.MA_LEN and rd.stock_row(late, w) is None

def test_scan_end_to_end_sorted_and_market_split():
    now = datetime(2026, 11, 19, 21, 0, tzinfo=KST)                   # 11-19 KST 21:00 → 11-19 확정(데이터는 11-18까지)
    up = [100.0] * 100 + list(np.linspace(101, 110, 10)) + list(np.linspace(108, 104, 15)) + list(np.linspace(104, 112, 10))
    dn = [100.0] * 100 + list(np.linspace(101, 110, 10)) + list(np.linspace(108, 70, 15)) + list(np.linspace(70, 72, 10))
    data = {"000001.KS": _df(up), "000002.KQ": _df(dn), "000003.KQ": _df(up), "000004.KS": _df([100.0] * 20)}
    uni = {"000001.KS": "강한코스피", "000002.KQ": "약한코스닥", "000003.KQ": "강한코스닥", "000004.KS": "신규"}
    idx = {"KOSPI": _ser(IDX), "KOSDAQ": _ser([x * 9 for x in IDX])}  # 수준만 다르고 모양 같음 → 같은 dd
    b = app.rs_drawdown_scan(data, uni, idx, now)
    names = [r["name"] for r in b["top"]]
    assert names[-1] == "약한코스닥" and set(names[:2]) == {"강한코스피", "강한코스닥"}
    assert [r["mkt"] for r in b["top"] if r["name"] == "약한코스닥"] == ["KOSDAQ"]
    assert b["n_short_history"] == 1 and b["n_checked"] == 3
    assert set(b["index"]) == {"KOSPI", "KOSDAQ"} and "reason" not in b


def test_scan_reasons_not_silent():
    now = datetime(2026, 11, 19, 21, 0, tzinfo=KST)
    assert app.rs_drawdown_scan({}, {}, {}, now)["reason"] == "no_index"
    assert app.rs_drawdown_scan({}, {}, {"KOSPI": _ser(IDX)}, now)["reason"] == "no_rows"


def test_unconfirmed_today_bar_cut():
    """KST 15:00(애프터 전)엔 오늘 봉이 확정 전 → 잘라야 한다. 오늘 봉에서 폭락한 종목이 폭락 전 값으로 계산돼야."""
    now = datetime(2026, 11, 18, 15, 0, tzinfo=KST)
    base = [100.0] * 100 + list(np.linspace(101, 110, 10)) + list(np.linspace(108, 100, 15)) + list(np.linspace(100, 104, 10))
    crash = base[:-1] + [50.0]                                          # 11-18(미확정)에 반토막
    b = app.rs_drawdown_scan({"000001.KS": _df(crash)}, {"000001.KS": "x"}, {"KOSPI": _ser(IDX)}, now)
    assert b["session_date"] < END and b["top"][0]["close"] > 90


def test_sector_table():
    rows = [{"sector": "반도체", "rel_dd": v} for v in (0.1, 0.2, 0.3)] + [{"sector": "은행", "rel_dd": 0.5}] * 2 + [{"sector": "기타", "rel_dd": 0.9}] * 5
    t = rd.sector_table(rows)
    assert [x["sector"] for x in t] == ["반도체"] and abs(t[0]["rel_dd_med"] - 0.2) < 1e-9


def test_bot_read_path_open():
    assert "/api/rs-drawdown" in app._BOT_READ_EXACT_PATHS
