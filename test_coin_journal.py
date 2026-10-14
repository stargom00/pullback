"""v5.347 코인 일지 — 평단·ROI·수수료 차감 ROI(손 계산), 검증 400, API CRUD·rev, 상태 자동 전이, 시각 정규화, 프론트 시각 함수.

사용자 지시: "사보타주: 덜어낸 가격을 종료가로 바꿔 계산하면 추가 2회 케이스가 FAIL해야 한다."
사보타주 확인(2026-10-14, FAIL 확인 후 원복): legs()에서 추가분 청산가를 trim_px 대신 exit_px로 → test_calc_two_adds_and_trim_at_other_price FAIL ·
평단을 증거금×배율 가중(Σ진입가×notional ÷ Σnotional)으로 → test_calc_two_adds_and_trim_at_other_price FAIL ·
덜어내기 검증 제거 → test_trim_without_adds_is_400 FAIL.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coin_journal as cj  # noqa: E402

ROOT = Path(__file__).resolve().parent
LEV = 10


def _rec(**kw):
    base = {"symbol": "BTCUSDT", "tf": "5m", "side": "long", "leverage": LEV,
            "entry": {"price": 80000, "margin": 100, "time": "2026-10-14T10:05"},
            "target": {"text": "15분봉 구름 아랫선", "price": 83650}}
    base.update(kw)
    return cj.normalize(base, creating=True)


# ── 계산(손 계산 대조) ──────────────────────────────────────────────
def test_calc_entry_only_long():
    r = _rec(exit={"price": 80800, "time": "2026-10-14T12:35"}, exit_reason="목표도달")
    c = cj.calc(r)
    hand = (80800 - 80000) / 80000 * LEV * 100                    # 10 USDT
    fee = 100 * LEV * 0.0012                                       # 1.2
    assert c["avg_entry"] == pytest.approx(80000) and c["pnl"] == pytest.approx(hand)
    assert c["roi_pct"] == pytest.approx(hand / 100 * 100) and c["roi_net_pct"] == pytest.approx((hand - fee) / 100 * 100)
    assert c["duration_min"] == pytest.approx(150) and c["status"] == "종료"


def test_calc_two_adds_and_trim_at_other_price():
    """추가 2회 + 덜어내기(79,500)와 종료(80,500)가 다른 가격 — 추가분은 덜어낸 가격, 1차는 종료가로 청산."""
    r = _rec(add1={"price": 79000, "margin": 100, "time": "2026-10-14T10:30"},
             add2={"price": 78000, "margin": 200, "time": "2026-10-14T10:50"},
             trim={"price": 79500, "time": "2026-10-14T11:20"},
             exit={"price": 80500, "time": "2026-10-14T13:00"}, exit_reason="포기선")
    c = cj.calc(r)
    legs = [(80000, 100, 80500), (79000, 100, 79500), (78000, 200, 79500)]
    notional = sum(m * LEV for _, m, _ in legs)
    qty = sum(m * LEV / p for p, m, _ in legs)
    hand_avg = notional / qty                                      # 수량 가중 = 바이낸스 평단
    hand_pnl = sum((x - p) / p * LEV * m for p, m, x in legs)
    hand_fee = sum(m * LEV * 0.0012 for _, m, _ in legs)
    assert c["avg_entry"] == pytest.approx(hand_avg) and round(c["avg_entry"], 2) == 78741.31   # 4000 ÷ (1000/80000 + 1000/79000 + 2000/78000)
    assert c["pnl"] == pytest.approx(hand_pnl) and c["roi_pct"] == pytest.approx(hand_pnl / 400 * 100)
    assert c["roi_net_pct"] == pytest.approx((hand_pnl - hand_fee) / 400 * 100)
    assert c["n_adds"] == 2 and [l["close"] for l in c["legs"]] == [80500, 79500, 79500]


def test_calc_short():
    r = _rec(side="short", add1={"price": 81000, "margin": 100, "time": "2026-10-14T10:30"},
             exit={"price": 79000, "time": "2026-10-14T11:00"}, exit_reason="목표도달")
    c = cj.calc(r)
    hand = -(79000 - 80000) / 80000 * LEV * 100 + -(79000 - 81000) / 81000 * LEV * 100   # 숏은 부호 반대, 덜어내기 없음 → 추가분도 종료가
    assert c["pnl"] == pytest.approx(hand) and c["roi_pct"] == pytest.approx(hand / 200 * 100) and c["pnl"] > 0
    assert c["roi_net_pct"] == pytest.approx((hand - 200 * LEV * 0.0012) / 200 * 100)


def test_no_exit_no_roi_even_if_trimmed():
    r = _rec(add1={"price": 79000, "margin": 100}, trim={"price": 79500})
    c = cj.calc(r)
    assert c["roi_pct"] is None and c["roi_net_pct"] is None and c["pnl"] is None and c["status"] == "덜어냄·보유중"


def test_status_auto():
    assert _rec()["status"] == "진행중"
    assert _rec(add1={"price": 79000, "margin": 100}, trim={"price": 79500})["status"] == "덜어냄·보유중"
    assert _rec(exit={"price": 80100}, exit_reason="충동")["status"] == "종료"


def test_time_normalized_nz_with_dst_and_iso_kept():
    assert cj.normalize_time("2026-10-14T10:05") == "2026-10-14T10:05:00+13:00"     # NZDT(서머타임)
    assert cj.normalize_time("2026-06-14T10:05") == "2026-06-14T10:05:00+12:00"     # NZST
    assert cj.normalize_time("2026-10-13T21:05:00Z") == "2026-10-13T21:05:00+00:00"
    assert cj.normalize_time("") is None
    with pytest.raises(ValueError):
        cj.normalize_time("어제 밤")


def test_validation_rules():
    assert cj.invalid(_rec()) is None
    assert "추가 진입이 없는데" in cj.invalid(_rec(trim={"price": 79500}))
    assert "추가 진입 1" in cj.invalid(_rec(add2={"price": 79000, "margin": 100}))
    assert "종료 사유" in cj.invalid(_rec(exit={"price": 80100}))
    assert cj.invalid(_rec(exit={"price": 80100}, exit_reason="감")) is not None
    assert cj.invalid(_rec(tf="1h")) is not None and cj.invalid(_rec(side="up")) is not None
    assert cj.EXIT_REASONS == ("목표도달", "포기선", "강제청산", "충동", "기타") and cj.FEE_ROUNDTRIP == 0.0012


def test_stats_closed_only():
    recs = [_rec(exit={"price": 80800}, exit_reason="목표도달"),
            _rec(add1={"price": 79000, "margin": 100}, exit={"price": 79000}, exit_reason="포기선"),
            _rec()]
    st = cj.stats(recs)
    c0, c1 = cj.calc(recs[0]), cj.calc(recs[1])
    assert st["n_closed"] == 2 and st["by_reason"]["목표도달"] == 1 and st["by_reason"]["포기선"] == 1
    assert st["avg_roi_pct"] == pytest.approx((c0["roi_pct"] + c1["roi_pct"]) / 2)
    assert st["avg_roi_net_pct"] == pytest.approx((c0["roi_net_pct"] + c1["roi_net_pct"]) / 2)
    assert st["with_adds"] == {"n": 1, "avg_roi_pct": pytest.approx(c1["roi_pct"])}
    assert st["no_adds"] == {"n": 1, "avg_roi_pct": pytest.approx(c0["roi_pct"])}


# ── API ──────────────────────────────────────────────────────────────
class _Req:
    def __init__(self, body=None):
        self._b = body
        self.client = type("C", (), {"host": "127.0.0.1"})()
        self.headers = {"user-agent": "pytest"}

    async def json(self):
        return self._b


@pytest.fixture
def api(monkeypatch, tmp_path):
    import app
    monkeypatch.setattr(app, "COIN_JOURNAL_PATH", str(tmp_path / "coin_journal.json"))
    monkeypatch.setattr(app, "COIN_JOURNAL_DELETE_LOG_PATH", str(tmp_path / "coin_journal_deletions.log"))
    run = lambda coro: (lambda r: (r.status_code, json.loads(r.body)))(asyncio.run(coro))   # noqa: E731
    return app, run, tmp_path


QUICK = {"symbol": "btcusdt", "leverage": 10, "entry": {"price": 80000, "margin": 100}, "target": {"text": "구름 아랫선", "price": 83650}}


def test_api_crud_and_status_transitions(api):
    app, run, tmp = api
    st, d = run(app.coin_journal_create(_Req({"record": QUICK})))                    # 모바일 빠른 입력만으로 저장
    assert st == 200 and d["record"]["id"] == 1 and d["record"]["rev"] == 1 and d["record"]["status"] == "진행중"
    assert d["record"]["symbol"] == "BTCUSDT" and d["record"]["tf"] == "5m" and d["record"]["side"] == "long"
    assert d["record"]["entry"]["time"].endswith(("+13:00", "+12:00"))               # 시각 = 지금(NZ, 오프셋 포함)
    rec = d["record"]
    rec2 = {**rec, "add1": {"price": 79000, "margin": 100, "time": "2026-10-14T10:30"}, "trim": {"price": 79500, "time": "2026-10-14T11:20"}}
    st, d = run(app.coin_journal_put(1, _Req({"record": rec2, "base_rev": 1})))
    assert st == 200 and d["record"]["status"] == "덜어냄·보유중" and d["record"]["rev"] == 2
    st, d = run(app.coin_journal_put(1, _Req({"record": rec2, "base_rev": 1})))       # 낡은 rev → 409
    assert st == 409 and d["code"] == "conflict"
    rec3 = {**d["record"], "exit": {"price": 80500, "time": "2026-10-14T13:00"}, "exit_reason": "목표도달", "review": "본전 덜어내기 지킴"}
    st, d = run(app.coin_journal_put(1, _Req({"record": rec3, "base_rev": 2})))
    assert st == 200 and d["record"]["status"] == "종료"
    st, d = run(app.coin_journal_list())
    assert st == 200 and len(d["records"]) == 1 and d["records"][0]["calc"]["roi_pct"] is not None
    assert d["stats"]["n_closed"] == 1 and d["exit_reasons"][0] == "목표도달" and "calc" not in json.loads((tmp / "coin_journal.json").read_text())[0]
    st, d = run(app.coin_journal_delete(1, _Req()))
    assert st == 200 and d["deleted"] == 1
    assert run(app.coin_journal_list())[1]["records"] == []
    log = (tmp / "coin_journal_deletions.log").read_text(encoding="utf-8")
    assert '"id": 1' in log and "본전 덜어내기 지킴" in log
    st, d = run(app.coin_journal_put(1, _Req({"record": rec3, "base_rev": 3})))       # 삭제된 기록은 되살리지 않는다
    assert st == 409 and d["code"] == "gone"


def test_trim_without_adds_is_400(api):
    app, run, _ = api
    st, d = run(app.coin_journal_create(_Req({"record": {**QUICK, "trim": {"price": 79500}}})))
    assert st == 400 and "덜어낸 가격" in d["error"]
    st, d = run(app.coin_journal_create(_Req({"record": QUICK})))
    assert st == 200
    rec = {**d["record"], "trim": {"price": 79500, "time": "2026-10-14T11:20"}}
    st, d = run(app.coin_journal_put(1, _Req({"record": rec, "base_rev": 1})))
    assert st == 400 and "덜어낸 가격" in d["error"]


def test_other_400s(api):
    app, run, _ = api
    assert run(app.coin_journal_create(_Req({"record": {**QUICK, "exit": {"price": 80100}}})))[0] == 400            # 사유 없음
    assert run(app.coin_journal_create(_Req({"record": {**QUICK, "entry": {"price": 80000, "margin": 100, "time": "어제"}}})))[0] == 400
    assert run(app.coin_journal_create(_Req({"record": {**QUICK, "entry": {"price": 0, "margin": 100}}})))[0] == 400
    assert run(app.coin_journal_create(_Req({"nope": 1})))[0] == 400


def test_separate_from_stock_journal():
    import app
    assert app.COIN_JOURNAL_PATH.endswith("coin_journal.json") and "journal_user" not in app.COIN_JOURNAL_PATH
    src = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    block = src[src.index("// ── v5.347(사용자 지시 2026-10-14) 코인 일지"):src.index("async function onEnterLowpointTrackTab() {")]
    block = "\n".join(ln for ln in block.splitlines() if not ln.lstrip().startswith("//"))   # 주석의 설명 문구는 제외(실행 코드만)
    assert "pullback_journal_v1" not in block and "journalCache" not in block and "setJournal(" not in block and "localStorage" not in block
    assert "tzdata" in (ROOT / "requirements.txt").read_text().split()


# ── 프론트 시각 함수(Node로 원문 실행) ─────────────────────────────────
def _fn(src, name):
    i = src.index(f"function {name}(")
    b = src.index("{", src.index(")", i))
    d = 0
    for k in range(b, len(src)):
        d += {"{": 1, "}": -1}.get(src[k], 0)
        if d == 0:
            return src[i:k + 1]
    raise AssertionError(name)


def test_front_time_and_leg_helpers():
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    src = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    code = "\n".join(_fn(src, n) for n in ("cjNzLocal", "cjTimeBoth", "cjLeg", "cjPct"))
    expr = ('[cjNzLocal("2026-10-13T21:05:00+00:00"), cjNzLocal("2026-06-13T22:05:00+00:00"), cjNzLocal(""), '
            'cjTimeBoth("2026-10-14T10:05:00+13:00"), cjLeg("", "x", "1"), cjLeg("80000", "2026-10-14T10:05", "100"), '
            'cjLeg("79500", ""), cjPct(1.234), cjPct(-2), cjPct(null)]')
    p = subprocess.run(["node", "-e", code + f"\nconsole.log(JSON.stringify({expr}));"], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out[0] == "2026-10-14T10:05" and out[1] == "2026-06-14T10:05" and out[2] == ""
    assert "NZ 10. 14. 10:05" in out[3] and "KST 10. 14. 06:05" in out[3]
    assert out[4] is None and out[5] == {"price": 80000, "time": "2026-10-14T10:05", "margin": 100}
    assert out[6] == {"price": 79500, "time": None} and out[7:] == ["+1.23%", "-2.00%", "—"]
