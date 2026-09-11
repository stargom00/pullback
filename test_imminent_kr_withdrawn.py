"""돌파임박 KR 종가진입 철회 반영 검증 (v5.249, 사용자 지시).

근거: docs/confirm_entry_close_bench_revalidation.md §2 — 벤치마크 룩어헤드를
고친 재검증 R1(EV 0.138·z 1.84)/R2(0.148·1.93) 둘 다 원래 기준 미달로 철회.

검증하는 것:
1. get_calendar()를 실제로 호출해(네트워크는 mocked_env로 차단, 응답 조립
   경로는 그대로) 오늘 확인된 돌파임박 KR — pending_watch/auto_watch 두
   출처 모두 — 이 today_decision.immediate에 안 들어가고 interest(🔎)에는
   들어가는지. 같은 요청에 종가베팅 후보를 함께 넣어 immediate 버킷 자체는
   정상 작동함을 양성 대조로 확인(immediate가 늘 비어서 통과하는 tautology
   방지).
2. today_decision 어디에도 "0.157" 인용이 남지 않았는지.
3. static/index.html의 🔴 기준 툴팁·확인진입 배너가 철회를 반영했는지.
4. PAPER_TRACK_BACKTEST_EV의 KR 5탭 값이 재검증 문서 §2.3의 R2 열과
   일치하는지(문서↔코드 동기화 — 한쪽만 고치면 FAIL).
"""
import asyncio
import json as _json
import re
from pathlib import Path

import pandas as pd

from test_fetch_market_data_all_merge import mocked_env  # noqa: F401  (fixture 재사용)

import app

ROOT = Path(__file__).resolve().parent
INDEX_PATH = ROOT / "static" / "index.html"
REVAL_DOC = ROOT / "docs" / "confirm_entry_close_bench_revalidation.md"

PW_TICKER = "005380.KS"      # pending_watch 출처 돌파임박 KR
AW_TICKER = "000270.KS"      # auto_watch 출처 돌파임박 KR
JG_TICKER = "000660.KS"      # 종가베팅(양성 대조 — immediate에 있어야 함)


def _today():
    return app.datetime.now(app.KST).strftime("%Y-%m-%d")


def _df_ending(today: str, close: float = 105.0, n: int = 40):
    # 마지막 봉이 정확히 오늘이어야 한다 — 옛 코드의 🔴 조건이 confirm_bar_date==today라
    # bdate_range를 쓰면 주말 실행 시 마지막 봉이 금요일이 돼 옛 코드도 interest로
    # 보내버려 테스트가 탐지력을 잃는다(2026-09-12 토요일 sabotage 검증에서 실제로 발견).
    idx = pd.date_range(end=pd.Timestamp(today), periods=n, freq="D")
    return pd.DataFrame({
        "Open": [100.0] * (n - 1) + [101.0], "High": [101.0] * (n - 1) + [106.0],
        "Low": [99.0] * (n - 1) + [100.5], "Close": [100.0] * (n - 1) + [close],
        "Volume": [1000.0] * (n - 1) + [5000.0],
    }, index=idx)


def _setup(monkeypatch):
    today = _today()
    df = _df_ending(today)
    monkeypatch.setattr(app, "_calendar_default_market_session", lambda: "kr")
    monkeypatch.setattr(app, "is_trading_day", lambda market, d: True)
    monkeypatch.setattr(app, "_reignition_watchlist_view", lambda: [])
    monkeypatch.setattr(app, "_calendar_ticker_df", lambda t: df)
    monkeypatch.setattr(app, "_calendar_current_price", lambda t: 105.0)
    monkeypatch.setattr(app, "_scenario_for", lambda d, s: None)
    monkeypatch.setattr(app, "get_signal_snapshot",
                        lambda t, tab: {"signal_low": 100.5, "base_vol50": 1000.0, "signal_date": today})
    # 확인 판정 자체는 이 테스트 대상이 아님 — "확인됐다"로 고정해 버킷 배정만 본다.
    monkeypatch.setattr(app, "_pending_watch_confirm_check",
                        lambda d, pivot, vol_mult_required=1.5, base_vol50=None: (True, 105.0, 5.0))
    monkeypatch.setattr(app, "load_journal", lambda: [{
        "id": 9001, "status": "pending", "ticker": PW_TICKER, "name": "PW돌파임박",
        "pivot": 104.0, "tab": "돌파임박", "market": "KR", "date": today,
    }])
    monkeypatch.setattr(app, "_auto_watch", {f"{AW_TICKER}|돌파임박": {
        "status": "confirmed", "confirmed_at": today, "ticker": AW_TICKER, "name": "AW돌파임박",
        "tab": "돌파임박", "market": "KR", "signal_high": 104.0, "confirm_close": 105.0,
        "confirm_stop": 100.5, "stop": 100.5, "rs": 95, "risk_pct": 4.0, "atr_pct": 3.0,
    }})
    monkeypatch.setattr(app, "_cache", {"kr:jongga": {"snapshot_date": today, "hits": [
        {"ticker": JG_TICKER, "name": "종가베팅대조", "close": 200.0, "turnover_rank": 1},
    ]}})


