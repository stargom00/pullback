"""v5.320 — 저점 매매 기록 수정(보유·종료).

서버: 기존 PUT(base_rev) 그대로 — rev 충돌 409, 같은 종목·구분 보유 겹침을 새로 만드는 수정·생성은 400(거부+안내).
프론트(node, production 원문 실행): lpApplyHoldEdit/lpApplyCloseEdit → 목표가·정렬·수익금·월간 요약·상단 합계가
수정된 레코드로 다시 계산되는지(값은 렌더 때 레코드에서 계산 — 저장값 아님).

사보타주 확인(2026-10-04, FAIL 확인 후 원복):
① lpApplyHoldEdit가 매수가 수정을 버리게(buyPrice: r.buyPrice) → test_hold_edit_recalculates_target_and_return FAIL
② lp_trade_put의 on_update 겹침 검사 제거 → test_edit_creating_overlap_is_rejected FAIL
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess

import pytest

import app

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()


class _Req:
    def __init__(self, body=None):
        self._body = body
        self.headers = {"user-agent": "pytest"}
        self.client = type("C", (), {"host": "127.0.0.1"})()

    async def json(self):
        return self._body


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "LP_TRADES_PATH", str(tmp_path / "t.json"))
    monkeypatch.setattr(app, "LP_TRADES_DELETE_LOG_PATH", str(tmp_path / "d.log"))
    return tmp_path


def _put(rec, base):
    r = asyncio.run(app.lp_trade_put(rec["id"], _Req({"record": rec, "base_rev": base})))
    return r.status_code, json.loads(r.body)


HOLD = {"id": 1, "kind": "단기", "mkt": "KR", "code": "019680.KS", "name": "대교",
        "buyDate": "2026-09-22", "buyPrice": 1650, "qty": 100}
COIN = {"id": 7, "kind": "장기", "mkt": "UPBIT", "code": "KRW-XRP", "name": "엑스알피(리플)",
        "buyDate": "2026-10-01", "buyPrice": 3100.5, "qty": 12.5}


def _load(store):
    return json.loads((store / "t.json").read_text(encoding="utf-8"))


# ── 서버 ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("base", [HOLD, COIN])
def test_hold_edit_saves_with_rev(store, base):
    _put(base, None)
    s, d = _put({**base, "buyPrice": base["buyPrice"] * 0.9, "qty": base["qty"] * 2, "buyDate": "2026-09-20"}, 1)
    assert s == 200 and d["record"]["rev"] == 2
    rec = _load(store)[0]
    assert rec["qty"] == base["qty"] * 2 and rec["code"] == base["code"] and rec["mkt"] == base["mkt"]


def test_edit_with_stale_rev_is_rejected(store):
    _put(HOLD, None)
    assert _put({**HOLD, "qty": 120}, 1)[0] == 200                       # rev 1 → 2
    s, d = _put({**HOLD, "buyPrice": 1}, 1)                                # 낡은 탭의 수정
    assert s == 409 and d["code"] == "conflict" and d["record"]["qty"] == 120
    assert _load(store)[0]["buyPrice"] == 1650


def test_kind_change_without_overlap(store):
    _put(HOLD, None)
    s, d = _put({**HOLD, "kind": "장기"}, 1)
    assert s == 200 and _load(store)[0]["kind"] == "장기"


def test_edit_creating_overlap_is_rejected(store):
    """같은 종목의 장기 보유가 이미 있을 때 단기 → 장기로 바꾸면 겹친다 → 거부+안내, 기록은 그대로."""
    _put(HOLD, None)
    _put({**HOLD, "id": 2, "kind": "장기", "buyPrice": 1500, "qty": 10}, None)
    s, d = _put({**HOLD, "kind": "장기"}, 1)
    assert s == 400 and "같은 종목·구분" in d["error"] and "추매" in d["error"]
    assert [r["kind"] for r in _load(store)] == ["단기", "장기"]


def test_create_duplicate_holding_is_rejected_but_close_records_are_not(store):
    _put(HOLD, None)
    assert _put({**HOLD, "id": 3}, None)[0] == 400                         # 새 보유 겹침
    part = {**HOLD, "id": 4, "qty": 30, "sellDate": "2026-09-29", "sellPrice": 1720, "partial": True, "partial_of": 1}
    assert _put(part, None)[0] == 200                                      # 분할 종료(종료 기록)는 대상 아님


def test_existing_overlap_can_still_be_edited(store):
    """이미 겹쳐 있던 기록(예전 데이터)은 구분·보유 상태를 바꾸지 않는 수정이 막히지 않는다."""
    store.joinpath("t.json").write_text(json.dumps([{**HOLD, "rev": 1}, {**HOLD, "id": 2, "rev": 1}]), encoding="utf-8")
    assert _put({**HOLD, "id": 2, "qty": 50}, 1)[0] == 200


def test_closed_record_edit(store):
    closed = {**HOLD, "sellDate": "2026-09-30", "sellPrice": 1720}
    _put(closed, None)
    s, d = _put({**closed, "sellPrice": 1800, "sellDate": "2026-10-01", "qty": 90}, 1)
    assert s == 200 and d["record"]["sellPrice"] == 1800 and d["record"]["qty"] == 90
    assert _put({**closed, "sellPrice": 0}, 2)[0] == 400                   # 기존 검증 그대로


# ── 프론트(node) ───────────────────────────────────────────────────
def _fn(name):
    start = SRC.index(f"function {name}(")
    i = SRC.index("{", SRC.index(")", start))
    d = 0
    for j in range(i, len(SRC)):
        d += {"{": 1, "}": -1}.get(SRC[j], 0)
        if d == 0:
            return SRC[start:j + 1]
    raise AssertionError(name)


FNS = ("lpApplyHoldEdit", "lpApplyCloseEdit", "lpTargetPrice", "lpReturnPct", "lpSortHoldings", "lpRealizedPnl",
       "lpCurrencyBucket", "lpMonthlySummary", "lpRealizedSummary")


def _js(expr):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    src = "const LPT_KINDS = ['단기', '장기'];\n" + "\n".join(_fn(f) for f in FNS)
    p = subprocess.run(["node", "-e", src + f"\nconsole.log(JSON.stringify({expr}));"], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_kinds_constant_matches_source():
    assert "const LPT_KINDS = ['단기', '장기'];" in SRC


TP = {"단기": 4, "장기": 100}


def test_hold_edit_recalculates_target_and_return():
    r = _js(f"""(() => {{ const e = lpApplyHoldEdit({json.dumps({**HOLD, "rev": 3})}, {{kind: '장기', buyDate: '2026-09-20', buyPrice: '1500', qty: '120'}});
      return {{e, target: lpTargetPrice(e.buyPrice, {json.dumps(TP)}[e.kind]), ret: lpReturnPct(1650, e.buyPrice)}}; }})()""")
    e = r["e"]
    assert (e["kind"], e["buyDate"], e["buyPrice"], e["qty"]) == ("장기", "2026-09-20", 1500, 120)
    assert (e["id"], e["code"], e["name"], e["mkt"], e["rev"]) == (1, "019680.KS", "대교", "KR", 3)   # 종목·rev 불변
    assert r["target"] == 3000 and r["ret"] == 10


def test_kind_change_moves_row_in_sort():
    rows = [{**HOLD, "id": 1, "name": "가"}, {**HOLD, "id": 2, "name": "나", "code": "X"}]
    got = _js(f"""lpSortHoldings([lpApplyHoldEdit({json.dumps(rows[0])}, {{kind: '장기', buyDate: 'd', buyPrice: 1, qty: 1}}),
      {json.dumps(rows[1])}]).map(r => r.kind + ':' + r.name)""")
    assert got == ["단기:나", "장기:가"]


def test_close_edit_recalculates_pnl_and_summaries():
    closed = {**HOLD, "sellDate": "2026-09-30", "sellPrice": 1700}
    r = _js(f"""(() => {{ const e = lpApplyCloseEdit({json.dumps(closed)}, {{sellDate: '2026-10-02', sellPrice: '1800', qty: '50'}});
      return {{pnl: lpRealizedPnl(e).pnl, months: lpMonthlySummary([e]).map(m => [m.month, m.pnl]),
              top: lpRealizedSummary([e], '2026-10-05').month.KR}}; }})()""")
    assert r["pnl"] == (1800 - 1650) * 50 and r["months"] == [["2026-10", 7500]] and r["top"] == 7500


@pytest.mark.parametrize("fields,msg", [
    ("{kind: '중기', buyDate: 'd', buyPrice: 1, qty: 1}", "구분"),
    ("{kind: '단기', buyDate: '', buyPrice: 1, qty: 1}", "매수일"),
    ("{kind: '단기', buyDate: 'd', buyPrice: 0, qty: 1}", "0보다"),
])
def test_hold_edit_validation(fields, msg):
    got = _js(f"(() => {{ try {{ lpApplyHoldEdit({json.dumps(HOLD)}, {fields}); return null; }} catch (e) {{ return e.message; }} }})()")
    assert got and msg in got


def test_coin_edit_keeps_decimals():
    e = _js(f"lpApplyHoldEdit({json.dumps(COIN)}, {{kind: '장기', buyDate: '2026-10-01', buyPrice: '3050.25', qty: '12.345678'}})")
    assert e["buyPrice"] == 3050.25 and e["qty"] == 12.345678 and e["mkt"] == "UPBIT"


def test_rejection_shows_server_message_only():
    put = _fn("_lptPut")
    assert "allow: [409, 400]" in put and "throw new Error((d && d.error)" in put


def test_ui_wiring():
    assert SRC.count('onclick="lptStartEdit(${r.id})">수정</button>') == 2          # 보유·종료 두 곳
    assert SRC.count('onsubmit="event.preventDefault();lptSaveEdit(this, ${r.id})"') == 2
    save = _fn("lptSaveEdit")
    assert "_lptPut(rec, r.rev)" in save and "lpApplyCloseEdit" in save and "lpApplyHoldEdit" in save
