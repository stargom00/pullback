"""v5.386 추적 장부 — track_lines(값·검증) + /api/track-lines(옮겨 심기·rev·삭제 로그·보기 필드).

사보타주 확인(2026-11-19, FAIL 확인 후 원복):
① line_value의 linear 분기 제거(전부 로그) → test_shared_vectors FAIL
② track_line_put이 kind를 받은 값 그대로 저장 → test_put_recomputes_kind_and_validates FAIL
③ _track_lines_load가 옮겨 심은 뒤 파일을 안 씀 → test_seed_once_then_file_is_source FAIL
④ view_row의 near 판정을 < 로(경계 제외) → test_view_zones FAIL
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest

import app
import track_lines as tl

ROOT = os.path.dirname(os.path.abspath(__file__))


class _Req:
    def __init__(self, body=None):
        self._body = body
        self.headers = {"user-agent": "pytest"}
        self.client = type("C", (), {"host": "127.0.0.1"})()

    async def json(self):
        return self._body


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "TRACK_LINES_PATH", str(tmp_path / "track_lines.json"))
    monkeypatch.setattr(app, "TRACK_LINES_DELETE_LOG_PATH", str(tmp_path / "track_lines_deletions.log"))
    monkeypatch.setattr(app, "_track_close", lambda t: ({"352820.KS": 189400.0, "NVDA": 165.0}.get(t), "2026-11-19") if t in ("352820.KS", "NVDA") else (None, None))
    return tmp_path


def _put(rec, base):
    r = asyncio.run(app.track_line_put(rec["id"], _Req({"record": rec, "base_rev": base})))
    return r.status_code, json.loads(r.body)


def _list(prices=1):
    return json.loads(asyncio.run(app.track_lines_list(prices)).body)


HYBE = {"id": "tl_hybe_top", "ticker": "352820.KS", "name": "하이브", "theme": "한한령", "label": "채널 상단", "scale": "log",
        "p1": ["2026-06-25", 250000], "p2": ["2026-10-15", 180000], "note": "상단 종가 돌파 시 진입 검토", "status": "추적"}


def test_shared_vectors():
    """얼마냐봇 trendlines.line_value와 같은 값(두 레포 테스트에 같은 숫자 — stock-alert test_trendlines.py::test_shared_vectors)."""
    log = {"id": "a", "p1": ["2026-01-01", 100.0], "p2": ["2026-04-11", 200.0]}            # 100일
    assert abs(tl.line_value(log, "2026-02-20") - 100 * 2 ** 0.5) < 1e-9                    # 50일째 = √2배
    assert abs(tl.line_value(log, "2026-07-20") - 400.0) < 1e-9                             # 200일째(연장) = 4배
    lin = dict(log, scale="linear")
    assert abs(tl.line_value(lin, "2026-02-20") - 150.0) < 1e-9
    assert abs(tl.line_value(lin, "2026-07-20") - 300.0) < 1e-9
    with pytest.raises(ValueError):
        tl.line_value({"id": "x", "p1": ["2026-01-01", 1], "p2": ["2026-01-01", 2]}, "2026-01-02")


def test_kind_and_validation():
    assert tl.kind_of(HYBE) == "fall" and tl.kind_of(dict(HYBE, p2=["2026-10-15", 250000])) == "flat"
    assert tl.kind_of(dict(HYBE, p1=["2026-10-15", 180000], p2=["2026-06-25", 250000])) == "fall"   # 점 순서가 뒤집혀도 시간순으로
    assert tl.invalid(HYBE) is None
    for bad in (dict(HYBE, ticker="352820.ks"), dict(HYBE, name=""), dict(HYBE, label="대각선"), dict(HYBE, scale="ln"),
                dict(HYBE, p1=["2026-06-25", 0]), dict(HYBE, p1=["2026-6-25", 1]), dict(HYBE, p2=["2026-06-25", 180000]),
                dict(HYBE, p1=["2026-06-25", True]), dict(HYBE, note="x" * 301), dict(HYBE, view={}), dict(HYBE, status="보류")):
        assert tl.invalid(bad), bad


def test_view_zones():
    base = {"id": "f", "p1": ["2026-01-01", 100.0], "p2": ["2026-06-01", 100.0]}
    z = lambda c: tl.view_row(base, c, "2026-11-19")["zone"]
    assert [z(103.0), z(97.0), z(103.1), z(96.9)] == ["near", "near", "above", "below"]     # ±3% 경계 포함
    v = tl.view_row(base, None, "2026-11-19")
    assert v["zone"] == "unknown" and v["dist_pct"] is None and v["value"] == 100.0


def test_seed_once_then_file_is_source(store):
    seed = json.load(open(os.path.join(ROOT, "track_lines_seed.json"), encoding="utf-8"))
    j = _list(0)
    assert j["ok"] and j["n"] == len(seed) == 19
    assert {x["id"] for x in j["lines"]} == {x["id"] for x in seed}
    assert all(x["rev"] == 1 and x["status"] == "추적" and x["scale"] == "log" for x in j["lines"])
    assert {x["label"] for x in j["lines"]} == {"빗각", "수평선"}
    assert os.path.exists(app.TRACK_LINES_PATH)
    # 하나 지우면 다시 안 살아난다(옮겨 심기는 파일이 없을 때 한 번뿐)
    r = asyncio.run(app.track_line_delete("soxx-flat-165", _Req()))
    assert json.loads(r.body)["deleted"] == "soxx-flat-165"
    assert _list(0)["n"] == 18 and "soxx-flat-165" in open(app.TRACK_LINES_DELETE_LOG_PATH, encoding="utf-8").read()


def test_put_recomputes_kind_and_validates(store):
    _list(0)
    st, j = _put(dict(HYBE, kind="rise"), None)                       # 받은 kind는 안 믿는다
    assert st == 200 and j["created"] and j["record"]["kind"] == "fall" and j["record"]["rev"] == 1 and j["record"]["created_at"]
    st, j = _put(dict(HYBE, ticker="hybe"), None)
    assert st == 400 and "티커" in j["error"]
    st, j = _put(dict(HYBE, id="tl_other"), 5)                         # 없는 id + base_rev → 되살리지 않음
    assert st == 409 and j["code"] == "gone"
    st, j = _put(dict(HYBE, note="수정", created_at="2026-11-19T00:00:00+09:00"), 0)   # 낡은 rev
    assert st == 409 and j["code"] == "conflict"
    st, j = _put(dict(HYBE, note="수정"), 1)
    assert st == 200 and j["record"]["rev"] == 2 and j["record"]["note"] == "수정"
    st, j = _put(dict(HYBE, note="수정", view={"x": 1}), 2)             # 화면이 붙여 보낸 view는 버린다
    assert st == 200 and "view" not in j["record"]


def test_list_view_fields_and_price_failure_visible(store):
    _list(0)
    _put(HYBE, None)
    _put({"id": "tl_x", "ticker": "ZZZZ", "name": "없는 종목", "label": "수평선", "scale": "log",
          "p1": ["2026-10-01", 10], "p2": ["2026-11-01", 10], "status": "추적"}, None)
    j = _list(1)
    by = {x["id"]: x for x in j["lines"]}
    h = by["tl_hybe_top"]["view"]
    exp = tl.line_value(HYBE, "2026-11-19")
    assert abs(h["value"] - exp) < 1e-6 and h["close"] == 189400.0 and h["zone"] == "above"
    assert abs(h["dist_pct"] - (189400 / exp - 1) * 100) < 1e-9
    assert by["tl_x"]["view"]["zone"] == "unknown"
    assert j["n_price_failed"] >= 1                                    # 조용히 빠지지 않는다


def test_bot_read_open_and_writes_need_session():
    assert "/api/track-lines" in app._BOT_READ_EXACT_PATHS
    assert not any(p.startswith("/api/track-lines/") for p in app._BOT_READ_EXACT_PATHS)


def test_page_and_header_link():
    html = open(os.path.join(ROOT, "static", "track.html"), encoding="utf-8").read()
    assert html.count("/api/track-lines") >= 4 and "Coordinates" in html
    idx = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
    assert idx.count('href="/track"') == 1
