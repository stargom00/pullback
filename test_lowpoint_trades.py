"""v5.302 — 저점 매매 기록(일지와 분리, 레코드 단위 저장).

서버: PUT 409 규칙 · 추매 합산 저장 · 분할 종료 수량 검증 · DELETE 로그.
프론트(node, production 원문 실행): 추매 평단 · 분할 종료 분기 · 보유 정렬 · 월간 집계.

사보타주 확인(2026-09-30): lp_trade_put의 base_rev 비교 제거 → test_stale_put_is_409 FAIL — 원복.
"""
import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import app

TEXT = (Path(__file__).resolve().parent / "static" / "index.html").read_text(encoding="utf-8")


class _Req:
    def __init__(self, body=None):
        self._body = body
        self.headers = {"user-agent": "pytest"}
        self.client = type("C", (), {"host": "127.0.0.1"})()

    async def json(self):
        return self._body


def _b(r):
    return json.loads(r.body)


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "LP_TRADES_PATH", str(tmp_path / "lowpoint_trades.json"))
    monkeypatch.setattr(app, "LP_TRADES_DELETE_LOG_PATH", str(tmp_path / "del.log"))
    monkeypatch.setattr(app, "LP_TRADES_SETTINGS_PATH", str(tmp_path / "settings.json"))
    return tmp_path


def _put(rec, base_rev):
    return asyncio.run(app.lp_trade_put(rec["id"], _Req({"record": rec, "base_rev": base_rev})))


HOLD = {"id": 1, "kind": "단기", "mkt": "KR", "code": "019680.KS", "name": "대교",
        "buyDate": "2026-09-22", "buyPrice": 1650, "qty": 100}


def _load(store):
    return json.loads((store / "lowpoint_trades.json").read_text(encoding="utf-8"))


def test_create_and_list(store):
    r = _put(HOLD, None)
    assert r.status_code == 200 and _b(r)["record"]["rev"] == 1
    lst = _b(asyncio.run(app.lp_trades_list()))
    assert [t["id"] for t in lst["trades"]] == [1]
    assert lst["settings"]["target_pct"] == {"단기": 4.0, "장기": 100.0}


def test_stale_put_is_409(store):
    _put(HOLD, None)
    assert _put({**HOLD, "qty": 150, "buyPrice": 1640}, 1).status_code == 200      # rev 1 → 2
    r = _put({**HOLD, "qty": 999}, 1)                                                 # 낡은 탭
    assert r.status_code == 409 and _b(r)["code"] == "conflict" and _b(r)["record"]["qty"] == 150
    assert _load(store)[0]["qty"] == 150


def test_add_on_buy_saved_as_one_record(store):
    """추매: 프론트가 합친 한 레코드(평단·수량)를 base_rev로 PUT — 레코드 수는 그대로."""
    _put(HOLD, None)
    merged = {**HOLD, "qty": 150, "buyPrice": 1633.3333}
    r = _put(merged, 1)
    assert r.status_code == 200
    rows = _load(store)
    assert len(rows) == 1 and rows[0]["qty"] == 150 and rows[0]["rev"] == 2


@pytest.mark.parametrize("qty,ok", [(40, True), (100, False), (120, False)])
def test_partial_close_quantity_checked(store, qty, ok):
    _put(HOLD, None)
    part = {**HOLD, "id": 2, "qty": qty, "sellDate": "2026-09-29", "sellPrice": 1720,
            "partial": True, "partial_of": 1}
    r = _put(part, None)
    assert (r.status_code == 200) is ok, _b(r)
    if not ok:
        assert "보유 수량보다 작아야" in _b(r)["error"]
        assert len(_load(store)) == 1


def test_invalid_records_rejected(store):
    assert _put({**HOLD, "kind": "중기"}, None).status_code == 400
    assert _put({**HOLD, "qty": 0}, None).status_code == 400
    assert _put({**HOLD, "sellDate": "2026-09-29"}, None).status_code == 400   # 매도가 없음


def test_delete_logs_and_removes(store):
    _put(HOLD, None)
    r = asyncio.run(app.lp_trade_delete(1, _Req()))
    assert r.status_code == 200 and _load(store) == []
    e = json.loads((store / "del.log").read_text(encoding="utf-8").strip())
    assert e["id"] == 1 and e["record"]["name"] == "대교" and e["client"] == "127.0.0.1"


def test_deleted_record_put_is_gone(store):
    _put(HOLD, None)
    asyncio.run(app.lp_trade_delete(1, _Req()))
    r = _put(HOLD, 1)
    assert r.status_code == 409 and _b(r)["code"] == "gone" and _load(store) == []


def test_settings_roundtrip(store):
    r = asyncio.run(app.lp_trade_settings_put(_Req({"target_pct": {"단기": 5, "장기": 80}})))
    assert r.status_code == 200
    assert app._lp_trade_settings()["target_pct"] == {"단기": 5.0, "장기": 80.0}
    assert asyncio.run(app.lp_trade_settings_put(_Req({"target_pct": {"단기": -1}}))).status_code == 400


