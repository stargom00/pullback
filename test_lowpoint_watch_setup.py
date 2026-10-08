"""v5.343 — 저점 관찰 유형(바닥형/눌림형/판정 불가) = ABC A 판정 재사용. 표시·필터 전용, 저점·관찰 판정 불변.

사용자 지시 요지: "사용자가 보려는 종목은 '긴 하락 → 바닥 박스 → 재상승'형(하이딥·꿈비). 저점 히트에는 상승 추세 속 과매도 눌림형
(PEG)도 섞여 있어 구분이 필요. ABC의 A·B 판정과 B 품질 라벨이 이 구분에 쓸 수 있는 기존 기준이다. 새 임계값 금지."
실데이터 픽스처 test_fixtures/lowpoint_setup_20261009.json.gz(2026-10-09 수집, 원본 그대로 — 하이딥은 09-14·09-15 무거래일
OHLC=0 오염 봉 포함: 정제(app._downcast) 없이 재면 저가 0 → 하락 100%로 틀린다, ABC 탭은 정제된 번들로 판정).

사보타주 확인(2026-10-09, FAIL 확인 후 원복):
① 유형 계산에 ABC_CONFIG 대신 다른 하락폭(a_drop_min 0.20) → test_real_fixture_types · test_matches_abc_tab_recorded_verdicts FAIL
"""
from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.join(ROOT, "scripts", "screens"), os.path.join(ROOT, "scripts", "measurements")):
    sys.path.insert(0, p)
import abc_screener as A  # noqa: E402
import lowpoint_rank as rk  # noqa: E402
import lowpoint_watch as w  # noqa: E402

import app  # noqa: E402

FX = json.load(gzip.open(os.path.join(ROOT, "test_fixtures", "lowpoint_setup_20261009.json.gz"), "rt", encoding="utf-8"))["tickers"]
ABC_FX = json.load(gzip.open(os.path.join(ROOT, "test_fixtures", "abc_display_only.json.gz"), "rt", encoding="utf-8"))
SRC = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()


def _df(code):
    t = FX[code]
    return pd.DataFrame({k: [float("nan") if v is None else v for v in t[k]] for k in ("Open", "High", "Low", "Close", "Volume")},
                        index=pd.to_datetime(t["dates"]))


def test_real_fixture_types():
    hi = w.setup_type(_df("365590.KQ"), "2026-10-08")
    kb = w.setup_type(_df("407400.KQ"), "2026-10-08")
    peg = w.setup_type(_df("PEG"), "2026-10-08")
    assert (hi["setup_type"], hi["setup_a"]["drop_pct"], hi["setup_a"]["span_bars"], hi["setup_b_quality"]) == ("bottom", 86.0, 169, "B 미형성")
    assert hi["setup_a"]["low"] == 1178.0                                  # 무거래일 0 봉 정제 — 저가 0이 아니다
    assert (kb["setup_type"], kb["setup_a"]["drop_pct"], kb["setup_b_quality"]) == ("bottom", 73.3, "흡수")
    assert kb["setup_b"]["bars"] == 47 and kb["setup_b"]["range_pct"] == 38.4
    assert (peg["setup_type"], peg["setup_a"]["drop_pct"], peg["setup_b"], peg["setup_b_quality"]) == ("pullback", 24.5, None, None)
    assert "A 미달" in peg["setup_reason"]
    assert hi["setup_checked_date"] == "2026-10-08"


def test_raw_unclean_would_be_wrong():
    """전제 고정: 정제 없이 analyze_abc를 돌리면 하이딥 저가가 0(하락 100%) — setup_type이 정제를 거치는 이유."""
    assert A.analyze_abc(_df("365590.KQ"))["a"]["low"] == 0.0


