"""v5.345 — 저점 매매 기록 종료 사유.

사용자 지시: "종료할 때 이유를 남길 곳이 없어 '기준대로 나온 매매'와 '감으로 나온 매매'를 구분할 수 없음." 종료 시 사유 선택 필수
(목표 도달 / 손절선 이탈 / 시간 손절 / 판단 변경 / 기타) + 메모 한 줄(선택). 종료 행에 사유 표시, 사유별 건수·실현 손익 표. 기존
종료 레코드는 "미기록", 수정으로 나중에 채움.
메인 일지 종료 사유는 수정 모달 안의 선택지·인라인 검증(추세추종용 사유 — 손절가 도달/트레일링/목표/충동)이라 그대로 쓸 수 없어,
같은 "종료 시 필수 게이트" 방식을 저점 매매의 기존 검증 경로(lp_trade_put on_create/on_update · 프론트 lpSplitSell)에 얹었다.

사보타주 확인(2026-10-09, FAIL 확인 후 원복):
① 서버 필수 검증 제거(_lp_trade_exit_rule이 항상 None) → test_close_without_reason_rejected · test_partial_close_needs_reason FAIL
② 프론트 필수 검증 제거(lpSplitSell의 사유 확인 줄) → test_front_split_requires_reason FAIL
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


def test_reasons_are_user_list():
    assert app.LP_TRADE_EXIT_REASONS == ("목표 도달", "손절선 이탈", "시간 손절", "판단 변경", "기타")
    d = json.loads(asyncio.run(app.lp_trades_list()).body)
    assert d["exit_reasons"] == list(app.LP_TRADE_EXIT_REASONS)                                # 화면은 이 목록만 쓴다


def test_close_without_reason_rejected(store):
    assert _put(HOLD, None)[0] == 200
    s, d = _put({**HOLD, "sellDate": "2026-10-08", "sellPrice": 1700}, 1)                      # 보유 → 종료, 사유 없음
    assert s == 400 and "종료 사유를 고르세요" in d["error"]
    s, d = _put({**HOLD, "sellDate": "2026-10-08", "sellPrice": 1700, "exitReason": "감"}, 1)
    assert s == 400 and "exitReason" in d["error"]
    s, d = _put({**HOLD, "sellDate": "2026-10-08", "sellPrice": 1700, "exitReason": "시간 손절", "exitMemo": "3주 횡보"}, 1)
    assert s == 200 and (d["record"]["exitReason"], d["record"]["exitMemo"]) == ("시간 손절", "3주 횡보")
    assert _put({**HOLD, "id": 9, "sellDate": "2026-10-08", "sellPrice": 1700}, None)[0] == 400   # 처음부터 종료로 생성도 사유 필수
    assert _put({**HOLD, "id": 10, "exitReason": "기타"}, None)[0] == 400                        # 보유 기록엔 사유 없음
    assert _put({**d["record"], "exitMemo": "a\nb"}, d["record"]["rev"])[0] == 400                # 메모는 한 줄


def test_partial_close_needs_reason(store):
    _put(HOLD, None)
    part = {**HOLD, "id": 2, "qty": 30, "sellDate": "2026-10-08", "sellPrice": 1720, "partial": True, "partial_of": 1}
    assert _put(part, None)[0] == 400
    assert _put({**part, "exitReason": "목표 도달"}, None)[0] == 200


def test_old_closed_record_unrecorded_then_filled(store):
    """v5.345 전 종료 레코드(사유 없음 = 미기록)는 그대로 수정 저장되고, 나중에 사유를 채울 수 있다. 채운 사유는 지울 수 없다."""
    old = {**HOLD, "sellDate": "2026-09-30", "sellPrice": 1720, "rev": 3, "updated_at": "x"}
    (store / "t.json").write_text(json.dumps([old]), encoding="utf-8")
    s, d = _put({**old, "sellPrice": 1730}, 3)                                                   # 미기록인 채 다른 칸 수정
    assert s == 200 and d["record"].get("exitReason") is None
    s, d = _put({**d["record"], "exitReason": "판단 변경", "exitMemo": "실적 미스"}, d["record"]["rev"])
    assert s == 200 and d["record"]["exitReason"] == "판단 변경"
    saved = json.loads((store / "t.json").read_text(encoding="utf-8"))[0]
    assert (saved["exitReason"], saved["exitMemo"], saved["sellPrice"]) == ("판단 변경", "실적 미스", 1730)
    s, d = _put({**saved, "exitReason": None}, saved["rev"])
    assert s == 400                                                                             # 지우기 거부


# ── 프론트(node, production 원문 실행) ─────────────────────────────────
def _fn(name):
    start = SRC.index(f"function {name}(")
    i = SRC.index("{", SRC.index(")", start))
    d = 0
    for j in range(i, len(SRC)):
        d += {"{": 1, "}": -1}.get(SRC[j], 0)
        if d == 0:
            return SRC[start:j + 1]
    raise AssertionError(name)


def _js(expr):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    fns = ("lpSplitSell", "lpApplyCloseEdit", "lpExitReasonSummary", "lpRealizedPnl", "lpCurrencyBucket", "lpReturnPct")
    p = subprocess.run(["node", "-e", "\n".join(_fn(f) for f in fns) + f"\nconsole.log(JSON.stringify({expr}));"],
                       capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_front_split_requires_reason():
    got = _js("""(() => { const h = {id:1, qty:100, buyPrice:1000, rev:2};
      const err = (() => { try { lpSplitSell(h, {qty:100, sellDate:'2026-10-08', sellPrice:1100}, 9); return null; } catch (e) { return e.message; } })();
      const full = lpSplitSell(h, {qty:100, sellDate:'2026-10-08', sellPrice:1100, exitReason:'목표 도달', exitMemo:'  +10%  '}, 9);
      const part = lpSplitSell(h, {qty:30, sellDate:'2026-10-08', sellPrice:1100, exitReason:'기타', exitMemo:''}, 9);
      return [err, full.close.exitReason, full.close.exitMemo, part.close.exitReason, part.close.exitMemo, part.remain.exitReason ?? null]; })()""")
    assert got == ["종료 사유를 고르세요", "목표 도달", "+10%", "기타", None, None]                 # 잔여 보유엔 사유 없음


def test_front_close_edit_rules():
    got = _js("""(() => { const old = {id:1, sellDate:'2026-09-30', sellPrice:1700, qty:10};
      const keep = lpApplyCloseEdit(old, {sellDate:'2026-09-30', sellPrice:'1710', qty:'10', exitReason:'', exitMemo:''});
      const fill = lpApplyCloseEdit(old, {sellDate:'2026-09-30', sellPrice:'1710', qty:'10', exitReason:'손절선 이탈', exitMemo:'저점 이탈'});
      const err = (() => { try { lpApplyCloseEdit(fill, {sellDate:'2026-09-30', sellPrice:'1710', qty:'10', exitReason:''}); return null; } catch (e) { return e.message; } })();
      return [keep.exitReason, fill.exitReason, fill.exitMemo, err]; })()""")
    assert got[0] is None and got[1:3] == ["손절선 이탈", "저점 이탈"] and "지울 수 없어요" in got[3]


def test_reason_summary_sums_to_all_closed():
    closed = [
        {"sellDate": "2026-10-01", "mkt": "KR", "buyPrice": 1000, "sellPrice": 1100, "qty": 10, "exitReason": "목표 도달"},
        {"sellDate": "2026-10-02", "mkt": "KR", "buyPrice": 1000, "sellPrice": 900, "qty": 10, "exitReason": "손절선 이탈"},
        {"sellDate": "2026-10-03", "mkt": "US", "buyPrice": 10, "sellPrice": 12, "qty": 5, "exitReason": "목표 도달"},
        {"sellDate": "2026-10-04", "mkt": "KR", "buyPrice": 1000, "sellPrice": 1050, "qty": 2},                    # 옛 종료 → 미기록
        {"sellDate": "2026-10-05", "mkt": "UPBIT", "buyPrice": 100, "sellPrice": 90, "qty": 1, "exitReason": "기타"},
        {"sellDate": "2026-10-06", "mkt": "KR", "buyPrice": None, "sellPrice": 1, "qty": 1, "exitReason": "기타"},  # 값 누락
    ]
    rows = _js(f"lpExitReasonSummary({json.dumps(closed)}, {json.dumps(list(app.LP_TRADE_EXIT_REASONS))})")
    by = {r["reason"]: r for r in rows}
    assert [r["reason"] for r in rows] == list(app.LP_TRADE_EXIT_REASONS) + ["미기록"]
    assert sum(r["n"] for r in rows) == len(closed)                                            # 사유별 합 = 전체 종료 건수
    assert by["목표 도달"]["n"] == 2 and by["목표 도달"]["pnl"] == {"KR": 1000, "US": 10}
    assert by["손절선 이탈"]["pnl"]["KR"] == -1000 and by["미기록"]["n"] == 1 and by["미기록"]["pnl"]["KR"] == 100
    assert by["기타"]["n"] == 2 and by["기타"]["skipped"] == 1 and by["기타"]["pnl"]["KR"] == -10   # 업비트는 ₩ 버킷


def test_wiring():
    form = _fn("_lptSellFormHtml")
    assert form.count("_lptReasonFieldsHtml(null, true)") == 1
    assert "exitReason: f.get('exitReason')" in _fn("lptConfirmSell")
    track = _fn("renderLowpointTrack")
    assert "_lptReasonFieldsHtml(r, !!r.exitReason)" in track and "미기록" in track and "lpExitReasonSummary(closed, _lpt.exitReasons)" in track
    assert "_lpt.exitReasons = d.exit_reasons || [];" in _fn("_lptLoad")