def test_no_full_array_write_route():
    paths = {getattr(r, "path", "") for r in app.app.routes}
    methods = {(getattr(r, "path", ""), m) for r in app.app.routes for m in (getattr(r, "methods", None) or [])}
    assert ("/api/lowpoint/trades", "POST") not in methods and ("/api/lowpoint/trades", "PUT") not in methods
    assert "/api/lowpoint/trades/{rid}" in paths


# ── 프론트 순수 함수 ────────────────────────────────────────────────
def _fn(name):
    i = TEXT.index(f"function {name}(")
    b = TEXT.index("{", TEXT.index(")", i))
    d = 0
    for k in range(b, len(TEXT)):
        d += {"{": 1, "}": -1}.get(TEXT[k], 0)
        if d == 0:
            return TEXT[i:k + 1]
    raise AssertionError(name)


def _js(expr):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    # v5.311: lpMonthlySummary가 공용 가드 lpRealizedPnl(값 누락 레코드 제외)을 쓴다 —
    # 추출 목록에 없으면 ReferenceError로 즉시 드러난다(실제로 그렇게 잡혔다).
    src = "\n".join(_fn(n) for n in ("lpMergeBuy", "lpSplitSell", "lpSortHoldings", "lpReturnPct",
                                     "lpTargetPrice", "lpRealizedPnl", "lpMonthlySummary"))
    p = subprocess.run(["node", "-e", src + f"\nconsole.log(JSON.stringify({expr}));"],
                       capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_merge_buy_weighted_average():
    r = _js("lpMergeBuy({id:1, buyPrice:1000, qty:100, buyDate:'2026-09-22', rev:3}, {buyPrice:1300, qty:50, buyDate:'2026-09-20'})")
    assert r["qty"] == 150 and r["buyPrice"] == 1100 and r["buyDate"] == "2026-09-20" and r["rev"] == 3


def test_split_sell_branches():
    full = _js("lpSplitSell({id:1, qty:100, buyPrice:1000, rev:2}, {qty:100, sellDate:'2026-09-29', sellPrice:1100}, 99)")
    assert full["full"] is True and full["remain"] is None and full["close"]["id"] == 1 and full["close"]["sellPrice"] == 1100
    part = _js("lpSplitSell({id:1, qty:100, buyPrice:1000, rev:2, updated_at:'x'}, {qty:30, sellDate:'2026-09-29', sellPrice:1100}, 99)")
    assert part["full"] is False
    assert part["close"]["id"] == 99 and part["close"]["qty"] == 30 and part["close"]["partial_of"] == 1
    assert "rev" not in part["close"], "새 종료 레코드가 보유의 rev를 물려받으면 생성 PUT이 gone으로 막힌다"
    assert part["remain"]["qty"] == 70 and part["remain"]["rev"] == 2
    err = _js("(() => { try { lpSplitSell({id:1, qty:10, buyPrice:1}, {qty:11, sellDate:'d', sellPrice:1}, 2); return null; } catch (e) { return e.message; } })()")
    assert "보유 10주보다" in err


def test_holdings_sorted_short_first_even_if_long_inserted_first():
    r = _js("lpSortHoldings([{kind:'장기', name:'가나'}, {kind:'단기', name:'하나'}, {kind:'장기', name:'Apple'}, {kind:'단기', name:'대교'}]).map(x => x.kind + ':' + x.name)")
    assert r[:2] == ["단기:대교", "단기:하나"]
    assert [x.split(":")[0] for x in r[2:]] == ["장기", "장기"]


def test_monthly_summary_by_sell_month_and_market():
    r = _js("""lpMonthlySummary([
      {sellDate:'2026-09-10', mkt:'KR', buyPrice:1000, sellPrice:1100, qty:10},
      {sellDate:'2026-09-20', mkt:'KR', buyPrice:1000, sellPrice:900, qty:10},
      {sellDate:'2026-09-21', mkt:'US', buyPrice:10, sellPrice:12, qty:5},
      {sellDate:'2026-08-30', mkt:'KR', buyPrice:100, sellPrice:104, qty:1}])""")
    assert [(x["month"], x["mkt"]) for x in r] == [("2026-09", "KR"), ("2026-09", "US"), ("2026-08", "KR")]
    kr9 = r[0]
    assert kr9["n"] == 2 and kr9["wins"] == 1 and kr9["winRate"] == 50 and kr9["avgRet"] == 0 and kr9["pnl"] == 0
    assert all(x["skipped"] == 0 for x in r), "정상 레코드인데 값 누락으로 빠진 게 있다"
    assert r[1]["pnl"] == 10 and r[1]["avgRet"] == 20


def test_render_puts_long_separator_row():
    fn = _fn("renderLowpointTrack")
    assert "prevKind === '단기' && r.kind === '장기'" in fn and '<span>장기</span>' in fn
    assert "lpSortHoldings(" in fn
