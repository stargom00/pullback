"""측정 실행 시각 규칙(사용자 결정 2026-10-09) — harness.run_window_ok 경계 고정.

"KR 데이터만 쓰는 측정: KR 종가 확정(20:10 KST) 이후 ~ 다음 KR 거래일 08:00 KST 전. 주말·휴장일은 종일.
 US 데이터를 쓰는 측정: 기존대로 KST 20:10~22:30(US 장중 회피)."
2026-10-08(목) 거래일 · 10-09(금) 한글날 휴장 · 10-10·11 주말 · 10-12(월) 거래일.

사보타주 확인(2026-10-09, FAIL 확인 후 원복): 휴장일 종일 허용 분기 제거 → test_kr_only_window FAIL
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "scripts", "measurements"))
import harness  # noqa: E402

KST = timezone(timedelta(hours=9))


def _k(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=KST)


@pytest.mark.parametrize("t,ok", [
    ("2026-10-08 12:00", False), ("2026-10-08 20:09", False), ("2026-10-08 20:10", True), ("2026-10-08 23:59", True),
    ("2026-10-08 07:59", True), ("2026-10-08 08:00", False),          # 거래일 아침: 전 거래일 확정 뒤 ~ 08:00 전
    ("2026-10-09 00:00", True), ("2026-10-09 12:00", True),           # 한글날 휴장 — 종일
    ("2026-10-10 15:00", True), ("2026-10-11 21:00", True),           # 주말 — 종일
    ("2026-10-12 07:59", True), ("2026-10-12 08:00", False), ("2026-10-12 20:10", True),
])
def test_kr_only_window(t, ok):
    assert harness.run_window_ok(("kr",), _k(t))[0] is ok


@pytest.mark.parametrize("t,ok", [
    ("2026-10-08 20:09", False), ("2026-10-08 20:10", True), ("2026-10-08 22:29", True), ("2026-10-08 22:30", False),
    ("2026-10-09 12:00", False), ("2026-10-09 21:00", True),          # US 포함은 휴장일에도 20:10~22:30만(기존 규칙)
])
def test_us_window(t, ok):
    assert harness.run_window_ok(("kr", "us"), _k(t))[0] is ok
    assert harness.run_window_ok(("us",), _k(t))[0] is ok


def test_constants_shared_not_copied():
    import app
    import inspect
    src = inspect.getsource(harness.run_window_ok)
    assert "app.KR_CLOSE_CONFIRMED_HM" in src and "app.is_trading_day" in src and "1210" not in src
    assert harness.KR_MEASURE_CUTOFF_MIN == 8 * 60 and harness.US_OPEN_MIN == 22 * 60 + 30
    ma99 = open(os.path.join(ROOT, "scripts", "measurements", "2026-10-09_ma99_breakout_retest.py"), encoding="utf-8").read()
    assert "harness.check_run_window(RUN_MARKETS)" in ma99 and 'RUN_MARKETS = ("kr",)' in ma99 and "RUN_WINDOW_KST" not in ma99
