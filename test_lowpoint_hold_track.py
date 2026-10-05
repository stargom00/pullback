"""v5.327 — 저점 추적(보유 종목) · 평가 최신순 정렬 · 평가 카드 접기.

사용자 지시: "추적 페이지 신설: 매매 기록의 보유 종목 수익률을 매일 자동 추적 — 관찰(진입 전)과 짝이 되는 보유 중
페이지" · "평가 페이지 정렬: 최근 추가한 종목이 위로" · "평가 카드 기본 접힘: 요약 한 줄만 보이고 누르면 펼쳐지게".

사보타주 확인(2026-10-06, FAIL 확인 후 원복):
① track_holdings가 종료 기록까지 조회 → test_track_holdings_fetches_only_open FAIL
② lpkGroups 정렬을 수익률 오름차순으로 → test_groups_sync_sort_and_hit FAIL
③ lpeSortEvals에서 관심 우선 제거 → test_eval_sort_interest_then_newest FAIL
④ lpeToggleIgnored가 늘 false(버튼 클릭도 접힘 토글) → test_toggle_ignores_buttons_and_links FAIL
⑤ v5.327 CSS 블록에 전역 규칙(.jr-card-h{cursor:pointer}) 추가 → test_new_css_scoped FAIL
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.join(ROOT, "scripts", "screens"), os.path.join(ROOT, "scripts", "measurements")):
    sys.path.insert(0, p)
import harness  # noqa: E402
import lowpoint as lp  # noqa: E402
import lowpoint_watch as w  # noqa: E402

import app  # noqa: E402

KST = timezone(timedelta(hours=9))
SRC = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()

# 실데이터 모양: KR(.KS)·US·코인(KRW-), 단기·장기, 종료 기록, 분할 종료(partial_of)
TRADES = [
    {"id": 1, "kind": "단기", "mkt": "KR", "code": "019680.KS", "name": "대교", "buyDate": "2026-09-22", "buyPrice": 1650, "qty": 100},
    {"id": 2, "kind": "단기", "mkt": "US", "code": "AVA", "name": "Avista Corporation Common Stock", "buyDate": "2026-10-05", "buyPrice": 35.0, "qty": 10},
    {"id": 3, "kind": "장기", "mkt": "UPBIT", "code": "KRW-XRP", "name": "엑스알피(리플)", "buyDate": "2026-10-01", "buyPrice": 3100.5, "qty": 12.5},
    {"id": 4, "kind": "단기", "mkt": "KR", "code": "005930.KS", "name": "삼성전자", "buyDate": "2026-09-01", "buyPrice": 70000, "qty": 1,
     "sellDate": "2026-09-20", "sellPrice": 72000},
    {"id": 5, "kind": "단기", "mkt": "KR", "code": "019680.KS", "name": "대교", "buyDate": "2026-09-22", "buyPrice": 1650, "qty": 30,
     "sellDate": "2026-09-29", "sellPrice": 1720, "partial": True, "partial_of": 1},
]


def _df(close):
    idx = pd.bdate_range(end="2026-10-05", periods=3)
    return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": [close * 0.99, close * 0.995, close]}, index=idx)


# ── 서버 ───────────────────────────────────────────────────────────
def test_track_holdings_fetches_only_open(monkeypatch):
    import naver_kr
    import upbit
    asked = {"kr": [], "us": [], "coin": []}
    monkeypatch.setattr(naver_kr, "fetch_history", lambda c, days=730: asked["kr"].append((c, days)) or _df(1700))
    monkeypatch.setattr(harness, "_fetch_us_batch",
                        lambda t, period="2y", auto_adjust=True: asked["us"].append((tuple(t), period, auto_adjust)) or {x: _df(36) for x in t})
    monkeypatch.setattr(upbit, "fetch_candles", lambda m, unit="days", count=200: asked["coin"].append((m, unit, count)) or _df(3200))
    for name in ("kr_universe", "us_universe", "fetch_kr", "fetch_us"):
        monkeypatch.setattr(lp, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError("전수 조회")))
    prices, s = w.track_holdings(TRADES, date(2026, 10, 6), "now")
    assert asked == {"kr": [("019680.KS", w.HOLD_LOOKBACK_DAYS)], "us": [(("AVA",), "1mo", lp.US_AUTO_ADJUST)],
                     "coin": [("KRW-XRP", "days", w.HOLD_LOOKBACK_DAYS)]}
    assert set(prices) == {"019680.KS", "AVA", "KRW-XRP"} and "005930.KS" not in prices
    assert prices["KRW-XRP"] == {"mkt": "UPBIT", "last_close": 3200.0, "last_date": "2026-10-05", "checked_at": "now"}
    assert s == {"held": 3, "fetched": 3, "failed": []}


def test_failed_keeps_previous_and_drops_sold():
    prev = {"AVA": {"mkt": "US", "last_close": 34.0, "last_date": "2026-10-01", "checked_at": "old"},
            "005930.KS": {"mkt": "KR", "last_close": 1, "last_date": "x", "checked_at": "old"}}
    prices, s = w.track_holdings(TRADES, date(2026, 10, 6), "now", prev,
                                 fetch=lambda items, today: {"019680.KS": {"last_close": 1700.0, "last_date": "2026-10-05"}})
    assert prices["AVA"]["checked_at"] == "old" and "KRW-XRP" not in prices and "005930.KS" not in prices
    assert s["failed"] == ["AVA", "KRW-XRP"]


def test_job_writes_holdings_track_and_api(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "LOWPOINT_DATA_PATH", str(tmp_path / "d.json"))
    monkeypatch.setattr(app, "LOWPOINT_LATEST_PATH", str(tmp_path / "r.json"))
    monkeypatch.setattr(app, "LOWPOINT_STATE_PATH", str(tmp_path / "s.json"))
    app._rec_list_write(app.LP_TRADES_PATH, TRADES)
    fake = lambda items, today: {i["code"]: {"last_close": 10.0, "last_date": "2026-10-05"} for i in items}
    monkeypatch.setattr(w.track_holdings, "__defaults__", (None, fake))
    s = app._lp_watch_job_blocking("watch", datetime(2026, 10, 6, 7, 0, tzinfo=KST))
    assert s["counts"]["holdings"]["held"] == 3
    d = json.loads(asyncio.run(app.lp_holdings_track()).body)
    assert set(d["prices"]) == {"019680.KS", "AVA", "KRW-XRP"} and d["checked_at"].startswith("2026-10-06T07:00")


# ── 프론트(node, production 원문) ───────────────────────────────────
def _fn(name):
    start = SRC.index(f"function {name}(")
    i = SRC.index("{", SRC.index(")", start))
    d = 0
    for j in range(i, len(SRC)):
        d += {"{": 1, "}": -1}.get(SRC[j], 0)
        if d == 0:
            return SRC[start:j + 1]
    raise AssertionError(name)


def _js(expr, fns, pre=""):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    src = ("const _escapeHtml = s => String(s);\nconst kstStr = () => '2026-10-06';\nconst tvUrl = (c, m) => 'tv:' + c;\n"
           "const LPT_KINDS = ['단기', '장기'];\n" + "\n".join(_fn(f) for f in fns) + "\n" + pre)
    p = subprocess.run(["node", "-e", src + f"\nconsole.log(JSON.stringify({expr}));"], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


KFNS = ("lpkGroups", "lpTargetPrice", "lpReturnPct", "lpwDays", "lpwBar")
TP = {"단기": 4, "장기": 100}
PRICES = {"019680.KS": {"last_close": 1716, "last_date": "2026-10-05"},      # +4.0% = 목표가 정확히 → 도달
          "AVA": {"last_close": 34.3, "last_date": "2026-10-05"},           # −2%
          "KRW-XRP": {"last_close": 4650.75, "last_date": "2026-10-05"}}    # +50%


def test_groups_sync_sort_and_hit():
    g = _js(f"lpkGroups({json.dumps(TRADES)}, {json.dumps(PRICES)}, {json.dumps(TP)}, '2026-10-06').map(g => [g.kind, g.rows.map(x => [x.r.code, Math.round(x.ret * 100) / 100, x.hit, x.days])])", KFNS)
    assert g == [["단기", [["019680.KS", 4.0, True, 14], ["AVA", -2.0, False, 1]]],
                 ["장기", [["KRW-XRP", 50.0, False, 5]]]]
    # 동기화: 종료하면 빠지고, 수정하면 바로 반영, 새 기록은 가격 전이라 아래(현재가 없음)
    edited = [*TRADES[:1], {**TRADES[1], "buyPrice": 30.0}, {**TRADES[2], "sellDate": "2026-10-06", "sellPrice": 4600},
              {"id": 9, "kind": "단기", "mkt": "KR", "code": "000660.KS", "name": "SK하이닉스", "buyDate": "2026-10-06", "buyPrice": 1, "qty": 1}]
    g2 = _js(f"lpkGroups({json.dumps(edited)}, {json.dumps(PRICES)}, {json.dumps(TP)}, '2026-10-06').map(g => [g.kind, g.rows.map(x => [x.r.code, x.ret == null ? null : Math.round(x.ret * 100) / 100])])", KFNS)
    assert g2 == [["단기", [["AVA", 14.33], ["019680.KS", 4.0], ["000660.KS", None]]]]


@pytest.mark.parametrize("ret,goal,side,width", [(0, 4, "pos", 0), (4, 4, "pos", 100), (6, 4, "pos", 100), (-2, 4, "neg", 50),
                                                 (50, 100, "pos", 50), (100, 100, "pos", 100), (130, 100, "pos", 100)])
def test_progress_bar_uses_target_scale(ret, goal, side, width):
    b = _js(f"lpwBar({ret}, {goal})", ("lpwBar",))
    assert b["side"] == side and b["width"] == pytest.approx(width)


def _render_hold(pre=""):
    fns = KFNS + ("_lptFmt", "_lptPct", "lpDisplayName", "_lptSellFormHtml", "renderLowpointHold")
    state = (f"var _lpt = {{ trades: {json.dumps(TRADES)}, settings: {{ target_pct: {json.dumps(TP)} }}, selling: null, error: null }};\n"
             f"var _lpk = {{ prices: {json.dumps(PRICES)}, checkedAt: '2026-10-06T07:00:30+09:00', failed: [], error: null, busy: false, msg: '' }};\n")
    return _js("renderLowpointHold()", fns, state + pre)


def test_render_hold_rows_and_end_button():
    html = _render_hold()
    assert "단기 · 보유 2 · 종료 후보 1" in html and "장기 · 보유 1" in html
    assert html.count('class="lpt-hit"') == 1 and "종료 후보</span>" in html
    assert html.count("onclick=\"lptStartSell(") == 3 and ">AVA</a>" in html and ">엑스알피(리플)</a>" in html   # US는 티커(저점 화면 규칙)
    assert '<i class="pos" style="width:50.0%">' in html          # 대교 +4% = 목표 → 오른쪽 절반 꽉
    assert 'lpt-sell' not in html
    sold = _render_hold("_lpt.selling = 2;")
    assert sold.count('class="lpt-sell"') == 1 and "lptConfirmSell(this, 2)" in sold   # 매매 기록과 같은 종료 폼


def test_sell_form_shared_with_trades_page():
    assert "_lptSellFormHtml(r, cur, today, 8)" in _fn("renderLowpointTrack")
    assert "_lptSellFormHtml(r, x.cur, today, 6)" in _fn("renderLowpointHold")
    assert SRC.count("lptConfirmSell(this, ${r.id})") == 1


def test_view_wiring():
    t = _fn("renderLowpointTrack")
    assert "['hold', '추적']" in t and "renderLowpointHold()" in t
    assert "lpkLoad().then(renderLowpointTrack)" in _fn("lpSetView")
    assert "fetch('/api/lowpoint/watch/refresh', { method: 'POST' })" in _fn("lpkRefresh")


# ── 평가 정렬·접기 ─────────────────────────────────────────────────
def test_eval_sort_interest_then_newest():
    ev = [{"id": "A", "interest": False, "created_at": "2026-10-04"},
          {"id": "B", "interest": True, "created_at": "2026-10-03"},
          {"id": "C", "interest": False, "created_at": "2026-10-06T09:00:00"},
          {"id": "D", "interest": True, "created_at": "2026-10-05T10:00:00"},
          {"id": "E", "interest": False, "created_at": "2026-10-04"},          # A와 같은 날(옛 형식) → 뒤에 추가된 E가 위
          {"id": "F", "interest": False}]
    assert _js(f"lpeSortEvals({json.dumps(ev)}).map(r => r.id)", ("lpeSortEvals",)) == ["D", "B", "C", "E", "A", "F"]
    assert "const sorted = lpeSortEvals(_lpe.evals);" in _fn("renderLowpointEval")
    assert "created_at: kstStr(new Date(), 19)" in SRC


CARD_FNS = ("lpeTally", "lpeManualEffective", "_lpeMark", "lpDisplayName", "lpPriceNoteMark", "_lpeCard")


def _card(open_):
    rec = {"id": "042000.KQ", "code": "042000.KQ", "name": "카페24", "mkt": "KR", "interest": True, "checked_at": "2026-10-05",
           "manual": {"long_base": "O"}, "memo": "메모",
           "auto": {"items": [{"key": "months12", "label": "완성 월봉 12개 초과", "result": True, "detail": "x"},
                              {"key": "drawdown", "label": "고점 대비 −50%", "result": False, "detail": "y"}], "manual_auto": {}}}
    pre = ("var _lpe = { manualItems: [['rise_rsi','RSI'],['long_base','긴 바닥']], evaluating: {}, memoDraft: {}, open: {} };\n"
           + ("_lpe.open['042000.KQ'] = true;\n" if open_ else ""))
    return _js(f"[_lpeCard({json.dumps(rec)}), lpeTally({json.dumps(rec)}, _lpe.manualItems)]", CARD_FNS, pre)


def test_card_closed_by_default_with_same_tally():
    html, ta = _card(False)
    assert f"O {ta['O']} · X {ta['X']} · 미표시 {ta['blank']}" in html
    assert "lpe-closed" in html and 'aria-expanded="false"' in html
    assert "lpe-table" not in html and "lpeMemo_" not in html            # 본문·메모는 펼쳐야 보인다
    for b in ("재평가", "관심 해제", "삭제", "체크 2026-10-05", "카페24"):
        assert b in html
    html2, ta2 = _card(True)
    assert "lpe-table" in html2 and "lpeMemo_" in html2 and 'aria-expanded="true"' in html2
    assert f"O {ta2['O']} · X {ta2['X']} · 미표시 {ta2['blank']}" in html2 and ta2 == ta


def test_toggle_ignores_buttons_and_links():
    fake = "sel => ({closest: s => (s.split(',').includes(sel) ? {} : null)})"
    got = _js(f"(() => {{ const t = {fake}; return [lpeToggleIgnored(t('button')), lpeToggleIgnored(t('a')), lpeToggleIgnored(t('textarea')), lpeToggleIgnored(t('span')), lpeToggleIgnored(null)]; }})()",
              ("lpeToggleIgnored",))
    assert got == [True, True, True, False, False]
    tog = _fn("lpeToggleCard")
    assert "if (ev && lpeToggleIgnored(ev.target)) return;" in tog and "_lpe.open[id] = !_lpe.open[id];" in tog


def test_new_and_reevaluated_cards_open():
    assert "_lpe.open[id] = true;" in _fn("lpeEvaluate")
    assert "await lpeEvaluate(id)" in _fn("lpeAdd") and "await lpeEvaluate(id)" in _fn("lpeAddHit")
    assert "localStorage" not in _fn("lpeToggleCard") and "localStorage" not in _fn("_lpeCard")


def test_new_css_scoped():
    block = SRC[SRC.index("/* v5.327 평가 카드 접기"):SRC.index("/* /v5.327 */")]
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    sels = re.findall(r"([^{}]+)\{[^{}]*\}", block)
    assert sels and all(".lpe-card" in s for sel in sels for s in sel.split(","))


def test_other_pages_untouched():
    t = _fn("renderLowpointTrack")
    trades_part = t.split("const head = `")[1]
    assert "lpk" not in trades_part and "lpe-sumline" not in trades_part and "lpw-" not in trades_part
    assert "lpk" not in _fn("renderLowpointWatch") and "lpe-" not in _fn("renderLowpointHold")