@pytest.mark.parametrize("t", sorted(ABC_FX))
def test_matches_abc_tab_recorded_verdicts(t):
    """ABC 탭 판정 기록(2026-10-06 KR 실종목 13개 — test_abc_display_only 픽스처)과 같은 종목에서 A 판정이 일치."""
    o = ABC_FX[t]["ohlcv"]
    df = pd.DataFrame({c: o[c] for c in ("Open", "High", "Low", "Close", "Volume")}, index=pd.to_datetime(o["date"])).astype("float64")
    got = w.setup_type(df, str(df.index[-1].date()))
    j = ABC_FX[t]["expect"]["judge"]
    want = "bottom" if j["verdict"] == "ABC" else ("unknown" if j.get("a") is None else "pullback")
    assert got["setup_type"] == want, (t, j["verdict"])
    if j.get("a"):
        assert got["setup_a"]["drop_pct"] == j["a"]["drop_pct"] and got["setup_a"]["span_bars"] == j["a"]["span_bars"]
    if want == "bottom":                                                   # B 품질 = ABC 탭 기록(v5.331 라벨)과 같은 값
        assert got["setup_b_quality"] == ABC_FX[t]["new_labels"]["b_quality"]


def test_short_history_unknown_and_confirmed_only():
    d = _df("407400.KQ")
    assert w.setup_type(d.iloc[-300:], "2026-10-08")["setup_type"] == "unknown"          # MA600 계산 불가 = 봉 부족
    assert w.setup_type(d, None)["setup_type"] == "unknown"
    assert w.setup_type(d, "2026-10-06")["setup_checked_date"] == "2026-10-06"           # 확정 봉까지만


def _kumbi_rec(**k):
    return {"id": "w_week_2026-10-02_407400.KQ", "tf": "week", "label": "2026-10-02", "code": "407400.KQ", "mkt": "KR",
            "base_date": "2026-10-02", "base_price": 1969.0, "status": "active", "reach_rule": w.REACH_RULE,
            "regular_checked_through": "2026-10-02", **k}


def _bars(day, high):
    return [{"localDateTime": day.replace("-", "") + "100000", "highPrice": high}]


def test_stage_values_unchanged_by_setup_and_longer_window(monkeypatch):
    """관찰 단계·출발·무효선 값이 유형 추가 전후·조회 창 확대 전후 같다(유형 필드만 늘어난다)."""
    d = _df("407400.KQ")
    fm = lambda c, day: _bars(str(day), 2080.0 if str(day) == "2026-10-07" else 1990.0)
    recs = [_kumbi_rec(), _kumbi_rec(id="w_s", status="reached", reached_date="2026-10-06", reached_high=2070.0,
                                     reached_days=4, reached_pct=5.13, stage="resting")]
    run = lambda df: w.track([dict(r) for r in recs], date(2026, 10, 9), "2026-10-09T07:00:00+09:00",
                             fetch=lambda r, t: {"407400.KQ": df}, fetch_min=fm)[0]
    full = run(d)
    monkeypatch.setattr(w, "setup_type", lambda daily, through: {})
    no_setup = run(d)
    short = run(d[d.index >= "2026-06-01"])                                               # 예전(v5.342) 조회 창 정도
    setup_keys = {"setup_type", "setup_reason", "setup_a", "setup_b", "setup_b_quality", "setup_checked_date",
                  "setup_ref_low", "setup_ref_basis", "setup_ref_date"}                     # v5.344 무효 참고 — 표시 전용
    shape = {"departure_vol_mult"}                                                        # 50일 평균 — 짧은 창이면 원래 None(창 확대 전에도 같은 규칙)
    for rid in full:
        assert {k: v for k, v in full[rid].items() if k not in setup_keys} == no_setup[rid]
        assert {k: v for k, v in no_setup[rid].items() if k not in shape} == {k: v for k, v in short[rid].items() if k not in shape}
    assert full["w_s"]["setup_type"] == "bottom" and full["w_s"]["stage"] == no_setup["w_s"]["stage"]
    assert full[recs[0]["id"]]["status"] == "reached" and full[recs[0]["id"]]["reached_date"] == "2026-10-07"


