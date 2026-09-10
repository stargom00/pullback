"""_signal_snapshots 영구 고정 결함 — 1단계 (v5.238, 사용자 지시).

배경: 실측(728건, 이 세션 조사)에서 괴리 3%+ 80건 중 70건(87.5%)이
`last_seen_date < 오늘`이었다 — 즉 그 종목이 해당 탭의 스캔 히트에서
빠진 뒤로는 `_record_signal_snapshot()`이 다시 호출되지 않아(app.py
docstring: "run_scan()의 게이트형 히트 적재 시점에서만 호출") 리셋 조건
(a)(5거래일 공백)(b)(pivot 3%+ 변동) 자체가 평가되지 않는다 — 재등장
없이는 영원히 얼어붙는다.

이 파일은 "1단계: 결함을 고정하는 테스트부터" — 수정 이전 코드에서
먼저 실행해 (a) 스캔 히트에서 빠진 채로 시간이 지나도 스냅샷이 살아있는
결함이 실제로 재현되는지 확인한다. FAIL 안 하면(=이미 만료됐으면) 이
테스트 자체가 잘못 짜인 것이므로 보고 후 중단해야 한다(사용자 지시)."""
import sys
from datetime import datetime, timedelta

import pytest

import app

KST = app.KST


def _days_ago_str(n: int) -> str:
    return (datetime.now(KST) - timedelta(days=n)).strftime("%Y-%m-%d")


@pytest.fixture
def isolated_snapshots(monkeypatch):
    """전역 _signal_snapshots를 격리 — 실제 프로덕션에서 pull한
    signal_snapshot_cache.json이 로컬에 있어도(별도 투자 세션 산출물,
    .gitignore 처리됨) 이 테스트가 그 실데이터를 건드리거나 그로부터
    오염되지 않게 한다."""
    fresh = {}
    monkeypatch.setattr(app, "_signal_snapshots", fresh)
    return fresh


def test_stale_snapshot_still_alive_without_rehit(isolated_snapshots):
    """★ 결함 고정 테스트. 스캔 히트에서 완전히 빠진(last_seen_date가
    아주 오래전에 멈춘) 종목의 스냅샷을 만들고, get_signal_snapshot()을
    호출했을 때 만료 처리(None) 되는지 확인한다.

    30 캘린더일 전으로 anchor를 잡는 이유: SIGNAL_SNAPSHOT_RESET_GAP_DAYS
    (5거래일)를 어떤 실행 시점(주말/공휴일 포함)에도 확실히 넘기기
    위함 — 실제 거래일 계산(is_trading_day) 없이도 "5거래일보다 훨씬
    오래 지났다"를 결정론적으로 보장.

    수정 전: 이 assert는 FAIL해야 한다(스냅샷이 여전히 살아있음 = 결함
    재현). FAIL 안 하면(이미 None이면) 테스트가 잘못 짜인 것 — 그 경우
    보고 후 중단."""
    key = "TESTTICK|돌파임박"
    isolated_snapshots[key] = {
        "signal_date": _days_ago_str(30),
        "stop": 900.0,
        "pivot": 1000.0,
        "signal_high": 1000.0,
        "signal_low": 890.0,
        "base_vol50": 12345.0,
        "last_seen_date": _days_ago_str(30),   # 그날 이후로 단 한 번도 재히트 안 됨
    }
    snap = app.get_signal_snapshot("TESTTICK", "돌파임박")
    assert snap is None, (
        "만료됐어야 할 30일 전 스냅샷이 여전히 살아있음(None 아님) — "
        "만료 메커니즘 미구현 상태(수정 전 정상 결과, 결함 재현됨)"
    )


def test_fresh_snapshot_still_alive(isolated_snapshots):
    """대조군 — 방금 기록된(last_seen_date=오늘) 스냅샷은 만료되면 안
    된다. 만료 메커니즘이 "무조건 None"처럼 너무 공격적으로 구현되는
    회귀를 막기 위한 가드레일."""
    key = "TESTTICK2|돌파임박"
    isolated_snapshots[key] = {
        "signal_date": _days_ago_str(1),
        "stop": 900.0, "pivot": 1000.0, "signal_high": 1000.0, "signal_low": 890.0,
        "base_vol50": 12345.0,
        "last_seen_date": _days_ago_str(0),
    }
    snap = app.get_signal_snapshot("TESTTICK2", "돌파임박")
    assert snap is not None, "방금(오늘) 관측된 스냅샷이 잘못 만료됨"
    assert snap["pivot"] == 1000.0
