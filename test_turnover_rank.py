"""v5.403 거래대금 상위 순위 기록. 실데이터: test_fixtures/turnover_rows_20261126.json.gz(2026-11-26 장중 naver marketValue
상위 60줄 + 시세 날짜가 다른 줄 — ETF·ETN 섞인 실제 형태). 사보타주 확인(2026-11-26, 전부 FAIL 확인 후 원복):
ETF 거르기 제거 / 연속 일수가 끊김을 무시 / 새 진입 판정을 상위 20 기준으로 / 시세 날짜 ≠ 오늘인데 저장 / 스케줄러 호출 제거 / 화면 NEW 글자 뒤집기."""
from __future__ import annotations

import asyncio
import gzip
import json
import os
import shutil
import subprocess

import pytest

import app
import turnover_rank as tr

ROOT = os.path.dirname(os.path.abspath(__file__))
FX = json.load(gzip.open(os.path.join(ROOT, "test_fixtures", "turnover_rows_20261126.json.gz"), "rt", encoding="utf-8"))


def test_snapshot_real_rows():
    day, top = tr.snapshot(FX["rows"])
    assert day == "2026-11-26" and len(top) == tr.KEEP_TOP
    assert [r["rank"] for r in top] == list(range(1, 21))
    assert all(a["value_mil"] >= b["value_mil"] for a, b in zip(top, top[1:]))
    names = {r["name"] for r in top}
    assert not any(k in n.upper() for n in names for k in ("KODEX", "TIGER", "ETN", "KBSTAR", "ACE ", "RISE "))
    etfs = [r for r in FX["rows"] if not r["is_stock"]]
    assert etfs and all(r["code"] not in {t["code"] for t in top} for r in etfs)
    assert top[0]["name"] == "SK하이닉스"


def _day(codes):
    return [{"rank": i + 1, "code": c, "ticker": c + ".KS", "name": "N" + c, "value_mil": 1000 - i, "close": 1.0, "chg_pct": 0.0}
            for i, c in enumerate(codes)]


def test_analyze_moves_streak_new_dropped():
    base = [f"{i:06d}" for i in range(1, 21)]                      # 1~20
    d1 = _day(base)
    d2 = _day(base[1:10] + ["000015"] + base[10:14] + ["000001"] + base[15:20])  # 1위가 15위로, 15위가 10위로
    d3 = _day(["000099"] + base[1:10] + base[10:20])                # 20위 밖이던 000099가 1위
    days = {"2026-11-24": d1, "2026-11-25": d2, "2026-11-26": d3}
    a = tr.analyze(days)
    assert a["day"] == "2026-11-26" and a["prev_day"] == "2026-11-25" and a["n_days"] == 3
    t = {r["code"]: r for r in a["top"]}
    assert t["000099"]["new"] is True and t["000099"]["prev_rank"] is None and t["000099"]["move"] is None
    assert t["000002"]["prev_rank"] == 1 and t["000002"]["move"] == -1 and t["000002"]["streak"] == 3 and t["000002"]["new"] is False
    assert t["000002"]["in_top_20d"] == 3
    dropped = {r["code"]: r for r in a["dropped"]}
    assert dropped["000015"]["prev_rank"] == 10 and dropped["000015"]["rank"] == 15
    assert "000001" not in dropped                                   # 전 기록일 15위 — 10위 밖이었으니 "빠짐" 아님
    a2 = tr.analyze(days, "2026-11-25")
    t2 = {r["code"]: r for r in a2["top"]}
    assert t2["000015"]["new"] is True and t2["000015"]["prev_rank"] == 15 and t2["000015"]["move"] == 5 and t2["000015"]["streak"] == 1
    assert tr.analyze({}) is None and tr.analyze({"2026-11-24": d1})["prev_day"] is None
    assert len(tr.trim({f"2026-01-{i:02d}": [] for i in range(1, 31)}, 10)) == 10


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "TURNOVER_RANK_PATH", str(tmp_path / "turnover_rank.json"))
    monkeypatch.setattr(app.naver_kr, "fetch_market_rows", lambda: (FX["rows"], FX["stats"]))
    return tmp_path