def test_rank_snapshot_and_review_by_type():
    wr = {"status": "active", "setup_type": "bottom", "setup_b_quality": "흡수"}
    sn = rk.snapshot_item({"base_price": 100.0}, None, None, wr, [], None, 60, 50)
    assert (sn["setup_type"], sn["setup_b_quality"]) == ("bottom", "흡수")
    items = [{"watch_id": f"w{i}", "code": f"C{i}", "name": "n", "mkt": "KR", "base_date": "2026-10-08", "base_price": 100.0}
             for i in range(4)]
    recs = [{"id": "r", "tf": "week", "label": "2026-10-08", "items": items, "confirmed_at": "x",
             "picks": {"w0": {"pick": "first", "reasons": []}},
             "snapshot": {"w0": {"setup_type": "bottom"}, "w1": {"setup_type": "pullback"}}, "results": {}}]
    watch = {"w0": {"status": "reached", "setup_type": "pullback"},                       # 스냅샷(확정 시점) 유형이 우선
             "w1": {"status": "active"}, "w2": {"status": "active", "setup_type": "bottom"}, "w3": {"status": "active"}}
    rv = rk.review(recs, watch)
    assert {t: rv["by_type"][t]["total"]["n"] for t in rk.SETUP_TYPES} == {"bottom": 2, "pullback": 1, "unknown": 1}
    assert sum(rv["by_type"][t]["total"]["n"] for t in rk.SETUP_TYPES) == rv["total"]["n"]
    assert rv["by_type"]["bottom"]["by_pick"]["first"]["departed"] == 1


# ── 프론트(node, production 원문 실행) ────────────────────────────────
def _fn(name):
    start = SRC.index(f"function {name}(")
    i = SRC.index("{", SRC.index(")", start))
    d = 0
    for j in range(i, len(SRC)):
        d += {"{": 1, "}": -1}.get(SRC[j], 0)
        if d == 0:
            return SRC[start:j + 1]
    raise AssertionError(name)


def test_front_chip_and_filter():
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    src = "\n".join(_fn(f) for f in ("lpwTypeLabel", "lpwSetupChip", "lpwTypeFilter", "lprReviewPart"))
    p = subprocess.run(["node", "-e", src + """
      const R = [{id:'a', setup_type:'bottom', setup_a:{drop_pct:86, high:8400, low:1178, span_bars:169}, setup_b:{bars:5, range_pct:null}, setup_b_quality:'B 미형성'},
                 {id:'b', setup_type:'pullback', setup_reason:'A 미달'}, {id:'c', setup_type:'unknown'}, {id:'d'}];
      console.log(JSON.stringify([R.map(r => lpwSetupChip(r) && lpwSetupChip(r).text), lpwSetupChip(R[0]).title,
        ['all','bottom','pullback'].map(f => lpwTypeFilter(R, f).map(r => r.id)),
        lprReviewPart({total:1, by_type:{bottom:{total:2}}}, 'bottom').total, lprReviewPart({total:1}, 'all').total]));"""],
                       capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    texts, title, filt, part_b, part_all = json.loads(p.stdout)
    assert texts == ["바닥형 −86% · B 미형성", "눌림형", "판정 불가", None]
    assert "ABC A 통과" in title and "B 5봉 (범위 못 잼)" in title
    assert filt == [["a", "b", "c", "d"], ["a"], ["b"]] and (part_b, part_all) == (2, 1)
    body = _fn("renderLowpointWatch")
    assert "lpwStageSplit(shown)" in body and "lpwGroups(shown)" in body and "lpwStageSplit(_lpw.recs)" in body   # 종료는 전체
    assert body.count("lpwSetupChipHtml(r)") == 2 and "lpwSetTypeFilter('${k}')" in body
    assert "['all', '전체'], ['bottom', '바닥형'], ['pullback', '눌림형']" in body
    assert "lpwSetupChipHtml(" in _fn("renderLowpointRank") and "lprSetReviewType(" in _fn("renderLowpointRankReview")
