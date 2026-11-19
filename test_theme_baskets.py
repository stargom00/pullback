"""v5.393 테마 바구니 — theme_baskets.py(순수) + /api/theme-baskets + /track 화면 요약 줄.

사보타주 확인(2026-11-19, 전부 FAIL 확인 후 원복): 점화 조건에서 비율(MIN_FRAC) 제거 / 옛 봉 줄을 요약에 포함 /
daily가 전 봉 대신 같은 봉 종가로 나눔 / 엔드포인트가 종료 바구니도 가격 조회 / basketLine에서 가격 실패 표시 제거.
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
    return [tb.member_row(m, None if p is None else 100 * (1 + p / 100), None if p is None else 100, day) for m, p in zip(_m(len(pcts)), pcts)]


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
    rows.append(tb.member_row({"ticker": "999999.KS", "name": "정지"}, 150, 100, "2026-11-10"))   # 옛 봉 +50%
    s = tb.summary(rows)
    assert (s["n"], s["n_px"], s["n_failed"], s["n_stale"], s["date"]) == (6, 3, 2, 1, "2026-11-19")
    assert s["strong"] == 3 and s["leader"]["chg_pct"] == 9.0   # 옛 봉 +50%는 대장·강한 종목에 안 들어간다
    empty = tb.summary(_rows([None, None]))
    assert empty["ignited"] is False and empty["avg_pct"] is None and empty["leader"] is None and empty["n_px"] == 0


def test_daily_uses_previous_bar_and_orders_newest_first():
    ms = _m(3)
    ser = {ms[0]["ticker"]: [("2026-11-17", 100.0), ("2026-11-18", 110.0), ("2026-11-19", 121.0)],
           ms[1]["ticker"]: [("2026-11-17", 50.0), ("2026-11-19", 55.0)],      # 11-18 봉 없음
           ms[2]["ticker"]: []}                                                 # 가격 실패
    d = tb.daily(ms, ser)
    assert [x["date"] for x in d] == ["2026-11-19", "2026-11-18", "2026-11-17"]
    assert [r["chg_pct"] for r in d[0]["rows"]] == [10.0, 10.0, None]
    assert [r["chg_pct"] for r in d[1]["rows"]] == [10.0, None, None]
    assert [r["chg_pct"] for r in d[2]["rows"]] == [None, None, None]         # 첫 봉은 앞 봉이 없어 등락률 없음
    assert d[0]["summary"]["n_failed"] == 1 and len(tb.daily(ms, ser, n=2)) == 2


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "THEME_BASKETS_PATH", str(tmp_path / "theme_baskets.json"))
    monkeypatch.setattr(app, "THEME_BASKETS_DELETE_LOG_PATH", str(tmp_path / "theme_baskets_deletions.log"))
    monkeypatch.setattr(app, "TRACK_LINES_PATH", str(tmp_path / "track_lines.json"))
    calls = []

    def quote(t):
        calls.append(t)
        up = {"352820.KS": 1.16, "041510.KQ": 1.30, "035900.KQ": 1.10, "122870.KQ": 1.07}.get(t, 1.0)
        return [("2026-11-17", 100.0), ("2026-11-18", 100.0 * up), ("2026-11-19", 100.0 * up * 0.99)]
    monkeypatch.setattr(app, "_basket_quote", quote)
    monkeypatch.setattr(app, "_basket_today", lambda ms: "2026-11-19")
    return calls


def _put(rec, base):
    r = asyncio.run(app.theme_basket_put(rec["id"], _Req({"record": rec, "base_rev": base})))
    return r.status_code, json.loads(r.body)


def _list(prices=1):
    return json.loads(asyncio.run(app.theme_baskets_list(prices)).body)


def test_seed_is_valid_and_planted_once(store):
    seed = json.load(open(os.path.join(ROOT, "theme_baskets_seed.json"), encoding="utf-8"))
    assert all(tb.invalid(tb.clean(x)) is None for x in seed) and len(seed[0]["members"]) == 23
    j = _list(0)
    assert j["ok"] and [b["name"] for b in j["baskets"]] == ["한한령"] and j["baskets"][0]["rev"] == 1
    assert store == []                                   # prices=0이면 가격 조회 0건
    code, _ = _put({"id": "b2", "name": "전력", "members": _m(3)}, None)
    assert code == 200 and [b["name"] for b in _list(0)["baskets"]] == ["한한령", "전력"]   # 파일이 있으면 다시 심지 않는다


def test_list_rows_summary_recent(store):
    assert _put({"id": "b3", "name": "끝난 테마", "status": "종료", "members": [{"ticker": "ENDED1"}, {"ticker": "ENDED2"}]}, None)[0] == 200
    j = _list(1)
    b = j["baskets"][0]
    assert b["summary"]["date"] == "2026-11-19" and b["summary"]["is_today"] is True and b["summary"]["ignited"] is False
    assert b["rows"][0]["chg_pct"] == -1.0 and len(b["rows"]) == 23
    assert [d["date"] for d in b["recent"]] == ["2026-11-19", "2026-11-18", "2026-11-17"]
    # 11-18 봉: 4/23이 +5% 이상이지만 30%(7개) 미달 → 점화 아님
    assert (b["recent"][1]["strong"], b["recent"][1]["ignited"]) == (4, False) and b["last_ignited"] is None
    assert j["rule"] == {"up_pct": tb.IGNITE_UP_PCT, "min_count": tb.IGNITE_MIN_COUNT, "min_frac": tb.IGNITE_MIN_FRAC}
    ended = j["baskets"][1]
    assert ended["summary"] is None and ended["rows"] == [] and not any(t.startswith("ENDED") for t in store)


def test_last_ignited_and_put_rules(store):
    ms = [{"ticker": t, "name": t} for t in ("352820.KS", "041510.KQ", "035900.KQ", "122870.KQ", "999999.KS")]
    code, j = _put({"id": "b4", "name": "엔터", "members": ms, "rows": [1]}, None)
    assert code == 200 and j["record"]["rev"] == 1 and "rows" not in j["record"]
    b = [x for x in _list(1)["baskets"] if x["id"] == "b4"][0]
    assert b["summary"]["ignited"] is False and b["last_ignited"]["date"] == "2026-11-18" and b["last_ignited"]["strong"] == 4
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
    rows.append(tb.member_row({"ticker": "999999.KS", "name": "정지"}, 150, 100, "2026-11-10"))
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
