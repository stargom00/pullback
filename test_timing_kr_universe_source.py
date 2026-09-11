"""TIMING/캘린더에 kr_universe_source가 매 스캔마다 노출되는지 검증
(v5.246, 사용자 지시 — "정적 폴백 상태가 지속되는 동안은 매 스캔 TIMING에
kr_universe_source: static_fallback이 찍혀야 로그만 봐도 바로 보인다").

`app.get_kr_universe_info`(universe.py에서 import된 이름)를 monkeypatch로
고정해 실제 네트워크/모듈 전역 상태와 무관하게 결정론적으로 검증한다 —
test_kr_universe_recovery.py가 이미 이 함수 자체(universe.py 쪽) 로직을
검증하므로, 여기서는 "그 결과가 TIMING까지 정확히 전달되는지"만 본다.

mocked_env(test_fetch_market_data_all_merge.py)를 재사용 — 네트워크 완전
차단, 결정론적 픽스처."""
import asyncio

import app
from test_fetch_market_data_all_merge import mocked_env  # noqa: F401


def test_timing_kr_shows_static_fallback(mocked_env, monkeypatch):
    monkeypatch.setattr(app, "get_kr_universe_info",
                         lambda: {"source": "static_fallback", "dynamic_count": 0})
    bundle = asyncio.run(app._fetch_market_data("kr", wait_for_fresh=True))
    assert bundle is not None
    assert bundle["timing"]["kr_universe_source"] == "static_fallback"
    assert bundle["timing"]["kr_universe_dynamic_count"] == 0


def test_timing_kr_shows_dynamic(mocked_env, monkeypatch):
    monkeypatch.setattr(app, "get_kr_universe_info",
                         lambda: {"source": "dynamic", "dynamic_count": 1499})
    bundle = asyncio.run(app._fetch_market_data("kr", wait_for_fresh=True))
    assert bundle["timing"]["kr_universe_source"] == "dynamic"
    assert bundle["timing"]["kr_universe_dynamic_count"] == 1499


def test_timing_us_has_no_kr_universe_info(mocked_env, monkeypatch):
    """US 전용 fetch에는 KR 유니버스 정보가 의미 없음 — None/0."""
    monkeypatch.setattr(app, "get_kr_universe_info",
                         lambda: {"source": "static_fallback", "dynamic_count": 0})
    bundle = asyncio.run(app._fetch_market_data("us", wait_for_fresh=True))
    assert bundle["timing"]["kr_universe_source"] is None
    assert bundle["timing"]["kr_universe_dynamic_count"] == 0


def test_timing_every_call_reflects_current_state_not_cached_once(mocked_env, monkeypatch):
    """★ 핵심 — 사용자 지시 2번. 상태가 static_fallback→dynamic으로
    바뀌면(예: naver가 복구됨) 그다음 호출의 TIMING도 즉시 바뀌어야
    한다 — "한 번 찍히고 다음 슬롯까지 조용"해지면 안 됨."""
    monkeypatch.setattr(app, "get_kr_universe_info",
                         lambda: {"source": "static_fallback", "dynamic_count": 0})
    bundle1 = asyncio.run(app._fetch_market_data("kr", wait_for_fresh=True, force=True))
    assert bundle1["timing"]["kr_universe_source"] == "static_fallback"

    monkeypatch.setattr(app, "get_kr_universe_info",
                         lambda: {"source": "dynamic", "dynamic_count": 1500})
    bundle2 = asyncio.run(app._fetch_market_data("kr", wait_for_fresh=True, force=True))
    assert bundle2["timing"]["kr_universe_source"] == "dynamic"
    assert bundle2["timing"]["kr_universe_dynamic_count"] == 1500


def test_all_market_merge_propagates_kr_universe_source(mocked_env, monkeypatch):
    """market="all"(kr+us 병합)에서도 kr 서브번들의 값이 그대로 전달되는지
    — static/index.html의 load()는 항상 market=all로 요청하므로 이 경로가
    실제로 화면에 뜨는 값이다."""
    monkeypatch.setattr(app, "get_kr_universe_info",
                         lambda: {"source": "static_fallback", "dynamic_count": 0})
    bundle = asyncio.run(app._fetch_market_data("all", wait_for_fresh=True))
    assert bundle["timing"]["kr_universe_source"] == "static_fallback"
    assert bundle["timing"]["kr_universe_dynamic_count"] == 0


def test_timing_schema_keys_include_kr_universe_fields():
    assert "kr_universe_source" in app._TIMING_SCHEMA_KEYS
    assert "kr_universe_dynamic_count" in app._TIMING_SCHEMA_KEYS


def test_calendar_pipeline_health_source_calls_get_kr_universe_info():
    """/api/calendar 핸들러 소스에 immediate_pipeline_health가
    get_kr_universe_info()를 실제로 스프레드해 넣는지 확인(엔드포인트
    전체를 mocking으로 다 채워 실행하기엔 비용이 커, 소스 확인으로
    "새 fetch 트리거 없이" 요구사항과의 연결점만 검증) — 재구현이 아니라
    실제 소스 문자열을 그대로 확인."""
    text = open("app.py", encoding="utf-8").read()
    start = text.index("immediate_pipeline_health = {")
    end = text.index("\n    }", start)
    body = text[start:end]
    assert "get_kr_universe_info()" in body
