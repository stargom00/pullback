"""v5.378 보유 종목 바닥선 — 저점관리 추적 대상에 보유 추가 + GET /api/lowpoint/held-floors(봇 읽기).

픽스처는 실데이터 형태를 따른다: 토스 KR 티커는 접미사 없는 6자리("407400"), 저점 기록은 "407400.KQ",
저점관리 행 키도 "407400.KQ". 바닥선 없는 행(no_hit)은 active_line 키가 아예 없다.

사보타주 확인(2026-11-10, FAIL 확인 후 원복):
  ① held_floor_rows가 저점관리 행 키를 _base_code 없이 원래 코드("407400.KQ")로 둠 → test_toss_code_joins_suffixed_floor_row FAIL
  ② _held_items가 sellDate 있는 기록도 보유로 셈 → test_closed_lp_trade_not_held FAIL
  ③ _lp_floor_add_held가 naver 시장 확인 없이 ".KS"를 붙임 → test_add_held_resolves_market_via_naver FAIL
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts", "screens"))
import app  # noqa: E402

FLOOR_ROWS = {
    "407400.KQ": {"code": "407400.KQ", "status": "ok", "state": "holding", "floor_line": 1850.0, "active_line": 1850.0,
                  "new_line": None, "last_close": 1990.0, "last_date": "2026-11-09", "dist_pct": 7.6,
                  "hit": {"tf": "week", "bar_date": "2026-10-02", "close": 1969.0}},
    "AAPL": {"code": "AAPL", "status": "no_hit", "hit": None},
}


def _held(monkeypatch, positions, trades):
    monkeypatch.setattr(app, "_load_positions_raw", lambda: {"positions": positions})
    monkeypatch.setattr(app, "_rec_list_load", lambda path: trades if path == app.LP_TRADES_PATH else [])


def test_base_code():
    assert app._base_code("407400.KQ") == "407400"
    assert app._base_code("A407400") == "407400"
    assert app._base_code("407400") == "407400"
    assert app._base_code("aapl") == "AAPL"
    assert app._base_code("0013V0.KQ") == "0013V0"


def test_toss_code_joins_suffixed_floor_row(monkeypatch):
    _held(monkeypatch, [{"ticker": "407400", "name": "꿈비", "market": "KR", "avg_price": 2000}], [])
    rows = app.held_floor_rows(app._held_items(), FLOOR_ROWS)
    assert len(rows) == 1
    r = rows[0]
    assert r["has_floor"] is True and r["active_line"] == 1850.0 and r["hit"]["tf"] == "week"
    assert r["sources"] == ["토스"]


def test_no_floor_reasons_are_explicit(monkeypatch):
    _held(monkeypatch, [{"ticker": "AAPL", "name": "Apple", "market": "US", "avg_price": 200},
                        {"ticker": "005930", "name": "삼성전자", "market": "KR", "avg_price": 70000}], [])
    rows = {r["code"]: r for r in app.held_floor_rows(app._held_items(), FLOOR_ROWS)}
    assert rows["AAPL"]["has_floor"] is False and rows["AAPL"]["reason"] == "no_hit"
    assert rows["005930"]["has_floor"] is False and rows["005930"]["reason"] == "no_row"


def test_closed_lp_trade_not_held(monkeypatch):
    _held(monkeypatch, [], [{"code": "407400.KQ", "name": "꿈비", "mkt": "KR", "buyPrice": 1990, "sellDate": "2026-10-10"},
                            {"code": "AAPL", "name": "Apple", "mkt": "US", "buyPrice": 200}])
    codes = [h["code"] for h in app._held_items()]
    assert codes == ["AAPL"]


def test_toss_and_lp_trade_merge_once(monkeypatch):
    _held(monkeypatch, [{"ticker": "407400", "name": "꿈비", "market": "KR", "avg_price": 2000}],
          [{"code": "407400.KQ", "name": "꿈비", "mkt": "KR", "buyPrice": 1990}])
    items = app._held_items()
    assert len(items) == 1 and items[0]["sources"] == ["토스", "저점 기록"] and items[0]["avg_price"] == 2000


def test_add_held_resolves_market_via_naver(monkeypatch):
    import naver_kr
    _held(monkeypatch, [{"ticker": "407400", "name": "꿈비", "market": "KR"},
                        {"ticker": "999999", "name": "없는종목", "market": "KR"},
                        {"ticker": "AAPL", "name": "Apple", "market": "US"}], [])
    monkeypatch.setattr(naver_kr, "fetch_basic", lambda c: {"suffix": ".KQ"} if c == "407400" else None)
    targets = {}
    app._lp_floor_add_held(targets)
    assert set(targets) == {"407400.KQ", "AAPL"}          # 시장 모르는 종목은 추측해 붙이지 않고 뺀다
    assert targets["407400.KQ"]["market"] == "KOSDAQ" and targets["407400.KQ"]["mkt"] == "KR"
    assert targets["AAPL"]["mkt"] == "US"


def test_add_held_marks_existing_target(monkeypatch):
    _held(monkeypatch, [{"ticker": "407400", "name": "꿈비", "market": "KR"}], [])
    targets = {"407400.KQ": {"code": "407400.KQ", "sources": ["저점 주봉"]}}
    app._lp_floor_add_held(targets)
    assert list(targets) == ["407400.KQ"] and targets["407400.KQ"]["sources"] == ["저점 주봉", "보유"]


def test_bot_can_read_path():
    assert "/api/lowpoint/held-floors" in app._BOT_READ_EXACT_PATHS
