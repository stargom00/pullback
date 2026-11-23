"""v5.399 RSI 상승 다이버전스 — divergence.bull_div + 저점 관찰 연결 + 화면 칩. 표시 전용 · 측정 전.

실데이터 픽스처 test_fixtures/divergence_20261120.json.gz: 2026-11-20 종가까지 naver 통합 일봉 600봉(app._downcast 정제) —
일·주 둘 다(브리지텍·엠로), 일봉만(베셀·네오위즈홀딩스 — 네오위즈는 최근 저점 확정), 주봉만(보원케미칼·퓨쳐켐),
저점 신호(11-13 이후)인데 둘 다 없음(HD현대일렉트릭·삼성중공업).
사보타주 확인(2026-11-23, 전부 FAIL 확인 후 원복): RSI_GAP 0으로 / 가격 비교 방향 뒤집기(B > A) / 주봉에 진행 중 주 포함 /
관찰 daily_ready에서 div_fields 제거 / 칩을 값 없이 항상 표시.
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import sys

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "scripts", "screens"))
import divergence as dv  # noqa: E402
import lowpoint_watch as w  # noqa: E402
from test_stir_screen import _extract_js  # noqa: E402

FX = json.load(gzip.open(os.path.join(ROOT, "test_fixtures", "divergence_20261120.json.gz"), "rt", encoding="utf-8"))
THRU = FX["through"]


def _df(t):
    x = FX["tickers"][t]
    return pd.DataFrame({"Close": x["close"]}, index=pd.to_datetime(x["index"]))


@pytest.mark.parametrize("t", list(FX["tickers"]))
def test_real_cases(t):
    f = dv.div_fields(_df(t), THRU)
    exp = FX["tickers"][t]["expect"]
    assert (f["div_day"] is not None) == (exp in ("both", "day")), (t, f)
    assert (f["div_week"] is not None) == (exp in ("both", "week")), (t, f)
    for x in (f["div_day"], f["div_week"]):
        if x:
            assert x["b_close"] < x["a_close"] and x["b_rsi"] - x["a_rsi"] >= dv.RSI_GAP and x["a_rsi"] < dv.RSI_A_MAX
            assert x["a_date"] < x["b_date"] <= THRU


def test_known_values():
    d = dv.div_fields(_df("064480.KQ"), THRU)["div_day"]
    assert (d["a_date"], d["a_close"], d["a_rsi"], d["b_date"], d["b_close"], d["b_rsi"], d["b_confirmed"]) == \
        ("2026-11-10", 2125.0, 24.4, "2026-11-20", 2115.0, 34.0, False)
    n = dv.div_fields(_df("042420.KQ"), THRU)["div_day"]
    assert n["b_date"] == "2026-11-17" and n["b_confirmed"] is True


def test_no_lookahead_and_weekly_drops_open_week():
    """마지막 봉 뒤 데이터는 안 본다 · 주봉은 through보다 뒤 라벨(진행 중 주)을 뺀다."""
    df = _df("058970.KQ")
    cut = df[df.index <= "2026-11-18"]                                  # 수요일까지 — 그 주(11-20 라벨)는 진행 중
    wk = dv.weekly_closed(cut["Close"], "2026-11-18")
    assert str(wk.index[-1].date()) == "2026-11-13"
    assert dv.div_fields(cut, "2026-11-18") == dv.div_fields(cut.copy(), "2026-11-18")
    full = dv.div_fields(df, THRU)["div_day"]
    assert full["b_date"] == "2026-11-20"                               # 11-20 봉을 넣어야만 그 날짜가 저점이 된다
    assert (dv.div_fields(cut, "2026-11-18")["div_day"] or {}).get("b_date") != "2026-11-20"


def test_short_or_empty():
    assert dv.div_fields(None, THRU) == {"div_day": None, "div_week": None}
    assert dv.div_fields(_df("064480.KQ").iloc[:5], THRU) == {"div_day": None, "div_week": None}


def test_watch_daily_ready_carries_div():
    d = _df("064480.KQ")
    d = d.assign(Open=d.Close, High=d.Close, Low=d.Close, Volume=1e6)
    out = w.daily_ready(d, THRU)
    assert out["div_day"]["b_date"] == "2026-11-20" and out["div_week"] is not None and "stir" in out
    assert w.daily_ready(None, THRU)["div_day"] is None


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_chip():
    src = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
    assert src.count("${lpwDivChipHtml(r)}") == 2
    js = "const _escapeHtml=s=>String(s);" + _extract_js(src, "lpwDivChip") + _extract_js(src, "lpwDivChipHtml") + \
        "const x={a_date:'2026-11-10',a_close:2125,a_rsi:24.4,b_date:'2026-11-20',b_close:2115,b_rsi:34,b_confirmed:false};" \
        "console.log(JSON.stringify([lpwDivChipHtml({div_day:x,div_week:x}),lpwDivChipHtml({div_day:x,div_week:null})," \
        "lpwDivChipHtml({div_day:null,div_week:{...x,b_confirmed:true}}),lpwDivChipHtml({}),lpwDivChipHtml({div_day:null,div_week:null})]))"
    out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
    assert ">다이버전스 일·주<" in out[0] and "미확정" in out[0]
    assert ">다이버전스 일<" in out[1] and ">다이버전스 주<" in out[2] and "미확정" not in out[2]
    assert out[3] == "" and out[4] == ""


# ── v5.400 다이버전스+양봉 진입 자리 — 측정(2026-11-24_lowpoint_entry_timing) 후보 B 트리거와 같은 날을 고르는가 ──
PAR = json.load(gzip.open(os.path.join(ROOT, "test_fixtures", "div_entry_parity.json.gz"), "rt"))


def _pdf(x):
    return pd.DataFrame({"Open": x["open"], "Close": x["close"]}, index=pd.to_datetime(x["index"]))


@pytest.mark.parametrize("x", PAR["pos"], ids=lambda x: x["ticker"] + "@" + x["base_date"])
def test_entry_matches_measurement_trigger(x):
    """측정 결과 JSON의 B 트리거 6건(실데이터, 정제 일봉 전체 이력 — RSI 시드를 맞추려고 앞을 자르지 않음)."""
    e = dv.entry_signal(_pdf(x), x["base_date"])
    assert e is not None and e["date"] == x["trigger_date"] and e["listed_ok"] is True
    assert e["b_date"] > x["base_date"] and e["bars_after"] <= dv.ENTRY_WINDOW


@pytest.mark.parametrize("x", PAR["neg"], ids=lambda x: x["ticker"] + "@" + x["base_date"])
def test_entry_none_when_measurement_had_no_trigger(x):
    assert dv.entry_signal(_pdf(x), x["base_date"]) is None


def test_entry_no_lookahead_and_bad_base():
    x = PAR["pos"][0]
    df = _pdf(x)
    cut = df[df.index < x["trigger_date"]]
    assert dv.entry_signal(cut, x["base_date"]) is None                  # 트리거 날 봉이 없으면 아직 없음
    assert dv.entry_signal(df, "1999-01-01") is None and dv.entry_signal(None, x["base_date"]) is None
    assert dv.entry_signal(df, None) is None


def test_watch_daily_ready_carries_entry():
    x = PAR["pos"][2]
    d = _pdf(x).assign(High=lambda f: f[["Open", "Close"]].max(axis=1), Low=lambda f: f[["Open", "Close"]].min(axis=1), Volume=1e6)
    out = w.daily_ready(d, x["index"][-1], x["base_date"])
    assert out["div_entry"]["date"] == x["trigger_date"]
    assert w.daily_ready(d, x["index"][-1])["div_entry"] is None and w.daily_ready(None, x["index"][-1])["div_entry"] is None
    src = open(os.path.join(ROOT, "scripts", "screens", "lowpoint_watch.py"), encoding="utf-8").read()
    assert src.count('daily_ready(daily, thru, r.get("base_date"))') == 1


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_entry_chip():
    src = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
    assert src.count("${lpwDivEntryChipHtml(r)}") == 2
    js = "const _escapeHtml=s=>String(s);" + _extract_js(src, "lpwDivEntryChip") + _extract_js(src, "lpwDivEntryChipHtml") + \
        "const e={date:'2026-11-20',close:1000,b_date:'2026-11-17',bars_after:5,listed_ok:true};" \
        "console.log(JSON.stringify([lpwDivEntryChipHtml({tf:'week',div_entry:e}),lpwDivEntryChipHtml({tf:'month',div_entry:e})," \
        "lpwDivEntryChipHtml({tf:'week',div_entry:{...e,listed_ok:false}}),lpwDivEntryChipHtml({tf:'week',div_entry:null}),lpwDivEntryChipHtml({tf:'week'})]))"
    out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
    assert "lpw-dentry" in out[0] and "🎯 다이버전스+양봉 11/20" in out[0] and "70.1%" in out[0]
    assert "lpw-dentry" not in out[1] and "월봉 신호는 측정 밖" in out[1]
    assert "lpw-dentry" not in out[2] and "상장 1년 미만" in out[2]
    assert out[3] == "" and out[4] == ""
