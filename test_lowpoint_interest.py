"""v5.328 — 저점 추적 페이지의 관심 종목(매수 전) 추적.

사용자 지시: "추적 페이지가 보유 종목만 보여줌. 매수 전 종목도 같은 방식으로 추적하고 싶다." 추가 폼(저점 resolve ·
구분 · 기준가 = 추가 시점 마지막 종가 자동, 수정 가능) · "관심 추적" 그룹(보유 단기 → 보유 장기 → 관심 추적) ·
[기록] → 매매 기록 프리필, 보유가 실제로 저장되면 그 관심 항목 자동 제거 · 일일 추적·지금 갱신에 포함.

사보타주 확인(2026-10-06, FAIL 확인 후 원복):
① track_holdings가 관심 종목을 조회 대상에서 뺌(보유만) → test_tracking_includes_interest FAIL
② lp_trade_put의 from_interest 처리 제거 → test_record_converts_and_removes_interest FAIL
③ 종료 기록(sellDate) 저장에도 관심 제거 → test_no_removal_when_not_a_holding FAIL
④ lpiLookup이 이미 적은 기준가를 덮어씀(keepPrice 무시) → test_base_price_autofill_and_edit FAIL
⑤ lpiSetQuery가 종목을 바꿔도 기준가를 남김(로컬에서 실제로 난 버그 — 하이딥용 1,250이 PEG에 붙음)
   → test_changing_stock_clears_base_price FAIL
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.join(ROOT, "scripts", "screens"), os.path.join(ROOT, "scripts", "measurements")):
    sys.path.insert(0, p)
import lowpoint_watch as w  # noqa: E402

import app  # noqa: E402

KST = timezone(timedelta(hours=9))
SRC = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()


class _Req:
    def __init__(self, body=None):
        self._body = body
        self.headers = {"user-agent": "pytest"}
        self.client = type("C", (), {"host": "127.0.0.1"})()

    async def json(self):
        return self._body


def _iput(rec, base=None):
    r = asyncio.run(app.lp_interest_put(rec["id"], _Req({"record": rec, "base_rev": base})))
    return r.status_code, json.loads(r.body)


def _tput(rec, base=None, **extra):
    r = asyncio.run(app.lp_trade_put(rec["id"], _Req({"record": rec, "base_rev": base, **extra})))
    return r.status_code, json.loads(r.body)


I_KR = {"id": "i_kr1", "kind": "단기", "mkt": "KR", "code": "019680.KS", "name": "대교", "basePrice": 1623, "baseDate": "2026-10-02"}
I_US = {"id": "i_us1", "kind": "장기", "mkt": "US", "code": "AVA", "name": "Avista Corporation Common Stock", "basePrice": 35.13, "baseDate": "2026-10-05"}
I_COIN = {"id": "i_c1", "kind": "단기", "mkt": "UPBIT", "code": "KRW-XRP", "name": "엑스알피(리플)", "basePrice": 2044, "baseDate": "2026-10-05"}


def _interest():
    return app._rec_list_load(app.LP_INTEREST_PATH)


# ── 서버: 저장 ─────────────────────────────────────────────────────
def test_add_kr_us_coin_with_rev():
    for rec in (I_KR, I_US, I_COIN):
        s, d = _iput(rec)
        assert s == 200 and d["record"]["rev"] == 1
    assert [r["code"] for r in _interest()] == ["019680.KS", "AVA", "KRW-XRP"]
    s, d = _iput({**I_KR, "basePrice": 1600}, 1)                          # 기준가 수정
    assert s == 200 and d["record"]["rev"] == 2
    assert _iput({**I_KR, "basePrice": 1}, 1)[0] == 409                  # 낡은 rev


@pytest.mark.parametrize("bad,msg", [({"code": "XRP", "mkt": "UPBIT"}, "UPBIT"), ({"basePrice": 0}, "기준가"),
                                     ({"baseDate": "2026-13-01"}, "baseDate"), ({"id": "x1"}, "i_"), ({"kind": "중기"}, "kind")])
def test_validation(bad, msg):
    rec = {**I_KR, **bad}
    r = asyncio.run(app.lp_interest_put(rec["id"], _Req({"record": rec, "base_rev": None})))
    assert r.status_code == 400 and msg in json.loads(r.body)["error"]


def test_duplicate_code_kind_rejected_but_other_kind_ok():
    _iput(I_KR)
    assert _iput({**I_KR, "id": "i_kr2"})[0] == 400
    assert _iput({**I_KR, "id": "i_kr3", "kind": "장기"})[0] == 200


def test_delete_logs():
    _iput(I_US)
    assert json.loads(asyncio.run(app.lp_interest_delete("i_us1", _Req())).body)["deleted"] == "i_us1"
    log = open(app.LP_INTEREST_DELETE_LOG_PATH, encoding="utf-8").read().splitlines()
    assert json.loads(log[0])["record"]["code"] == "AVA"


def test_last_close_endpoint_uses_tracking_fetch(monkeypatch):
    seen = []
    monkeypatch.setattr(w, "fetch_last_closes", lambda items, today: seen.append(items) or {"AVA": {"last_close": 35.13, "last_date": "2026-10-05"}})
    d = json.loads(asyncio.run(app.lp_last_close("AVA", "US")).body)
    assert d == {"ok": True, "code": "AVA", "last_close": 35.13, "last_date": "2026-10-05"} and seen == [[{"code": "AVA", "mkt": "US"}]]


# ── 서버: [기록] 전환 ──────────────────────────────────────────────
HOLD = {"id": 101, "kind": "단기", "mkt": "KR", "code": "019680.KS", "name": "대교", "buyDate": "2026-10-06", "buyPrice": 1620, "qty": 10}


def test_record_converts_and_removes_interest():
    _iput(I_KR); _iput(I_US)
    s, d = _tput(HOLD, from_interest="i_kr1")
    assert s == 200 and d["interest_removed"] == "i_kr1"
    assert [r["id"] for r in _interest()] == ["i_us1"]
    log = json.loads(open(app.LP_INTEREST_DELETE_LOG_PATH, encoding="utf-8").read().splitlines()[0])
    assert log["record"]["_reason"] == "converted_to_holding"
    assert [t["code"] for t in app._lp_trades_load()] == ["019680.KS"]         # 보유 그룹에 등장(보유 기록)


def test_no_removal_when_not_a_holding():
    _iput(I_KR)
    assert _tput({**HOLD, "sellDate": "2026-10-06", "sellPrice": 1700, "exitReason": "기타"}, from_interest="i_kr1")[0] == 200
    assert _tput({**HOLD, "id": 102, "code": "005930.KS", "name": "삼성전자"}, from_interest="i_kr1")[1].get("interest_removed") is None
    _tput({**HOLD, "id": 103})                                                 # 대교 보유 생성
    s, d = _tput({**HOLD, "id": 104}, from_interest="i_kr1")                  # 겹침 400 — 저장 안 됨
    assert s == 400 and [r["id"] for r in _interest()] == ["i_kr1"]


# ── 추적 포함 ───────────────────────────────────────────────────────
def test_tracking_includes_interest():
    asked = []
    fake = lambda items, today: asked.append(sorted(i["code"] for i in items)) or {i["code"]: {"last_close": 1.0, "last_date": "2026-10-05"} for i in items}
    prices, s = w.track_holdings([HOLD], date(2026, 10, 6), "now", None, interest=[I_US, I_COIN], fetch=fake)
    assert asked == [["019680.KS", "AVA", "KRW-XRP"]]
    assert set(prices) == {"019680.KS", "AVA", "KRW-XRP"} and prices["KRW-XRP"]["mkt"] == "UPBIT"
    assert s["held"] == 1 and s["interest"] == 2
    p2, _ = w.track_holdings([], date(2026, 10, 6), "now", prices, interest=[I_US], fetch=lambda i, t: {})
    assert set(p2) == {"AVA"}                                                   # 실패 → 이전 값, 관심에서 빠진 종목은 제거


def test_job_tracks_interest(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "LOWPOINT_DATA_PATH", str(tmp_path / "d.json"))
    monkeypatch.setattr(app, "LOWPOINT_LATEST_PATH", str(tmp_path / "r.json"))
    monkeypatch.setattr(app, "LOWPOINT_STATE_PATH", str(tmp_path / "s.json"))
    _iput(I_US)
    fake = lambda items, today: {i["code"]: {"last_close": 36.0, "last_date": "2026-10-05"} for i in items}
    monkeypatch.setattr(w.track_holdings, "__defaults__", (None, None, fake))
    s = app._lp_watch_job_blocking("watch", datetime(2026, 10, 6, 7, 0, tzinfo=KST))
    assert s["counts"]["holdings"]["interest"] == 1
    assert json.loads(asyncio.run(app.lp_holdings_track()).body)["prices"]["AVA"]["last_close"] == 36.0
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert src.count('_daily_backup(LP_INTEREST_PATH, "lowpoint_interest"') == 2


# ── 프론트(node) ───────────────────────────────────────────────────
def _fn(name):
    start = SRC.index(f"function {name}(")
    if SRC[start - 6:start] == "async ":
        start -= 6
    i = SRC.index("{", SRC.index(")", start))
    d = 0
    for j in range(i, len(SRC)):
        d += {"{": 1, "}": -1}.get(SRC[j], 0)
        if d == 0:
            return SRC[start:j + 1]
    raise AssertionError(name)


def _js(expr, fns, pre="", wrap_async=False):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    src = ("const _escapeHtml = s => String(s);\nconst kstStr = () => '2026-10-06';\nconst tvUrl = (c, m) => 'tv:' + c;\n"
           "const LPT_KINDS = ['단기', '장기'];\n" + "\n".join(_fn(f) for f in fns) + "\n" + pre)
    body = f"(async () => {{ console.log(JSON.stringify(await ({expr}))); }})();" if wrap_async else f"console.log(JSON.stringify({expr}));"
    p = subprocess.run(["node", "-e", src + "\n" + body], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


TP = {"단기": 4, "장기": 100}
PRICES = {"019680.KS": {"last_close": 1655.46, "last_date": "2026-10-05"}, "AVA": {"last_close": 52.695, "last_date": "2026-10-05"},
          "KRW-XRP": {"last_close": 1900, "last_date": "2026-10-05"}}


def test_rows_goal_per_kind_and_bar_scale():
    rows = _js(f"lpiRows({json.dumps([I_KR, I_US, I_COIN])}, {json.dumps(PRICES)}, {json.dumps(TP)}, '2026-10-06').map(x => [x.r.code, Math.round(x.ret * 100) / 100, x.goal, x.days, lpwBar(x.ret, x.goal)])",
               ("lpiRows", "lpReturnPct", "lpwDays", "lpwBar"))
    assert [r[:4] for r in rows] == [["AVA", 50.0, 100, 1], ["019680.KS", 2.0, 4, 4], ["KRW-XRP", -7.05, 4, 1]]
    assert rows[0][4]["side"] == "pos" and rows[0][4]["width"] == pytest.approx(50) and rows[1][4]["width"] == pytest.approx(50)   # 장기 +50%/100 = 단기 +2%/4
    assert rows[2][4] == {"side": "neg", "width": 100}


def test_from_interest_only_for_same_ticker():
    got = _js("[lpFromInterest({q: '019680.KS', interestId: 'i_kr1'}, '019680.KS'), lpFromInterest({q: '019680.KS', interestId: 'i_kr1'}, '005930.KS'), lpFromInterest({q: 'AVA'}, 'AVA'), lpFromInterest(null, 'AVA')]",
              ("lpFromInterest",))
    assert got == ["i_kr1", None, None, None]


def test_base_price_autofill_and_edit():
    stub = ("var _lpi = { q: 'AVA', kind: '장기', price: '', found: null };\n"
            "const apiJson = async url => url.includes('/resolve/') ? { ok: true, ticker: 'AVA', name: 'Avista', mkt: 'US' }"
            " : { ok: true, last_close: 35.13, last_date: '2026-10-05' };\n")
    got = _js("(async () => { await lpiLookup(true); const a = [_lpi.price, _lpi.found.date, _lpi.found.mkt];"
              " _lpi.price = '34'; await lpiLookup(true); const b = _lpi.price; await lpiLookup(false); return [a, b, _lpi.price]; })()",
              ("lpiLookup",), stub, wrap_async=True)
    assert got == [["35.13", "2026-10-05", "US"], "34", "35.13"]   # 비었으면 자동 입력 · 고친 값은 유지 · 불러오기 버튼은 다시 채움


def test_render_order_and_buttons():
    fns = ("lpkGroups", "lpTargetPrice", "lpReturnPct", "lpwDays", "lpwBar", "_lptFmt", "_lptPct", "lpDisplayName",
           "_lptSellFormHtml", "_lptReasonFieldsHtml", "lpiRows", "renderLowpointHold")
    trades = [{"id": 1, "kind": "단기", "mkt": "KR", "code": "005930.KS", "name": "삼성전자", "buyDate": "2026-10-01", "buyPrice": 70000, "qty": 1},
              {"id": 2, "kind": "장기", "mkt": "US", "code": "NKE", "name": "Nike", "buyDate": "2026-09-26", "buyPrice": 36, "qty": 5}]
    pre = (f"var _lpt = {{ trades: {json.dumps(trades)}, settings: {{ target_pct: {json.dumps(TP)} }}, selling: null, error: null }};\n"
           f"var _lpk = {{ prices: {json.dumps(PRICES)}, checkedAt: null, failed: [], error: null, busy: false, msg: '' }};\n"
           f"var _lpi = {{ recs: {json.dumps([I_KR, I_US])}, error: null, busy: false, msg: '', q: '', kind: '단기', price: '', found: null }};\n")
    html = _js("renderLowpointHold()", fns, pre)
    i_s, i_l, i_i = html.index("보유 단기 · 1"), html.index("보유 장기 · 1"), html.index("관심 추적 · 2")
    assert i_s < i_l < i_i and html.index("관심 종목 추가") < i_s
    interest = html[i_i:]
    assert interest.count("lpiRecord(") == 2 and interest.count("lpiDelete(") == 2 and "lptStartSell(" not in interest
    assert ">AVA</a>" in interest and "목표까지" in interest


def test_record_prefill_and_form_kind():
    r = _fn("lpiRecord")
    assert "_lpt.prefill = { q: r.code, kind: r.kind, interestId: r.id };" in r and "_lpt.view = 'trades';" in r
    assert "_lpt.prefill && _lpt.prefill.kind === k ? ' selected' : ''" in _fn("renderLowpointTrack")
    add = _fn("lptAdd")
    assert "const fi = lpFromInterest(_lpt.prefill, t.ticker);" in add and add.count(", extra)") == 2
    assert "...(extra || {})" in _fn("_lptPut")
    assert "await lpiLoad();" in _fn("lpkLoad")


def test_changing_stock_clears_base_price():
    """2026-10-06 로컬 실사용에서 난 버그: 앞 종목에 적은 기준가가 다음 종목 추가에 그대로 붙었다."""
    pre = ("var _lpi = { q: '하이딥', kind: '단기', price: '1250', found: { q: '하이딥', ticker: '365590.KQ' } };\n"
           "const price = { value: '1250' };\nconst el = { value: 'PEG', form: { querySelector: () => price } };\n")
    got = _js("(() => { lpiSetQuery(el); return [_lpi.q, _lpi.price, _lpi.found, price.value]; })()", ("lpiSetQuery",), pre)
    assert got == ["PEG", "", None, ""]
    pre2 = "var _lpi = { q: '', kind: '단기', price: '', found: null };\nconst el = { value: 'AVA', form: null };\n"
    assert _js("(() => { lpiSetQuery(el); _lpi.price = '35'; return [_lpi.q, _lpi.price]; })()", ("lpiSetQuery",), pre2) == ["AVA", "35"]
    assert 'oninput="lpiSetQuery(this)"' in _fn("renderLowpointHold")
