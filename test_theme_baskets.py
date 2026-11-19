"""v5.393 테마 바구니(v5.394 등락률 = 증권사 화면 값) — theme_baskets.py(순수) + /api/theme-baskets + /track 화면 요약 줄.

사보타주 확인(2026-11-19, 전부 FAIL 확인 후 원복): 점화 조건에서 비율(MIN_FRAC) 제거 / 옛 봉 줄을 요약에 포함 /
엔드포인트가 종료 바구니도 가격 조회 / basketLine에서 가격 실패 표시 제거.
v5.395: 씨앗 marker 무시(지운 바구니 부활) / replaces가 rev 무시 / sortBaskets 점화 우선 제거 — FAIL 확인 후 원복.
v5.394: 등락률 대신 전일 대비 금액을 읽음 / KR을 일봉으로 계산 / 실패도 캐시 — 전부 FAIL 확인 후 원복.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess

import pytest

import app
import theme_baskets as tb

ROOT = os.path.dirname(os.path.abspath(__file__))


class _Req:
    def __init__(self, body=None):
        self._body = body
        self.headers = {"user-agent": "pytest"}
        self.client = type("C", (), {"host": "127.0.0.1"})()

    async def json(self):
        return self._body


def _m(n):
    return [{"ticker": f"{i:06d}.KS", "name": f"종목{i}"} for i in range(1, n + 1)]


def _rows(pcts, day="2026-11-19"):
    return [tb.member_row(m, None if p is None else 100.0, p, day) for m, p in zip(_m(len(pcts)), pcts)]


def test_parse_members_and_validation():
    ms = tb.parse_members("하이브 352820.ks\n\nJYP Ent. 035900.KQ\nnvda, 에스엠 041510.KQ")
    assert ms == [{"ticker": "352820.KS", "name": "하이브"}, {"ticker": "035900.KQ", "name": "JYP Ent."},
                  {"ticker": "NVDA", "name": "NVDA"}, {"ticker": "041510.KQ", "name": "에스엠"}]
    ok = {"id": "b1", "name": "한한령", "members": ms}
    assert tb.invalid(ok) is None
    assert "이름" in tb.invalid({**ok, "name": " "})
    assert "2~40" in tb.invalid({**ok, "members": ms[:1]})
    assert "2~40" in tb.invalid({**ok, "members": _m(41)})
    assert "두 번" in tb.invalid({**ok, "members": ms + [{"ticker": "nvda", "name": "x"}]})
    assert "티커" in tb.invalid({**ok, "members": ms + [{"ticker": "하이브", "name": "x"}]})
    assert "status" in tb.invalid({**ok, "status": "보류"})
    c = tb.clean({**ok, "members": [{"ticker": "nvda"}, {"ticker": "aapl", "name": " 애플 "}], "rows": [1], "summary": {}})
    assert c["members"] == [{"ticker": "NVDA", "name": "NVDA"}, {"ticker": "AAPL", "name": "애플"}]
    assert "rows" not in c and "summary" not in c and c["status"] == "추적"


def test_summary_ignite_needs_count_and_fraction():
    # 10종목 중 +5% 이상 3개 → 3개 이상이고 30% 이상 → 점화
    s = tb.summary(_rows([9, 6, 5, 4.99, 1, 0, -1, -2, -3, -4]))
    assert (s["strong"], s["need"], s["ignited"], s["up"], s["n_px"]) == (3, 3, True, 5, 10)
    assert s["leader"]["chg_pct"] == 9.0 and s["avg_pct"] == pytest.approx(1.6, abs=0.01)
    # 20종목 중 3개 → 15%라 30% 미달(need 6) → 점화 아님 — 큰 바구니에서 몇 개만 튄 것
    s = tb.summary(_rows([9, 6, 5] + [0] * 17))
    assert (s["strong"], s["need"], s["ignited"]) == (3, 6, False)
    # 4종목 중 2개 = 50%지만 3개 미만 → 점화 아님
    s = tb.summary(_rows([9, 6, 0, 0]))
    assert (s["strong"], s["need"], s["ignited"]) == (2, 3, False)


def test_summary_excludes_failed_and_stale_rows():
    rows = _rows([9, 8, 7, None, None])
    rows.append(tb.member_row({"ticker": "999999.KS", "name": "정지"}, 150, 50.0, "2026-11-10"))   # 옛 봉 +50%
    s = tb.summary(rows)
    assert (s["n"], s["n_px"], s["n_failed"], s["n_stale"], s["date"]) == (6, 3, 2, 1, "2026-11-19")
    assert s["strong"] == 3 and s["leader"]["chg_pct"] == 9.0   # 옛 봉 +50%는 대장·강한 종목에 안 들어간다
    empty = tb.summary(_rows([None, None]))
    assert empty["ignited"] is False and empty["avg_pct"] is None and empty["leader"] is None and empty["n_px"] == 0


def test_parse_quote_uses_screen_ratio():
    """v5.394 — 등락률은 naver 화면 값(전일 정규장 종가 대비)을 그대로 쓴다. 실제 응답 모양(2026-11-19 스튜디오드래곤·코스맥스)."""
    import naver_kr
    q = naver_kr.parse_quote({"closePrice": "30,800", "compareToPreviousClosePrice": "7,100", "fluctuationsRatio": "29.96",
                              "localTradedAt": "2026-11-19T19:24:21+09:00"})
    assert q == {"close": 30800.0, "chg_pct": 29.96, "date": "2026-11-19"}
    assert naver_kr.parse_quote({"closePrice": "289,000", "fluctuationsRatio": "-2.36", "localTradedAt": "2026-11-19T19:47:21+09:00"})["chg_pct"] == -2.36
    for bad in (None, [], {}, {"closePrice": "30,800"}, {"closePrice": "0", "fluctuationsRatio": "1", "localTradedAt": "2026-11-19T1"},
                {"closePrice": "30,800", "fluctuationsRatio": "", "localTradedAt": "2026-11-19T19:24:21+09:00"},
                {"closePrice": "30,800", "fluctuationsRatio": "1.0", "localTradedAt": ""}):
        assert naver_kr.parse_quote(bad) is None, bad


def test_basket_quote_kr_screen_ratio_us_bars(monkeypatch):
    import pandas as pd
    monkeypatch.setattr(app, "_basket_px_cache", {})
    seen = []
    monkeypatch.setattr(app.naver_kr, "fetch_quote", lambda t: seen.append(t) or {"close": 30800.0, "chg_pct": 29.96, "date": "2026-11-19"})
    df = pd.DataFrame({"Close": [100.0, 110.0]}, index=pd.to_datetime(["2026-11-17", "2026-11-18"]))
    monkeypatch.setattr(app, "_fetch", lambda t: df if t == "NVDA" else (_ for _ in ()).throw(AssertionError("KR은 일봉으로 계산하지 않는다")))
    assert app._basket_quote("253450.kq") == (30800.0, 29.96, "2026-11-19") and app._basket_quote("253450") == (30800.0, 29.96, "2026-11-19")
    c, p, d = app._basket_quote("NVDA")
    assert (c, round(p, 6), d) == (110.0, 10.0, "2026-11-18")
    assert app._basket_quote("253450.KQ") == (30800.0, 29.96, "2026-11-19") and seen == ["253450.KQ", "253450"]   # 5분 캐시
    monkeypatch.setattr(app.naver_kr, "fetch_quote", lambda t: None)
    assert app._basket_quote("000001.KS") == (None, None, None) and "000001.KS" not in app._basket_px_cache    # 실패는 캐시 안 함


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "THEME_BASKETS_PATH", str(tmp_path / "theme_baskets.json"))
    monkeypatch.setattr(app, "THEME_BASKETS_DELETE_LOG_PATH", str(tmp_path / "theme_baskets_deletions.log"))
    monkeypatch.setattr(app, "THEME_BASKETS_SEEDED_PATH", str(tmp_path / "theme_baskets_seeded.json"))
    monkeypatch.setattr(app, "THEME_BASKETS_SEED_PATH", str(tmp_path / "seed.json"))
    json.dump([{"id": "tb_hanhanryeong", "name": "한한령", "members": SEED23}], open(tmp_path / "seed.json", "w"), ensure_ascii=False)
    monkeypatch.setattr(app, "TRACK_LINES_PATH", str(tmp_path / "track_lines.json"))
    calls = []

    def quote(t):
        calls.append(t)
        return 100.0, {"352820.KS": 16.0, "041510.KQ": 30.0, "035900.KQ": 10.0, "122870.KQ": 7.0}.get(t, -1.0), "2026-11-19"
    monkeypatch.setattr(app, "_basket_quote", quote)
    monkeypatch.setattr(app, "_basket_today", lambda ms: "2026-11-19")
    return calls


def _put(rec, base):
    r = asyncio.run(app.theme_basket_put(rec["id"], _Req({"record": rec, "base_rev": base})))
    return r.status_code, json.loads(r.body)


def _list(prices=1):
    return json.loads(asyncio.run(app.theme_baskets_list(prices)).body)


SEED23 = [{"ticker": t, "name": t} for t in ["352820.KS", "041510.KQ", "035900.KQ", "122870.KQ"] + [f"{i:06d}.KS" for i in range(1, 20)]]


def test_real_seed_file_is_valid():
    seed = json.load(open(os.path.join(ROOT, "theme_baskets_seed.json"), encoding="utf-8"))
    assert len(seed) == 11 and len({x["id"] for x in seed}) == 11 and len({x["name"] for x in seed}) == 11
    for x in seed:
        assert tb.invalid(tb.clean({k: v for k, v in x.items() if k != "replaces"})) is None, x["id"]
        assert all(m["ticker"].endswith((".KS", ".KQ")) and m["name"] != m["ticker"] for m in x["members"]), x["id"]
    assert [x["id"] for x in seed if x.get("replaces")] == ["tb_enter"]


def test_seed_planted_once_per_id(store, tmp_path):
    j = _list(0)
    assert j["ok"] and [b["name"] for b in j["baskets"]] == ["한한령"] and j["baskets"][0]["rev"] == 1
    assert store == []                                   # prices=0이면 가격 조회 0건
    code, _ = _put({"id": "b2", "name": "전력", "members": _m(3)}, None)
    assert code == 200 and [b["name"] for b in _list(0)["baskets"]] == ["한한령", "전력"]
    # 씨앗이 늘면 새 id만 심는다. replaces는 손대지 않은(rev 1) 옛 씨앗만 치운다
    json.dump([{"id": "tb_hanhanryeong", "name": "한한령", "members": SEED23},
               {"id": "tb_enter", "name": "엔터", "members": SEED23[:4], "replaces": ["tb_hanhanryeong"]},
               {"id": "tb_robot", "name": "로봇", "members": SEED23[4:8]}], open(tmp_path / "seed.json", "w"), ensure_ascii=False)
    assert [b["name"] for b in _list(0)["baskets"]] == ["전력", "엔터", "로봇"]
    # 지운 씨앗 바구니는 다시 살아나지 않는다
    asyncio.run(app.theme_basket_delete("tb_robot", _Req()))
    assert [b["name"] for b in _list(0)["baskets"]] == ["전력", "엔터"]
    assert sorted(json.load(open(app.THEME_BASKETS_SEEDED_PATH))) == ["tb_enter", "tb_hanhanryeong", "tb_robot"]


def test_seed_replace_keeps_edited_basket(store, tmp_path):
    b = _list(0)["baskets"][0]
    assert _put({"id": b["id"], "name": "한한령(내 목록)", "members": SEED23[:5]}, 1)[1]["record"]["rev"] == 2
    json.dump([{"id": "tb_hanhanryeong", "name": "한한령", "members": SEED23},
               {"id": "tb_enter", "name": "엔터", "members": SEED23[:4], "replaces": ["tb_hanhanryeong"]}], open(tmp_path / "seed.json", "w"), ensure_ascii=False)
    assert [x["name"] for x in _list(0)["baskets"]] == ["한한령(내 목록)", "엔터"]      # 고친 바구니는 그대로 둔다


def test_list_rows_summary_recent(store):
    assert _put({"id": "b3", "name": "끝난 테마", "status": "종료", "members": [{"ticker": "ENDED1"}, {"ticker": "ENDED2"}]}, None)[0] == 200
    j = _list(1)
    b = j["baskets"][0]
    assert b["summary"]["date"] == "2026-11-19" and b["summary"]["is_today"] is True and b["summary"]["ignited"] is False
    assert [r["chg_pct"] for r in b["rows"][:5]] == [30.0, 16.0, 10.0, 7.0, -1.0] and len(b["rows"]) == 23   # 많이 오른 순
    # 4/23이 +5% 이상이지만 30%(7개) 미달 → 점화 아님
    assert (b["summary"]["strong"], b["summary"]["need"]) == (4, 7) and "recent" not in b and "last_ignited" not in b
    assert j["rule"] == {"up_pct": tb.IGNITE_UP_PCT, "min_count": tb.IGNITE_MIN_COUNT, "min_frac": tb.IGNITE_MIN_FRAC}
    ended = j["baskets"][1]
    assert ended["summary"] is None and ended["rows"] == [] and not any(t.startswith("ENDED") for t in store)


def test_ignited_basket_and_put_rules(store):
    ms = [{"ticker": t, "name": t} for t in ("352820.KS", "041510.KQ", "035900.KQ", "122870.KQ", "999999.KS")]
    code, j = _put({"id": "b4", "name": "엔터", "members": ms, "rows": [1]}, None)
    assert code == 200 and j["record"]["rev"] == 1 and "rows" not in j["record"]
    b = [x for x in _list(1)["baskets"] if x["id"] == "b4"][0]
    assert b["summary"]["ignited"] is True and b["summary"]["strong"] == 4 and b["summary"]["leader"]["chg_pct"] == 30.0
    assert _put({"id": "b4", "name": "엔터", "members": ms[:1]}, 1)[0] == 400
    assert _put({"id": "b4", "name": "엔터2", "members": ms}, 7)[1]["code"] == "conflict"
    assert _put({"id": "b4", "name": "엔터2", "members": ms}, 1)[1]["record"]["rev"] == 2
    r = asyncio.run(app.theme_basket_delete("b4", _Req()))
    assert json.loads(r.body)["deleted"] == "b4"
    assert "b4" in open(app.THEME_BASKETS_DELETE_LOG_PATH, encoding="utf-8").read()
    assert _put({"id": "b4", "name": "엔터", "members": ms}, 2)[1]["code"] == "gone"


def test_bot_read_open_writes_need_session():
    assert "/api/theme-baskets" in app._BOT_READ_EXACT_PATHS
    assert not any(p.startswith("/api/theme-baskets/") for p in app._BOT_READ_EXACT_PATHS)


def _extract_js(name: str) -> str:
    src = open(os.path.join(ROOT, "static", "track.html"), encoding="utf-8").read()
    start = src.index(f"function {name}(")
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        depth += {"{": 1, "}": -1}.get(src[j], 0)
        if depth == 0:
            return src[start:j + 1]
    raise AssertionError(name)


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_page_basket_line_and_member_parser():
    rows = _rows([9, 8, 7, None])
    rows.append(tb.member_row({"ticker": "999999.KS", "name": "정지"}, 150, 50.0, "2026-11-10"))
    sm = tb.summary(rows)
    text = "하이브 352820.ks\n\nJYP Ent. 035900.KQ\nnvda, 에스엠 041510.KQ"
    js = ("const esc=s=>String(s??'');const pctTxt=p=>p==null?'—':(p>0?'+':'')+p.toFixed(1)+'%';\n" + _extract_js("basketLine") + "\n"
          + _extract_js("parseMembers") + "\nconsole.log(JSON.stringify({line:basketLine(" + json.dumps(sm) + "),none:basketLine("
          + json.dumps(tb.summary(_rows([None, None]))) + "),ms:parseMembers(" + json.dumps(text) + ")}))")
    out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
    assert "<b>3/3</b>" in out["line"] and "+8.0%" in out["line"] and "대장 종목1 <b>+9.0%</b>" in out["line"]
    assert "가격 실패 1" in out["line"] and "옛 봉 1" in out["line"]
    assert out["none"] == "가격을 못 받았어"
    assert out["ms"] == tb.parse_members(text)          # 화면과 서버가 같은 규칙으로 읽는다
    html = open(os.path.join(ROOT, "static", "track.html"), encoding="utf-8").read()
    assert html.count("loadBaskets()") >= 3 and 'id="themeBox"' in html
    bs = [{"id": "q", "status": "추적", "summary": {"ignited": False, "avg_pct": 9}}, {"id": "e", "status": "종료", "summary": None},
          {"id": "f1", "status": "추적", "summary": {"ignited": True, "avg_pct": 3}}, {"id": "f2", "status": "추적", "summary": {"ignited": True, "avg_pct": 8}},
          {"id": "n", "status": "추적", "summary": {"ignited": False, "avg_pct": None}}]
    js2 = _extract_js("sortBaskets") + "\nconsole.log(JSON.stringify(sortBaskets(" + json.dumps(bs) + ").map(b=>b.id)))"
    assert json.loads(subprocess.run(["node", "-e", js2], capture_output=True, text=True, check=True).stdout) == ["f2", "f1", "q", "n", "e"]