def _today_decision(monkeypatch):
    _setup(monkeypatch)
    r = asyncio.run(app.get_calendar())
    assert r.status_code == 200
    return _json.loads(r.body)["today_decision"]


def test_imminent_kr_not_in_immediate(mocked_env, monkeypatch):
    td = _today_decision(monkeypatch)
    imm = {it.get("ticker") for it in td["immediate"]}
    assert JG_TICKER in imm, f"양성 대조 실패 — 종가베팅이 immediate에 없음(버킷 자체 고장?): {imm}"
    bad = [t for t in (PW_TICKER, AW_TICKER) if t in imm]
    assert not bad, f"돌파임박 KR이 🔴 immediate에 남아있음(PW={PW_TICKER}, AW={AW_TICKER}): {bad}"
    other = {it.get("ticker") for it in td.get("immediate_other_market") or []}
    assert PW_TICKER not in other and AW_TICKER not in other


def test_imminent_kr_goes_to_interest(mocked_env, monkeypatch):
    td = _today_decision(monkeypatch)
    by_key = {it.get("key"): it for it in td["interest"]}
    pw = by_key.get("pending:9001")
    aw = by_key.get(f"auto_watch:{AW_TICKER}|돌파임박")
    assert pw is not None, f"pending_watch 돌파임박 KR이 🔎 interest에 없음: {list(by_key)}"
    assert aw is not None, f"auto_watch 돌파임박 KR이 🔎 interest에 없음: {list(by_key)}"
    for it in (pw, aw):
        assert it.get("verdict") != "entry_candidate"
        assert it.get("tab") == "돌파임박"
        assert "entry" not in it and "target_2r" not in it   # 진입가/사이즈 미표시(관심 버킷 정의)


def test_no_0157_citation_left_in_today_decision(mocked_env, monkeypatch):
    td = _today_decision(monkeypatch)
    dumped = _json.dumps(td, ensure_ascii=False)
    assert "0.157" not in dumped, "today_decision에 철회된 0.157R 인용이 남아있음"
    assert "확인 시 종가 진입" not in dumped


def test_frontend_texts_reflect_withdrawal():
    text = INDEX_PATH.read_text(encoding="utf-8")
    m = re.search(r"const TODAY_DECISION_INFO =(.*?);\n", text, re.S)
    assert m, "TODAY_DECISION_INFO를 못 찾음"
    info = m.group(1)
    assert "검증 진입 2종" in info and "검증 진입 3종" not in info
    assert "돌파임박 KR — 종가 확인 후 종가 진입" not in info
    b = re.search(r'<div[^>]*id="confirmEntryBanner"[^>]*>(.*?)</div>', text, re.S)
    assert b, "confirmEntryBanner를 못 찾음"
    assert "만 한계적으로 유효" not in b.group(1)
    assert "철회" in b.group(1) and "confirm_entry_close_bench_revalidation.md" in b.group(1)


def test_paper_track_kr_ev_matches_revalidation_doc_r2():
    """문서 §2.3 표의 R2 열(EV / z / nv 중 첫 값)과 코드 상수가 같아야 한다."""
    doc = REVAL_DOC.read_text(encoding="utf-8")
    sec = doc[doc.index("### 2.3"):doc.index("### 2.4")]
    for tab in ("눌림목", "돌파임박", "박스돌파", "돌파", "추세전환"):
        row = next(l for l in sec.splitlines()
                   if re.match(rf"^\|\s*(\*\*)?{tab}(\*\*)?\s*\|", l))
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        r2_cell = cells[4]   # 탭 | 원래 | R0 | R1 | R2 | R3
        r2_ev = float(re.search(r"\d+\.\d+", r2_cell).group(0))
        assert app.PAPER_TRACK_BACKTEST_EV[(tab, "KR")] == r2_ev, (
            f"{tab} KR: 코드 {app.PAPER_TRACK_BACKTEST_EV[(tab, 'KR')]} ≠ 문서 R2 {r2_ev}")
