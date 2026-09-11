"""종목 히스토리 조회 — /api/debug/{ticker} 응답의 history 섹션 (v5.248,
사용자 지시). 금강철강을 railway ssh로 파일 4개(journal_user.json/
auto_watch.json/signal_snapshot_cache.json/sector_snapshot.json) pull해
수동 검색했던 걸 화면에서 바로 할 수 있게 하는 기능.

`_ticker_scan_history(ticker)`(순수 조회 함수, app.py)를 직접 단위
테스트하고, 이름/코드 검색·해석 실패는 debug_ticker() 엔드포인트를
mocked_env로 실제로 호출해 확인한다(새 mock 인프라 만들지 않음,
test_fetch_market_data_all_merge.py의 mocked_env 재사용)."""
import asyncio
import json as _json

import pytest

import app
import sector_snapshot
from test_fetch_market_data_all_merge import _fake_get_universe, mocked_env  # noqa: F401


def _body(response):
    return _json.loads(response.body)


@pytest.fixture(autouse=True)
def _isolate_scanner_state(monkeypatch):
    """모듈 전역 상태(_auto_watch/_signal_snapshots)를 테스트마다 격리 —
    실제 프로덕션 데이터가 이 프로세스에 로드돼 있지 않은 로컬 테스트
    환경에선 보통 비어있지만, 명시적으로 고정해 테스트 간 오염을 막는다."""
    monkeypatch.setattr(app, "_auto_watch", {})
    monkeypatch.setattr(app, "_signal_snapshots", {})
    monkeypatch.setattr(app, "load_journal", lambda: [])
    monkeypatch.setattr(sector_snapshot, "load_all", lambda: {})
    yield


# ---------------------------------------------------------------------------
# _ticker_scan_history() — 순수 조회 함수 단위 테스트
# ---------------------------------------------------------------------------

def test_no_record_anywhere(monkeypatch):
    result = app._ticker_scan_history("999999.KQ")
    assert result["watch_snapshots"] == []
    assert result["sector_snapshot"] == []
    assert result["journal"] == []
    assert result["요약"] == "스캐너 기록 없음"


def test_all_three_sources_present(monkeypatch):
    monkeypatch.setattr(app, "_auto_watch", {
        "053260.KQ|돌파임박": {
            "ticker": "053260.KQ", "tab": "돌파임박", "signal_date": "2026-09-04",
            "pivot": 6030.0, "stop": 5890.0, "status": "confirmed",
            "confirmed_at": "2026-09-07", "confirm_close": 6410.0,
        },
    })
    monkeypatch.setattr(app, "_signal_snapshots", {
        "053260.KQ|돌파임박": {
            "signal_date": "2026-09-04", "pivot": 6030.0, "stop": 5890.0,
            "last_seen_date": "2026-09-07",
        },
    })
    monkeypatch.setattr(sector_snapshot, "load_all", lambda: {
        "2026-09-06": {
            "철강": {"hits": {"imminent": ["053260.KQ"]}, "leaders": ["053260.KQ", "009520.KQ"]},
        },
    })
    monkeypatch.setattr(app, "load_journal", lambda: [
        {"ticker": "053260.KQ", "date": "2026-09-05", "status": "entered",
         "entry": 6100, "stop": 5890, "result_r": "", "tab": "돌파임박"},
        {"ticker": "005930.KS", "date": "2026-09-01", "status": "closed",
         "entry": 70000, "stop": 68000, "result_r": 1.2, "tab": "눌림목"},
    ])

    result = app._ticker_scan_history("053260.KQ")
    assert len(result["watch_snapshots"]) == 1
    w = result["watch_snapshots"][0]
    assert w["tab"] == "돌파임박" and w["pivot"] == 6030.0 and w["status"] == "confirmed"
    assert w["confirm_close"] == 6410.0

    assert len(result["sector_snapshot"]) == 1
    s = result["sector_snapshot"][0]
    assert s["date"] == "2026-09-06" and s["sector"] == "철강"
    assert s["hit_tabs"] == ["imminent"] and s["is_leader"] is True

    assert len(result["journal"]) == 1   # 삼성전자 레코드는 안 섞여야 함
    assert result["journal"][0]["entry"] == 6100
    assert "스캐너 기록 없음" not in result["요약"]


def test_only_sector_snapshot_present(monkeypatch):
    """일부 소스만 있는 경우 — 나머지는 빈 리스트, 요약 문구에 정확한 건수."""
    monkeypatch.setattr(sector_snapshot, "load_all", lambda: {
        "2026-09-08": {"철강|KR": {"hits": {"breakout": ["053260.KQ"]}, "leaders": []}},
    })
    result = app._ticker_scan_history("053260.KQ")
    assert result["watch_snapshots"] == []
    assert result["journal"] == []
    assert len(result["sector_snapshot"]) == 1
    assert result["sector_snapshot"][0]["sector"] == "철강"   # "철강|KR" 키에서 파생
    assert "섹터스냅샷 1건" in result["요약"]
    assert "탭 히스토리 0건" in result["요약"]
    assert "저널 0건" in result["요약"]


def test_auto_watch_and_signal_snapshot_merge_no_duplicate_tab(monkeypatch):
    """같은 탭이 auto_watch/signal_snapshot 둘 다에 있으면 auto_watch(더
    풍부한 정보)만 쓰고 중복 행을 만들지 않는다."""
    monkeypatch.setattr(app, "_auto_watch", {
        "053260.KQ|돌파": {"ticker": "053260.KQ", "tab": "돌파", "signal_date": "2026-09-07",
                          "pivot": 6390.0, "stop": 6124.8, "status": "confirmed",
                          "confirmed_at": "2026-09-09", "confirm_close": 7990.0},
    })
    monkeypatch.setattr(app, "_signal_snapshots", {
        "053260.KQ|돌파": {"signal_date": "2026-09-07", "pivot": 6390.0, "stop": 6124.8,
                          "last_seen_date": "2026-09-08"},
    })
    result = app._ticker_scan_history("053260.KQ")
    assert len(result["watch_snapshots"]) == 1
    assert result["watch_snapshots"][0]["source"] == "auto_watch"
    assert result["watch_snapshots"][0]["confirm_close"] == 7990.0


