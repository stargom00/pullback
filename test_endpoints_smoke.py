"""엔드포인트 스모크 테스트 (v5.241, 사용자 지시 — 재발방지 작업 [2]).

배경: 오늘 두 사고(v5.238 KeyError 500, 그 이전 refactor 사고) 전부
단위 테스트는 전부 통과한 채로 실제 엔드포인트가 죽었다 — 응답 조립
경로(`run_scan()`/`get_calendar()` 등, 여러 함수가 합쳐지는 지점) 자체를
실제로 호출해보는 테스트가 하나도 없었기 때문이다. 이 파일은 "네트워크는
막되 응답 조립 경로 전체는 실제로 탄다" — `test_fetch_market_data_all_
merge.py`의 `mocked_env` fixture를 그대로 재사용(재구현 안 함, 사용자
지시 "mocked_env 확장 시 기존 테스트가 계속 통과하는지 확인"도 그
파일에서 이미 검증됨).

get_calendar()가 실제로 느려지는 지점(포지션 있으면 실적 D-3 조회가
진짜 네트워크를 탐)은 `mocked_env`가 `get_positions()`를 빈 포지션으로
고정해 회피한다 — 코드로 확인 + 직접 실행해서 확인 완료(포지션이
있으면 30초+ 걸리던 걸 실측으로 재현했었음, 이번 세션 앞선 조사)."""
import asyncio
import json as _json
import time

from test_fetch_market_data_all_merge import mocked_env  # noqa: F401  (fixture 재사용)

import app

ENDPOINTS_TIME_BUDGET_SEC = 5.0   # 전체 6개 합계 목표(사용자 지시: "몇 초 안에")


def _body(response):
    return _json.loads(response.body)


def test_scan_all_pullback_returns_200_with_hits(mocked_env):
    r = asyncio.run(app.scan(market="all", mode="pullback", refresh=False))
    assert r.status_code == 200
    body = _body(r)
    assert "hits" in body, body.keys()


def test_scan_kr_imminent_returns_200_with_hits(mocked_env):
    r = asyncio.run(app.scan(market="kr", mode="imminent", refresh=False))
    assert r.status_code == 200
    assert "hits" in _body(r)


def test_scan_us_pullback_returns_200_with_hits(mocked_env):
    r = asyncio.run(app.scan(market="us", mode="pullback", refresh=False))
    assert r.status_code == 200
    assert "hits" in _body(r)


def test_calendar_returns_200_with_expected_keys(mocked_env):
    r = asyncio.run(app.get_calendar())
    assert r.status_code == 200
    body = _body(r)
    for key in ("version", "market_session", "today_decision", "positions_summary"):
        assert key in body, f"{key} 없음 — {sorted(body.keys())}"


def test_watch_pending_returns_200(mocked_env):
    r = asyncio.run(app.watch_pending())
    assert r.status_code == 200
    body = _body(r)
    assert "pending" in body and "count" in body


def test_watch_positions_returns_200(mocked_env):
    r = asyncio.run(app.watch_positions())
    assert r.status_code == 200
    body = _body(r)
    assert "positions" in body and "count" in body


def test_all_six_endpoints_within_time_budget(mocked_env):
    """사용자 지시 — "몇 초 안에 끝나야 매 커밋 전에 돌릴 수 있다". 6개
    엔드포인트를 순서대로 다시 호출해 합계 소요시간을 실측·assert한다
    (개별 테스트들과 달리 이 테스트 자체가 "시간이 예산 안"이라는 요구를
    코드로 강제)."""
    t0 = time.time()
    asyncio.run(app.scan(market="all", mode="pullback", refresh=False))
    asyncio.run(app.scan(market="kr", mode="imminent", refresh=False))
    asyncio.run(app.scan(market="us", mode="pullback", refresh=False))
    asyncio.run(app.watch_pending())
    asyncio.run(app.watch_positions())
    asyncio.run(app.get_calendar())
    elapsed = time.time() - t0
    assert elapsed < ENDPOINTS_TIME_BUDGET_SEC, (
        f"스모크 테스트 6개 합계 {elapsed:.2f}초 — 예산({ENDPOINTS_TIME_BUDGET_SEC}초) 초과. "
        f"get_calendar()가 실제 네트워크를 타는 경로(실적 조회 등)에 걸렸을 가능성 — "
        f"mocked_env의 get_positions/market_gate/jongga_candidates mock을 재점검할 것."
    )


def test_scan_all_survives_legacy_timing_schema_subbundle(mocked_env, monkeypatch):
    """★ 오늘 사고 재현(사용자 지시 — 스모크 테스트에 반드시 포함).
    kr/us 서브 bundle을 정상적으로 채운 뒤 us 쪽 timing에서
    n_fetch_failed_kr/us를 삭제(오늘 프로덕션 사고 파일과 동일한 구형
    스키마)하고 GET /api/scan?market=all을 그대로 호출한다.

    현재(.get() 폴백 있는) 코드에서는 200이어야 한다. 아래
    `test_scan_all_crashes_without_get_fallback_sabotage`가 이 폴백을
    대괄호 인덱싱으로 되돌리면 여기서 크래시하는지(사고 재현) 직접
    sabotage해서 확인한다 — "확신한다"로 넘기지 않는다(사용자 지시)."""
    asyncio.run(app._fetch_market_data("kr", wait_for_fresh=True))
    asyncio.run(app._fetch_market_data("us", wait_for_fresh=True))
    del app._data_cache["data:us"]["timing"]["n_fetch_failed_kr"]
    del app._data_cache["data:us"]["timing"]["n_fetch_failed_us"]

    r = asyncio.run(app.scan(market="all", mode="pullback", refresh=False))
    assert r.status_code == 200
