"""v5.402 두 배 조건 칩 — double_traits.traits가 측정(2026-11-24_lowpoint_double_features.py) F4·F7과 같은 값을 내는가 + 화면 칩.
실데이터: test_fixtures/div_entry_parity.json.gz(정제 일봉 종가, 실종목 9건). 사보타주 확인(2026-11-24, 전부 FAIL 확인 후 원복):
200일선 → 199봉 / F7 범위를 60 → 59봉 / F4 컷 비교를 < → > / 관찰 daily_ready에서 dbl 제거 / 칩을 측정 밖에서도 🚀."""
from __future__ import annotations

import gzip
import importlib.util
import json
import os
import shutil
import subprocess
import sys

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "scripts", "screens"))
import double_traits as dt  # noqa: E402
import lowpoint_watch as w  # noqa: E402
from test_stir_screen import _extract_js  # noqa: E402

_spec = importlib.util.spec_from_file_location("dfm", os.path.join(ROOT, "scripts", "measurements", "2026-11-24_lowpoint_double_features.py"))
dfm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dfm)
PAR = json.load(gzip.open(os.path.join(ROOT, "test_fixtures", "div_entry_parity.json.gz"), "rt"))
CASES = PAR["pos"] + PAR["neg"]


def _df(x):
    c = pd.Series(x["close"], index=pd.to_datetime(x["index"]))
    return pd.DataFrame({"Open": x["open"], "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1e6}, index=c.index)


@pytest.mark.parametrize("x", CASES, ids=lambda x: x["ticker"] + "@" + x["base_date"])
def test_same_values_as_measurement(x):
    df = _df(x)
    jb = df.index.get_loc(pd.Timestamp(x["base_date"]))
    f = dfm.feats(df, jb)
    t = dt.traits(df, x["base_date"])
    assert t is not None
    assert abs(t["f4"] - f["F4_ma200_gap"]) < 1e-4 and abs(t["f7"] - f["F7_range60"]) < 1e-4
    assert t["f4_ok"] == (f["F4_ma200_gap"] <= dt.F4_CUT) and t["f7_ok"] == (f["F7_range60"] > dt.F7_CUT)
    assert t["n_ok"] == int(t["f4_ok"]) + int(t["f7_ok"])


def test_cuts_match_measurement_result_file():
    p = os.path.join(ROOT, "scripts", "measurements", "2026-11-24_lowpoint_double_features.results.json")
    if not os.path.exists(p):
        pytest.skip("결과 JSON은 커밋하지 않는다")
    e = json.load(open(p))["explore"]
    assert dt.F4_CUT == e["F4_ma200_gap"]["cuts"][0] and dt.F7_CUT == e["F7_range60"]["cuts"][2]


def test_boundaries_and_none():
    idx = pd.bdate_range("2024-01-01", periods=260)
    close = [100.0] * 200 + [50.0] * 59 + [40.0]
    df = pd.DataFrame({"Open": close, "Close": close}, index=idx)
    t = dt.traits(df, str(idx[-1].date()))
    assert t["f7"] == 0.25 and t["f7_ok"] is False                        # 마지막 60봉 = 50×59 + 40
    sma200 = (100 * 140 + 50 * 59 + 40) / 200
    assert t["f4"] == round(40 / sma200 - 1, 4) and t["f4_ok"] is True    # −53% ≤ −38%
    assert dt.traits(df.iloc[:199], str(idx[198].date())) is None          # 200봉 미만
    assert dt.traits(df, "1999-01-01") is None and dt.traits(None, "2024-01-01") is None and dt.traits(df, None) is None


def test_watch_daily_ready_carries_dbl():
    x = PAR["pos"][0]
    out = w.daily_ready(_df(x), x["index"][-1], x["base_date"])
    assert out["dbl"] is not None and "n_ok" in out["dbl"]
    assert w.daily_ready(_df(x), x["index"][-1])["dbl"] is None


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_chip():
    src = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
    assert src.count("${lpwDblChipHtml(r)}") == 2
    js = "const _escapeHtml=s=>String(s);" + _extract_js(src, "lpwDblChip") + _extract_js(src, "lpwDblChipHtml") + \
        "const both={f4:-0.45,f7:0.9,f4_ok:true,f7_ok:true,n_ok:2,listed_ok:true};const one={f4:-0.2,f7:0.8,f4_ok:false,f7_ok:true,n_ok:1,listed_ok:true};" \
        "console.log(JSON.stringify([lpwDblChipHtml({tf:'week',dbl:both}),lpwDblChipHtml({tf:'week',dbl:one}),lpwDblChipHtml({tf:'month',dbl:both})," \
        "lpwDblChipHtml({tf:'week',dbl:{...both,n_ok:0,f4_ok:false,f7_ok:false}}),lpwDblChipHtml({tf:'week',dbl:null}),lpwDblChipHtml({tf:'week',dbl:{...both,listed_ok:false}})]))"
    out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
    assert "lpw-dbl2" in out[0] and "🚀 두 배 조건 2/2" in out[0] and "37.7%" in out[0] and "−50%" in out[0]
    assert "lpw-dbl1" in out[1] and "두 배 조건 1/2" in out[1] and "30.6%" in out[1] and "60일 출렁임 +80%" in out[1]
    assert "lpw-dbl2" not in out[2] and "월봉 신호는 측정 밖" in out[2]
    assert out[3] == "" and out[4] == ""
    assert "lpw-dbl2" not in out[5] and "상장 1년 미만" in out[5]