def test_record_saves_only_today(store):
    assert "저장 안 함" in app._turnover_rank_record_blocking("2026-11-27") and app._turnover_rank_load() == {}
    msg = app._turnover_rank_record_blocking("2026-11-26")
    assert "2026-11-26 기록" in msg and "SK하이닉스" in msg
    assert list(app._turnover_rank_load()) == ["2026-11-26"] and len(app._turnover_rank_load()["2026-11-26"]) == tr.KEEP_TOP


def test_scheduler_gate(store, monkeypatch):
    calls = []
    monkeypatch.setattr(app, "_turnover_rank_record_blocking", lambda d: calls.append(d) or "ok")
    import datetime as dt

    class FakeDT(dt.datetime):
        now_value = None

        @classmethod
        def now(cls, tz=None):
            return cls.now_value
    monkeypatch.setattr(app, "datetime", FakeDT)
    FakeDT.now_value = dt.datetime(2026, 11, 26, 20, 5, tzinfo=app.KST)
    asyncio.run(app._maybe_record_turnover_rank())
    assert calls == []                                              # 20:10 전
    FakeDT.now_value = dt.datetime(2026, 11, 28, 21, 0, tzinfo=app.KST)
    asyncio.run(app._maybe_record_turnover_rank())
    assert calls == []                                              # 토요일
    FakeDT.now_value = dt.datetime(2026, 11, 26, 20, 15, tzinfo=app.KST)
    asyncio.run(app._maybe_record_turnover_rank())
    assert calls == ["2026-11-26"]
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert src.count("await _maybe_record_turnover_rank()") == 1
    assert '"/api/turnover-rank",' in src


def test_api(store):
    app._turnover_rank_record_blocking("2026-11-26")
    r = asyncio.run(app.turnover_rank_api())
    j = json.loads(r.body)
    assert j["ok"] and j["analysis"]["day"] == "2026-11-26" and len(j["analysis"]["top"]) == 10 and len(j["grid"]) == 1


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_page_render():
    src = open(os.path.join(ROOT, "static", "track.html"), encoding="utf-8").read()
    assert src.count("loadRank();") == 1 and src.count('id="rankBox"') == 1
    js = ("const esc=s=>String(s??'');const tv=t=>'#'+t;const pctTxt=p=>p==null?'—':(p>0?'+':'')+p.toFixed(1)+'%';const R={flow:true,pick:null};"
          + _extract_js_from(src, "rankMove") + _extract_js_from(src, "rankHTML") +
          "const top=[{rank:1,code:'a',ticker:'a.KS',name:'가',value_mil:2930200,chg_pct:3.79,prev_rank:2,move:1,new:false,streak:3,in_top_20d:5},"
          "{rank:2,code:'b',ticker:'b.KS',name:'나',value_mil:100,chg_pct:-1,prev_rank:14,move:12,new:true,streak:1,in_top_20d:1},"
          "{rank:3,code:'c',ticker:'c.KS',name:'다',value_mil:100,chg_pct:0,prev_rank:null,move:null,new:true,streak:1,in_top_20d:1},"
          "{rank:4,code:'d',ticker:'d.KS',name:'라',value_mil:100,chg_pct:0,prev_rank:2,move:-2,new:false,streak:2,in_top_20d:2}];"
          "const d={analysis:{day:'2026-11-26',prev_day:'2026-11-25',top,dropped:[{name:'마',prev_rank:7,rank:null}],n_days:2,first_day:'2026-11-25'},grid:[{day:'2026-11-25',top:[{rank:1,code:'a',name:'가'}]},{day:'2026-11-26',top:[{rank:1,code:'a',name:'가'}]}]};"
          "console.log(JSON.stringify([top.map(rankMove),rankHTML(d),rankHTML(null)]))")
    out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
    assert [m["txt"] for m in out[0]] == ["▲1", "NEW 14→", "NEW", "▼2"] and out[0][1]["cls"] == "new"
    assert "29,302억" in out[1] and "3일째" in out[1] and "마 7위→20위 밖" in out[1] and "기록 2일째" in out[1]
    assert "아직 기록이 없어" in out[2]


def _extract_js_from(src, name):
    i = src.index(f"function {name}(")
    j = src.index("{", src.index(")", i))
    d = 0
    for k in range(j, len(src)):
        d += {"{": 1, "}": -1}.get(src[k], 0)
        if d == 0:
            return src[i:k + 1]
