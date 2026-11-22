"""v5.398 일봉 준비(세력 꿈틀이 검색식) — stir_screen.stir_check + 저점 관찰 연결 + 화면 칩. 표시 전용.

실데이터 픽스처 test_fixtures/stir_screen_20261120.json.gz: 2026-11-20 종가까지 naver 통합 일봉(300일 조회) — 통과 20종목
(작가 키움 결과 화면에 보이는 9종목 포함)과, 조건 하나씩만 어긋난 종목 4개씩(A·BC·D·E).
사보타주 확인(2026-11-23, 전부 FAIL 확인 후 원복): A를 단순이평으로 / B를 금일 포함 5봉으로 / E 문턱 3% /
관찰 추적에서 daily_ready 호출 제거 / 칩을 stir 값 없이 항상 표시.
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
for p in (os.path.join(ROOT, "scripts", "screens"), os.path.join(ROOT, "scripts", "measurements")):
    sys.path.insert(0, p)
import stir_screen as ss  # noqa: E402
import lowpoint_watch as w  # noqa: E402

FX = json.load(gzip.open(os.path.join(ROOT, "test_fixtures", "stir_screen_20261120.json.gz"), "rt", encoding="utf-8"))
AUTHOR_VISIBLE = ["삼일기업공사", "대웅", "경동나비엔", "삼성에피스홀딩스", "대현", "한국캐피탈", "광주신세계", "정상제이엘에스", "바이넥스"]


def _df(t):
    x = FX["tickers"][t]
    df = pd.DataFrame(x["ohlcv"], columns=["Open", "High", "Low", "Close", "Volume"], index=pd.to_datetime(x["index"]))
    return df[df.Close > 0]


def test_real_hits_include_author_visible():
    got = {FX["tickers"][t]["name"] for t in FX["hits"] if ss.stir_check(_df(t))["stir"]}
    assert len(got) == 20 and set(AUTHOR_VISIBLE) <= got and "노머스" in got and "바이넥스" in got


@pytest.mark.parametrize("part", ["A", "BC", "D", "E"])
def test_near_misses_fail_exactly_that_part(part):
    for t in FX["near_miss"][part]:
        r = ss.stir_check(_df(t))
        assert r["stir"] is False and [k for k, v in r["stir_parts"].items() if not v] == [part], (t, r)


def test_bc_uses_previous_five_bars_not_today():
    """B·C는 금일 제외(1봉전 5봉) — 오늘 거래량만 크고 앞 5봉이 작으면 통과 못 한다(반대도)."""
    t = FX["hits"][0]
    df = _df(t).copy()
    df.iloc[-6:-1, df.columns.get_loc("Volume")] = 1.0
    df.iloc[-1, df.columns.get_loc("Volume")] = 1e9
    assert ss.stir_check(df)["stir_parts"]["BC"] is False
    df.iloc[-6:-1, df.columns.get_loc("Volume")] = 1e7
    df.iloc[-1, df.columns.get_loc("Volume")] = 1.0
    assert ss.stir_check(df)["stir_parts"]["BC"] is True


def test_short_or_empty_is_none():
    t = FX["hits"][0]
    assert ss.stir_check(_df(t).iloc[:ss.MIN_BARS - 1])["stir"] is None
    assert ss.stir_check(None)["stir"] is None
    r = ss.stir_check(_df(t))
    assert r["stir_date"] == "2026-11-20" and set(r["stir_ema"]) == {"20", "60", "115"}


def test_watch_daily_ready_uses_confirmed_bars_only():
    t = FX["hits"][0]
    df = _df(t)
    assert w.daily_ready(df, "2026-11-20")["stir"] is True
    later = pd.concat([df, pd.DataFrame([[1, 1, 1, 1, 1]], columns=df.columns, index=[pd.Timestamp("2026-11-23")])])
    r = w.daily_ready(later, "2026-11-20")                      # 확정 전(11-23) 봉은 안 본다
    assert r["stir"] is True and r["stir_date"] == "2026-11-20"
    assert w.daily_ready(df, None)["stir"] is None


def test_track_writes_stir_fields():
    src = open(os.path.join(ROOT, "scripts", "screens", "lowpoint_watch.py"), encoding="utf-8").read()
    body = src[src.index("def track("):]
    assert body.count("u.update(daily_ready(daily, thru))") == 1


def _extract_js(src, name):
    a = src.index(f"function {name}(")
    i = src.index("{", a)
    d = 0
    for j in range(i, len(src)):
        d += {"{": 1, "}": -1}.get(src[j], 0)
        if d == 0:
            return src[a:j + 1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_front_chip():
    src = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
    assert src.count("${lpwStirChipHtml(r)}") == 2
    js = "const _escapeHtml=s=>String(s);" + _extract_js(src, "lpwStirChip") + _extract_js(src, "lpwStirChipHtml") + \
        "console.log(JSON.stringify([lpwStirChipHtml({stir:true,stir_date:'2026-11-20',stir_gap_pct:-0.96,stir_ema:{'20':6503,'60':6552,'115':7333}})," \
        "lpwStirChipHtml({stir:false}),lpwStirChipHtml({stir:null}),lpwStirChipHtml({})]))"
    out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
    assert "일봉 준비" in out[0] and "6503·6552·7333" in out[0] and "-0.96%" in out[0] and "측정 전" in out[0]
    assert out[1:] == ["", "", ""]
