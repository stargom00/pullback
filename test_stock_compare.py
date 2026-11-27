"""v5.404 두 종목 비교 /compare. 실데이터: test_fixtures/compare_20261126.json.gz(바이넥스·아남전자, 11-26 확정 기준 실제 응답).
사보타주 확인(2026-11-27, 전부 FAIL 확인 후 원복): 손익비 분자·분모 뒤바꿈 / ATR 손절 1.5 → 1 / 투자자 순매수 일수를 순매도로 /
이름 찾기 정확 일치 우선 제거 / 확정 봉 자르기 제거 / 화면 중앙값 행 제거."""
from __future__ import annotations

import asyncio
import gzip
import json
import os
import shutil
import subprocess

import pandas as pd
import pytest

import app
import stock_compare as sc

ROOT = os.path.dirname(os.path.abspath(__file__))
FX = json.load(gzip.open(os.path.join(ROOT, "test_fixtures", "compare_20261126.json.gz"), "rt", encoding="utf-8"))


def test_plan_block():
    p = sc.plan_block(1352, 1205, 1470, 75.0)
    assert p["stop_pct"] == round(1205 / 1352 - 1, 4) and p["target_pct"] == round(1470 / 1352 - 1, 4)
    assert p["rr"] == round(p["target_pct"] / -p["stop_pct"], 2) == 0.8 and p["stop_atr"] == round(147 / 75, 2)
    assert sc.plan_block(None, 1, 2, 3) is None and "rr" not in sc.plan_block(100, None, 120, 3)
    assert "stop_pct" not in sc.plan_block(100, 120, 130, 3)          # 손절이 매수가 위면 무시


def test_flow_parse_real_shape():
    raw = [{"bizdate": "20261126", "foreignerPureBuyQuant": "+46,495", "foreignerHoldRatio": "1.96%", "organPureBuyQuant": "+29,553",
            "individualPureBuyQuant": "-74,406", "closePrice": "1,354", "accumulatedTradingVolume": "739,343"},
           {"bizdate": "20261125", "foreignerPureBuyQuant": "-1,000", "foreignerHoldRatio": "1.90%", "organPureBuyQuant": "0",
            "individualPureBuyQuant": "+1,000", "closePrice": "1,300", "accumulatedTradingVolume": "741,218"},
           {"bizdate": "20261124", "foreignerPureBuyQuant": None, "closePrice": "1,254"}]          # 못 읽는 줄은 버림
    rows = sc.parse_flow_rows(raw)
    assert [r["date"] for r in rows] == ["2026-11-25", "2026-11-26"]
    f = sc.flow_block(rows)
    assert f["organ"]["buy_days"] == 1 and f["foreign"]["buy_days"] == 1 and f["individual"]["buy_days"] == 1
    assert f["foreign_hold"] == {"first": 1.90, "last": 1.96} and f["organ"]["last10"] == [0, 29553]
    assert f["organ"]["eok"] == round(29553 * 1354 / 1e8, 2)
    assert sc.flow_block([]) is None


def test_fixture_values():
    a, b = FX["items"]
    assert a["name"] == "바이넥스" and b["name"] == "아남전자" and FX["through"] == "2026-11-26"
    assert a["plan"]["rr"] == 7.05 and b["plan"]["rr"] == 0.8
    assert a["turnover"]["median20"] > b["turnover"]["median20"] and b["turnover"]["avg20"] > a["turnover"]["avg20"]
    assert b["flow"]["organ"]["buy_days"] == 19 and a["flow"]["organ"]["buy_days"] == 15


def test_resolve(monkeypatch):
    rows = [{"name": "삼성전자우", "ticker": "005935.KS", "value": 3}, {"name": "삼성전자", "ticker": "005930.KS", "value": 1},
            {"name": "삼성전기", "ticker": "009150.KS", "value": 9}]
    monkeypatch.setattr(app, "_kr_name_rows", lambda: rows)
    assert app._compare_resolve("삼성전자")["ticker"] == "005930.KS"            # 정확히 같은 이름이 거래대금보다 먼저
    r = app._compare_resolve("삼성")
    assert r["ticker"] == "009150.KS" and r["candidates"] == ["삼성전자우", "삼성전자"]
    assert "찾지 못했어" in app._compare_resolve("없는종목")["error"] and "넣어 줘" in app._compare_resolve(" ")["error"]
    monkeypatch.setattr(app.naver_kr, "fetch_basic", lambda c: {"suffix": ".KQ", "name": "바이넥스"} if c == "053030" else None)
    assert app._compare_resolve("053030") == {"ticker": "053030.KQ", "name": "바이넥스"}