def test_signal_snapshot_only_tab_preserved(monkeypatch):
    """auto_watch엔 없고 signal_snapshot에만 있는 탭도 보존(만료됐거나
    auto_watch 생기기 전 신호)."""
    monkeypatch.setattr(app, "_signal_snapshots", {
        "053260.KQ|눌림목": {"signal_date": "2026-08-20", "pivot": 5000.0, "stop": 4800.0,
                           "last_seen_date": "2026-08-22"},
    })
    result = app._ticker_scan_history("053260.KQ")
    assert len(result["watch_snapshots"]) == 1
    assert result["watch_snapshots"][0]["tab"] == "눌림목"
    assert result["watch_snapshots"][0]["source"] == "signal_snapshot"
    assert result["watch_snapshots"][0]["status"] is None


def test_sector_snapshot_leaders_dict_schema(monkeypatch):
    """leaders가 {"ticker","qualifies"} 딕셔너리 목록인 날짜 스키마도 인식."""
    monkeypatch.setattr(sector_snapshot, "load_all", lambda: {
        "2026-09-08": {
            "철강|KR": {"hits": {}, "leaders": [{"ticker": "053260.KQ", "qualifies": True}]},
        },
    })
    result = app._ticker_scan_history("053260.KQ")
    assert result["sector_snapshot"][0]["is_leader"] is True
    assert result["sector_snapshot"][0]["hit_tabs"] == []


def test_watch_snapshots_sorted_chronologically(monkeypatch):
    monkeypatch.setattr(app, "_auto_watch", {
        "053260.KQ|박스돌파": {"ticker": "053260.KQ", "tab": "박스돌파", "signal_date": "2026-09-10",
                             "pivot": 7050.0, "stop": 6762.0, "status": "watching"},
        "053260.KQ|돌파임박": {"ticker": "053260.KQ", "tab": "돌파임박", "signal_date": "2026-09-04",
                             "pivot": 6030.0, "stop": 5890.0, "status": "confirmed"},
        "053260.KQ|돌파": {"ticker": "053260.KQ", "tab": "돌파", "signal_date": "2026-09-07",
                          "pivot": 6390.0, "stop": 6124.8, "status": "confirmed"},
    })
    result = app._ticker_scan_history("053260.KQ")
    dates = [w["signal_date"] for w in result["watch_snapshots"]]
    assert dates == sorted(dates)
    assert dates == ["2026-09-04", "2026-09-07", "2026-09-10"]


# ---------------------------------------------------------------------------
# debug_ticker() 엔드포인트 — 이름 검색 / 코드 검색 / 해석 실패
# ---------------------------------------------------------------------------

def test_debug_ticker_resolves_by_korean_name(mocked_env, monkeypatch):
    # 주의: 딕셔너리 병합 순서 — _fake_get_universe(market)를 먼저 펼치고
    # 오버라이드를 뒤에 둬야 한다(반대로 하면 FIXTURE_KR이 이미 갖고 있는
    # "005930.KS": "005930.KS" 항목이 우리가 지정한 이름을 도로 덮어씀).
    monkeypatch.setattr(app, "get_universe",
                         lambda market: {**_fake_get_universe(market), "005930.KS": "삼성전자"})
    resp = asyncio.run(app.debug_ticker("삼성전자"))
    body = _body(resp)
    assert body.get("ticker") == "005930.KS", body
    assert "history" in body


def test_debug_ticker_resolves_bare_code(mocked_env):
    resp = asyncio.run(app.debug_ticker("005930"))
    body = _body(resp)
    assert body.get("ticker") == "005930.KS", body


def test_debug_ticker_resolves_full_code(mocked_env):
    resp = asyncio.run(app.debug_ticker("005930.KS"))
    body = _body(resp)
    assert body.get("ticker") == "005930.KS", body


def test_debug_ticker_name_not_found_returns_explicit_error(mocked_env, monkeypatch):
    monkeypatch.setattr(app, "get_universe",
                         lambda market: {"005930.KS": "삼성전자", **_fake_get_universe(market)})
    resp = asyncio.run(app.debug_ticker("존재하지않는종목이름"))
    body = _body(resp)
    assert body.get("error") == "종목을 찾을 수 없음"
    assert body.get("query") == "존재하지않는종목이름"
    # 조용히 빈 결과가 아니라 명시적 에러여야 함(사용자 지시).
    assert "history" not in body


def test_debug_ticker_ambiguous_name_returns_candidates(mocked_env, monkeypatch):
    # 두 이름 다 "삼성전자"로 시작하지만 "정확히 일치"는 아무것도 없게
    # 골라야 진짜 모호(prefix 단계에서 후보 2개)해진다 — resolve_name_to_
    # ticker()는 exact 단계에서 1건이 나오면 그걸로 즉시 확정해버려서,
    # 그중 하나라도 정확히 "삼성전자"이면 애초에 모호해지지 않는다.
    monkeypatch.setattr(app, "get_universe", lambda market: {
        **_fake_get_universe(market),
        "005930.KS": "삼성전자보통주", "005935.KS": "삼성전자우선주",
    })
    resp = asyncio.run(app.debug_ticker("삼성전자"))
    body = _body(resp)
    assert "candidates" in body, body
    assert len(body["candidates"]) == 2