def test_block_cuts_to_confirmed(monkeypatch):
    idx = pd.bdate_range("2026-01-01", "2026-11-27")
    df = pd.DataFrame({"Open": 10.0, "High": 11.0, "Low": 9.0, "Close": 10.0, "Volume": 1000.0}, index=idx)
    monkeypatch.setattr(app.naver_kr, "fetch_history", lambda t, days: df)
    monkeypatch.setattr(app.naver_kr, "fetch_investor_trend", lambda t, days: None)
    app._COMPARE_CACHE.clear()
    blk = app._compare_block_blocking("000001.KS", "2026-11-26")
    assert blk["date"] == "2026-11-26" and blk["flow"] is None and blk["flow_error"]
    app._COMPARE_CACHE.clear()
    assert "130개 미만" in app._compare_block_blocking("000001.KS", "2026-02-10")["error"]


def test_routes_and_links():
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert src.count('@app.get("/api/compare")') == 1 and src.count('@app.get("/compare")') == 1
    assert open(os.path.join(ROOT, "static", "track.html"), encoding="utf-8").read().count('href="/compare"') == 1
    idx = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
    assert idx.count('href="/compare"') == 1 and "tabs + trackLink + cmpLink" in idx


def _fn(src, name):
    i = src.index(f"function {name}(")
    j = src.index("{", src.index(")", i))
    d = 0
    for k in range(j, len(src)):
        d += {"{": 1, "}": -1}.get(src[k], 0)
        if d == 0:
            return src[i:k + 1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_page_render_real():
    src = open(os.path.join(ROOT, "static", "compare.html"), encoding="utf-8").read()
    js = ("const esc=s=>String(s??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'})[c]);"
          + "".join(_fn(src, n) for n in ("fmtPct", "fmtNum", "fmtEok", "cmpRows", "tableHTML"))
          + f"const j={json.dumps(FX, ensure_ascii=False)};const err={{ok:true,through:'x',items:[j.items[0],{{query:'없음',error:'없음: 종목을 찾지 못했어'}}]}};"
          + "console.log(JSON.stringify([tableHTML(j),tableHTML(err),cmpRows(j.items).filter(r=>!r.sec).length]))")
    out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
    h = out[0]
    for s in ("바이넥스", "아남전자", "손익비 7.05", "손익비 0.8", "20일 중앙값", "19/20일 순매수", "15/20일 순매수", "매물대", "1.5 ATR 손절", "기준일 2026-11-26"):
        assert s in h, s
    assert "종목을 찾지 못했어" in out[1] and out[2] == 34


def test_atr_stop_is_one_and_half_atr():
    """일정한 고저폭 10원 → Wilder ATR 10 → 손절 참고선 = 종가 − 15."""
    import pandas as pd
    idx = pd.bdate_range("2025-01-01", periods=300)
    close = pd.Series([1000.0 + (i % 2) for i in range(300)], index=idx)
    df = pd.DataFrame({"Open": close, "High": close + 5, "Low": close - 5,
                       "Close": close, "Volume": 100000.0}, index=idx)
    out = sc.price_block(df)
    a = out["atr"]["atr"]
    assert 10 <= a <= 11
    assert abs(out["atr"]["stop_1_5"] - (out["close"] - 1.5 * a)) < 0.02


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_page_render_single():
    """v5.405: 한 종목만 — 열 1개, 구분 줄 colspan 2, 다른 종목 이름 없음."""
    src = open(os.path.join(ROOT, "static", "compare.html"), encoding="utf-8").read()
    js = ("const esc=s=>String(s??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'})[c]);"
          + "".join(_fn(src, n) for n in ("fmtPct", "fmtNum", "fmtEok", "cmpRows", "tableHTML"))
          + f"const j={json.dumps(FX, ensure_ascii=False)};const one={{ok:true,through:j.through,items:[j.items[0]]}};"
          + "console.log(JSON.stringify(tableHTML(one)))")
    h = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
    assert "바이넥스" in h and "아남전자" not in h
    assert h.count('colspan="2"') == 9 and 'colspan="3"' not in h
    assert "if(!q.get('a')&&!q.get('b'))" in src and "q.get('a')||q.get('b')" in src


def test_api_single_and_empty(monkeypatch):
    """v5.405: b를 비우면 한 종목만 조회, 둘 다 비우면 ok=False."""
    calls = []
    monkeypatch.setattr(app, "_compare_resolve", lambda q: calls.append(q) or {"ticker": "053030.KQ", "name": "바이넥스"})
    monkeypatch.setattr(app, "_compare_block_blocking", lambda t, th: {k: v for k, v in FX["items"][0].items() if k not in ("query", "ticker", "name", "plan")})
    r = json.loads(asyncio.run(app.compare_api(a="바이넥스", b="  ")).body)
    assert r["ok"] and len(r["items"]) == 1 and calls == ["바이넥스"]
    r = json.loads(asyncio.run(app.compare_api(b="바이넥스")).body)
    assert r["ok"] and len(r["items"]) == 1
    r = json.loads(asyncio.run(app.compare_api()).body)
    assert r["ok"] is False and len(calls) == 2
