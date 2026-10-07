"""v5.341 — 저점 순위(코호트 분류 학습: 예측 → 결과 → 비교).

사용자 지시 요지: "사용자가 저점 종목을 고르는 '눈'을 키우는 학습 시스템. 예측(분류) → 결과 → 비교를 기록·채점한다. 대상은 매주
주봉 스캔 히트와 매월 월봉 스캔 히트(관찰 코호트 그대로). 관심 신호 학습용이며 측정 결론이 아님. 새 판정 임계값 금지."
[분류 확정]은 기한(코호트 기준일 다음 거래일 09:00 KST) 전에만, 확정 뒤 수정 불가, 기한 안에 확정 안 하면 미분류.
결과 = 주봉 D+5·D+10, 월봉 D+20 확정 종가 수익률(매일 07:00 추적 경로, 분봉 없음). 스냅샷은 확정 시점 값으로 고정.

사보타주 확인(2026-10-07, 전부 FAIL 확인 후 원복):
① 기한 이후 확정 허용(lowpoint_rank.is_open이 기한을 안 봄) → test_deadline_boundary · test_confirm_after_deadline_rejected_and_unclassified FAIL
② 스냅샷을 현재 값으로 갱신(결과 기록 작업이 snapshot을 다시 계산) → test_snapshot_frozen_at_confirm FAIL
③ 수익률에 장중 가격(returns_for가 확정 봉 자르기 없이 전체 일봉) → test_returns_skip_holidays_and_use_confirmed_close_only FAIL
④ (기한·기준점 수정) 기준점을 라벨 종가(기준일 봉·base_price)로 되돌림 → test_month_deadline_and_anchor_from_scan_day FAIL,
   주봉 test_returns_… 는 그대로 통과(주봉은 기준점이 라벨 종가와 같은 날)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.join(ROOT, "scripts", "screens"), os.path.join(ROOT, "scripts", "measurements")):
    sys.path.insert(0, p)
import lowpoint_rank as rk  # noqa: E402
import lowpoint_watch as w  # noqa: E402

import app  # noqa: E402

KST = timezone(timedelta(hours=9))
SRC = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()


def _k(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=KST)


# ── 기한 ─────────────────────────────────────────────────────────────────
def test_deadline_next_kr_trading_day_0900():
    """기한 = **스캔 실행일** 다음 KR 거래일 09:00(v5.341 수정 — 라벨 기준 아님)."""
    assert rk.deadline("2026-10-03", app.is_trading_day) == "2026-10-06T09:00:00+09:00"   # 토 스캔 · 10-05 개천절 대체 휴장
    assert rk.deadline("2026-10-10", app.is_trading_day) == "2026-10-12T09:00:00+09:00"   # 주봉 10-08 라벨(10-09 한글날) 토 스캔
    assert rk.deadline("2026-10-01", app.is_trading_day) == "2026-10-02T09:00:00+09:00"   # 월봉 — 1일(거래일) 08:00 스캔 → 2번째 거래일


def test_deadline_boundary():
    rec = {"deadline": "2026-10-12T09:00:00+09:00", "confirmed_at": None}
    assert rk.is_open(rec, "2026-10-12T08:59:59+09:00")
    assert not rk.is_open(rec, "2026-10-12T09:00:00+09:00")
    assert not rk.is_open({**rec, "confirmed_at": "2026-10-10T10:00:00+09:00"}, "2026-10-10T11:00:00+09:00")


# ── 서버 흐름(코호트 생성 → 임시 저장 → 확정 → 결과) ─────────────────────────────
W = [{"id": f"w_week_2026-10-08_{c}", "tf": "week", "label": "2026-10-08", "code": c, "name": n, "mkt": m,
      "base_date": "2026-10-08", "base_price": bp, "status": "active", "stage": "watch"}
     for c, n, m, bp in (("111111.KQ", "가나", "KR", 1000.0), ("222222.KS", "다라", "KR", 2000.0), ("AAA", "Aaa", "US", 10.0))]


def _daily(dates, closes, vols=None, hi_pad=1.0):
    vols = vols or [100.0] * len(closes)
    return pd.DataFrame({"Open": closes, "High": [c + hi_pad for c in closes], "Low": [c - hi_pad for c in closes],
                         "Close": closes, "Volume": vols}, index=pd.to_datetime(dates))


class _Req:
    headers = {"user-agent": "pytest"}
    client = type("C", (), {"host": "127.0.0.1"})()

    def __init__(self, body):
        self._b = body

    async def json(self):
        return self._b


@pytest.fixture
def store(tmp_path, monkeypatch):
    app._rec_list_write(app.LP_WATCH_PATH, [dict(r) for r in W])
    monkeypatch.setattr(app, "LOWPOINT_STATE_PATH", str(tmp_path / "state.json"))      # 러너 상태 — 주봉 10-08 라벨을 토요일에 스캔
    (tmp_path / "state.json").write_text(json.dumps({"week": {"target": "2026-10-08", "status": "ok",
                                                              "finished_at": "2026-10-10T09:05:00+09:00"}}))
    clock = {"now": _k("2026-10-10 10:00")}
    monkeypatch.setattr(app, "_lp_rank_now", lambda: clock["now"])
    pre = pd.bdate_range(end="2026-10-08", periods=80)
    data = {"111111.KQ": _daily(pre, [1000.0] * 80, [100.0] * 79 + [400.0]),
            "222222.KS": _daily(pre, [2000.0] * 80), "AAA": _daily(pre, [10.0] * 80)}
    state = {"data": data, "fetched": []}

    def fetch(records, today):
        state["fetched"].append(sorted(r["code"] for r in records))
        return {r["code"]: state["data"][r["code"]] for r in records if r["code"] in state["data"]}
    monkeypatch.setattr(w, "fetch_daily", fetch)
    monkeypatch.setattr(app, "_flow_fetch_one", lambda t: {"ok": True, "organ": 3, "foreign": 1, "of": 5, "asof": "10-08"})
    monkeypatch.setattr(app, "_abc_theme_map", lambda uni: {"테스트테마": ["111111.KQ", "333333.KQ"]})
    monkeypatch.setattr(app, "_peek_market_bundle", lambda m: {"universe": {}, "data": {
        "111111.KQ": _daily(["2026-10-07", "2026-10-08"], [1000.0, 1100.0]),
        "333333.KQ": _daily(["2026-10-07", "2026-10-08"], [500.0, 510.0])}})
    return {"clock": clock, "state": state, "tmp": tmp_path}


def _recs():
    return app._rec_list_load(app.LP_RANK_PATH)


def _call(coro):
    r = asyncio.run(coro)
    return r.status_code, json.loads(r.body)


def test_ensure_creates_only_open_cohorts_and_merges(store):
    app._lp_rank_ensure(_k("2026-10-10 10:00"))
    (rec,) = _recs()
    assert rec["id"] == "r_week_2026-10-08" and rec["deadline"] == "2026-10-12T09:00:00+09:00" and rec["rev"] == 1
    assert rec["scan_at"] == "2026-10-10T09:05:00+09:00"                                    # 러너 상태의 같은 라벨 성공 시각
    assert [i["code"] for i in rec["items"]] == ["111111.KQ", "222222.KS", "AAA"] and rec["confirmed_at"] is None
    # 지난 코호트(스캔 시각을 모름 · 기한 지남)는 만들지 않는다 — 소급 미분류 없음
    app._rec_list_write(app.LP_WATCH_PATH, W + [{**W[0], "id": "w_week_2026-09-25_x", "label": "2026-09-25", "code": "X"}])
    app._lp_rank_ensure(_k("2026-10-10 10:00"))
    assert [r["id"] for r in _recs()] == ["r_week_2026-10-08"]
    # 확정 전 새 히트는 합친다
    app._rec_list_write(app.LP_WATCH_PATH, W + [{**W[0], "id": "w_week_2026-10-08_444444.KQ", "code": "444444.KQ"}])
    app._lp_rank_ensure(_k("2026-10-10 11:00"))
    assert len(_recs()[0]["items"]) == 4 and _recs()[0]["rev"] == 2


def test_draft_put_then_confirm_then_locked(store):
    app._lp_rank_ensure(store["clock"]["now"])
    rid, rec = "r_week_2026-10-08", _recs()[0]
    picks = {W[0]["id"]: {"pick": "first", "reasons": ["거래량 폭발 이력", "테마"]}, W[2]["id"]: {"pick": "no", "reasons": []}}
    st, d = _call(app.lp_rank_put(rid, _Req({"picks": picks, "base_rev": rec["rev"]})))
    assert st == 200 and d["record"]["picks"] == picks and d["record"]["rev"] == 2
    # 이유 칩 3개 · 목록 밖 칩 · 코호트 밖 종목은 거부
    for bad in ({W[0]["id"]: {"pick": "first", "reasons": ["테마", "수급", "이평 수렴"]}},
                {W[0]["id"]: {"pick": "first", "reasons": ["감"]}}, {"w_x": {"pick": "first", "reasons": []}},
                {W[0]["id"]: {"pick": "maybe", "reasons": []}}):
        assert _call(app.lp_rank_put(rid, _Req({"picks": bad, "base_rev": 2})))[0] == 400
    st, d = _call(app.lp_rank_confirm(rid, _Req({"picks": picks, "base_rev": 2})))
    assert st == 200 and d["record"]["confirmed_at"].startswith("2026-10-10T10:00") and d["record"]["picks"] == picks
    # 확정 뒤: 임시 저장·재확정 모두 거부, 기록 그대로
    assert _call(app.lp_rank_put(rid, _Req({"picks": {}, "base_rev": 3})))[1]["code"] == "confirmed"
    assert _call(app.lp_rank_confirm(rid, _Req({"picks": {}, "base_rev": 3})))[1]["code"] == "confirmed"
    assert _recs()[0]["picks"] == picks


def test_confirm_after_deadline_rejected_and_unclassified(store):
    app._lp_rank_ensure(store["clock"]["now"])
    rid = "r_week_2026-10-08"
    store["clock"]["now"] = _k("2026-10-12 09:00")                      # 다음 거래일 09:00 정각부터 불가
    st, d = _call(app.lp_rank_confirm(rid, _Req({"picks": {W[0]["id"]: {"pick": "first", "reasons": []}}, "base_rev": 1})))
    assert st == 409 and d["code"] == "deadline" and _recs()[0]["confirmed_at"] is None
    assert _call(app.lp_rank_put(rid, _Req({"picks": {}, "base_rev": 1})))[1]["code"] == "deadline"
    rv = rk.review(_recs(), {r["id"]: r for r in W})
    assert rv["by_pick"]["unclassified"]["n"] == 3 and rv["by_pick"]["first"]["n"] == 0


def test_snapshot_values(store):
    app._lp_rank_ensure(store["clock"]["now"])
    st, d = _call(app.lp_rank_confirm("r_week_2026-10-08", _Req({"picks": {}, "base_rev": 1})))
    sn = d["record"]["snapshot"][W[0]["id"]]
    assert (sn["close"], sn["close_date"], sn["pct_vs_base"]) == (1000.0, "2026-10-08", 0.0)
    assert sn["atr_pct"] == 0.2                                          # TR 2 ÷ 1000 — 평가 페이지 short_metrics 그대로
    assert sn["max_vol_mult"] == 4.0                                     # 마지막 봉 400 ÷ 직전 50일 평균 100
    assert sn["flow"]["organ"] == 3 and sn["themes"] == [{"theme": "테스트테마", "up": 1, "total": 2, "no_data": 0}]
    assert sn["stage"] == "watch" and sn["invalid_dist_pct"] is None
    us = d["record"]["snapshot"][W[2]["id"]]
    assert us["themes"] == [] and us["flow"] is None                    # US는 수급·테마 소스 없음 — 비움(0 아님)


def test_snapshot_frozen_at_confirm(store):
    app._lp_rank_ensure(store["clock"]["now"])
    _call(app.lp_rank_confirm("r_week_2026-10-08", _Req({"picks": {}, "base_rev": 1})))
    before = _recs()[0]["snapshot"]
    # 가격이 바뀌고(관찰 단계도 바뀌고) 결과 기록·코호트 확인이 돌아도 스냅샷은 그대로
    after_dates = pd.bdate_range(start="2026-10-12", periods=12)
    for code, base in (("111111.KQ", 1000.0), ("222222.KS", 2000.0), ("AAA", 10.0)):
        df = store["state"]["data"][code]
        store["state"]["data"][code] = pd.concat([df, _daily(after_dates, [base * 1.3] * 12)])
    app._rec_list_write(app.LP_WATCH_PATH, [{**W[0], "status": "reached", "stage": "resting", "invalid_line": 1010.0}] + W[1:])
    app._lp_rank_results_blocking(_k("2026-10-30 07:00"))
    app._lp_rank_ensure(_k("2026-10-30 07:00"))
    rec = _recs()[0]
    assert rec["snapshot"] == before and rec["results"][W[0]["id"]]["d5"]["pct"] == 30.0


def test_returns_skip_holidays_and_use_confirmed_close_only():
    """기준일 10-08 다음 거래일부터 — 10-09 한글날 휴장은 봉이 없어 저절로 건너뛴다. D+5 = 10-16, D+10 = 10-23."""
    item = {"base_date": "2026-10-08", "base_price": 100.0}
    days = ["2026-10-08", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19",
            "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"]
    d = _daily(days, [100.0, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110])
    anchor = w.confirmed_through("KR", "2026-10-12T09:00:00+09:00")                         # 기한(월 09:00) 시각의 확정일
    assert anchor == "2026-10-11"
    got = rk.returns_for(item, d, "2026-10-23", (5, 10), anchor)
    assert got == {"anchor": {"date": "2026-10-08", "close": 100.0},                         # 주봉 — 기준점 = 라벨 종가와 같은 날
                   "d5": {"date": "2026-10-16", "close": 105.0, "pct": 5.0}, "d10": {"date": "2026-10-23", "close": 110.0, "pct": 10.0}}
    # 10-16 장중(확정 전) — 그 봉 종가가 있어도 D+5는 아직 없다
    thru = w.confirmed_through("KR", "2026-10-16T15:00:00+09:00")
    assert thru == "2026-10-15" and set(rk.returns_for(item, d, thru, (5, 10), anchor)) == {"anchor"}
    assert rk.returns_for(item, d, None, (5,), anchor) == {} and rk.returns_for(item, d, "2026-10-23", (5,), None) == {}
    assert rk.HORIZONS == {"week": (5, 10), "month": (20,)}


def test_results_job_fills_without_confirm_and_no_minutes(store):
    app._lp_rank_ensure(store["clock"]["now"])
    after = pd.bdate_range(start="2026-10-12", periods=10)
    store["state"]["data"]["111111.KQ"] = pd.concat([store["state"]["data"]["111111.KQ"], _daily(after, [1100.0] * 10)])
    out = app._lp_rank_results_blocking(_k("2026-10-26 07:00"))
    rec = _recs()[0]
    assert out["changed"] == ["r_week_2026-10-08"] and rec["confirmed_at"] is None          # 미분류도 채점
    assert rec["results"][W[0]["id"]]["d5"]["pct"] == 10.0 and rec["results"][W[0]["id"]]["d10"]["pct"] == 10.0
    assert set(rec["results"][W[1]["id"]]) == {"anchor"}                                   # 이후 봉 없음 — 기준점만
    import inspect
    assert "fetch_min" not in inspect.getsource(app._lp_rank_results_blocking)


def test_month_deadline_and_anchor_from_scan_day(store, monkeypatch):
    """월봉 09-30 라벨 · 1일(거래일) 08:05 스캔 → 기한 = 2번째 거래일(10-02) 09:00, 기준점 = 1일 확정 종가, D+1 = 10-02."""
    mw = [{**W[0], "id": "w_month_2026-09-30_111111.KQ", "tf": "month", "label": "2026-09-30", "base_date": "2026-09-30",
           "base_price": 100.0},
          {**W[2], "id": "w_month_2026-09-30_AAA", "tf": "month", "label": "2026-09-30", "base_date": "2026-09-30", "base_price": 10.0}]
    app._rec_list_write(app.LP_WATCH_PATH, mw)
    entry = {"bar_date": "2026-09-30", "rows": []}
    store["clock"]["now"] = _k("2026-10-01 08:05")
    app._lp_watch_register(entry, "month", "scan")                         # 스캔 직후 등록 경로 — 그 시각이 스캔 시각
    (rec,) = _recs()
    assert (rec["scan_at"][:16], rec["deadline"]) == ("2026-10-01T08:05", "2026-10-02T09:00:00+09:00")
    assert rk.is_open(rec, "2026-10-01T09:30:00+09:00") and not rk.is_open(rec, "2026-10-02T09:00:00+09:00")
    days = ["2026-09-30", "2026-10-01"] + [str(x.date()) for x in pd.bdate_range(start="2026-10-02", periods=25)
                                           if app.is_trading_day("kr", str(x.date()))][:21]
    closes = [100.0, 110.0] + [110.0 + i + 1 for i in range(len(days) - 2)]
    store["state"]["data"] = {"111111.KQ": _daily(days, closes), "AAA": _daily(days, [c / 10 for c in closes])}
    app._lp_rank_results_blocking(_k("2026-11-30 07:00"))
    res = _recs()[0]["results"]
    kr, us = res[mw[0]["id"]], res[mw[1]["id"]]
    assert kr["anchor"] == {"date": "2026-10-01", "close": 110.0}                            # 라벨(09-30) 종가 100이 아니다
    assert kr["d20"] == {"date": days[21], "close": closes[21], "pct": round((closes[21] / 110.0 - 1) * 100, 2)}
    assert days[2] == "2026-10-02" and us["anchor"]["date"] == "2026-10-01"                 # US도 기한 시각(뉴욕 10-01 20:00) 확정일


# ── 리뷰 ─────────────────────────────────────────────────────────────────
def _rv_recs():
    items = [{"watch_id": f"w{i}", "code": f"C{i}", "name": f"N{i}", "mkt": "KR", "base_date": "2026-10-08", "base_price": 100.0}
             for i in range(6)]
    conf = {"id": "r_week_2026-10-08", "tf": "week", "label": "2026-10-08", "deadline": "x", "items": items[:4],
            "confirmed_at": "2026-10-10T10:00:00+09:00",
            "picks": {"w0": {"pick": "first", "reasons": ["테마"]}, "w1": {"pick": "no", "reasons": ["그냥 느낌", "테마"]},
                      "w2": {"pick": "first", "reasons": []}},                                 # w3은 안 고름 → 보통
            "snapshot": {f"w{i}": {"pct_vs_base": float(i), "atr_pct": 2.0, "max_vol_mult": 1.0 + i, "invalid_dist_pct": None,
                                   "flow": {"organ": i, "foreign": 0}, "themes": [{"theme": "t"}] if i % 2 else []} for i in range(4)},
            "results": {"w0": {"d5": {"pct": 4.0}}, "w1": {"d5": {"pct": 10.0}, "d10": {"pct": 12.0}}, "w3": {"d5": {"pct": -2.0}}}}
    unconf = {"id": "r_week_2026-10-16", "tf": "week", "label": "2026-10-16", "deadline": "x", "items": items[4:],
              "confirmed_at": None, "picks": {"w4": {"pick": "first", "reasons": ["수급"]}}, "snapshot": {},
              "results": {"w4": {"d5": {"pct": 6.0}}}}
    watch = {"w0": {"status": "active"}, "w1": {"status": "reached", "reached_date": "2026-10-13", "stage": "resting"},
             "w2": {"status": "active"}, "w3": {"status": "reached", "reached_date": "2026-10-14", "stage": "invalid"},
             "w4": {"status": "reached", "reached_date": "2026-10-19"}, "w5": {"status": "active"}}
    return [conf, unconf], watch


def test_review_group_sums_equal_total_and_tables():
    recs, watch = _rv_recs()
    rv = rk.review(recs, watch)
    assert sum(rv["by_pick"][p]["n"] for p in ("first", "normal", "no", "unclassified")) == rv["total"]["n"] == 6
    assert sum(rv["by_pick"][p]["departed"] for p in rv["by_pick"]) == rv["total"]["departed"] == 3
    assert [rv["by_pick"][p]["n"] for p in ("first", "normal", "no", "unclassified")] == [2, 1, 1, 2]   # 미확정 코호트 = 미분류
    assert rv["by_pick"]["first"]["avg"]["d5"] == 4.0 and rv["by_pick"]["no"]["avg"]["d10"] == 12.0
    assert rv["by_pick"]["unclassified"]["departed_rate"] == 50.0 and rv["total"]["avg"]["d5"] == 4.5
    assert rv["rose_vs_not"]["departed"]["n"] == 2 and rv["rose_vs_not"]["not_departed"]["n"] == 2
    assert rv["rose_vs_not"]["departed"]["max_vol_mult"] == 3.0 and rv["rose_vs_not"]["no_snapshot"] == 2
    assert [(r["code"], r["pick"]) for r in rv["missed"]] == [("C1", "no"), ("C3", "normal")]
    assert rv["missed"][0]["reasons"] == ["그냥 느낌", "테마"] and rv["missed"][0]["snapshot"]["pct_vs_base"] == 1.0
    assert rv["chips"]["테마"] == {"n": 2, "departed": 1, "rate": 50.0}
    assert rv["chips"]["수급"]["n"] == 0                                 # 미확정 코호트의 칩은 채점 안 함
    assert rv["cohorts"] == {"week": 2, "month": 0} and rv["min_cohorts"] == {"week": 4, "month": 3}


def test_api_list_and_wiring(store):
    app._lp_rank_ensure(store["clock"]["now"])
    d = json.loads(asyncio.run(app.lp_rank_list()).body)
    assert d["ok"] and d["records"][0]["id"] == "r_week_2026-10-08" and set(d["review"]["by_pick"]) == {"first", "normal", "no", "unclassified"}
    assert d["reasons"] == list(rk.REASONS) and d["max_reasons"] == 2 and d["first_guide"] == [3, 7]
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert src.count('_daily_backup(LP_RANK_PATH, "lowpoint_rankings"') == 2
    assert 'LP_RANK_PATH = _resolve_persistent_path("lowpoint_rankings.json")' in src
    assert "rank = _lp_rank_results_blocking(now)" in src                                   # 07:00 추적 경로
    asyncio.run(app.lp_rank_delete("r_week_2026-10-08", _Req({})))
    app._lp_rank_ensure(store["clock"]["now"])
    assert _recs() == []                                                                      # 삭제한 코호트는 다시 안 만든다


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
    fns = ("lprLatest", "lprPicksOf", "lprPickOf", "lprWithPick", "lprWithReason", "lprFirstGuide", "lprStatusText")
    p = subprocess.run(["node", "-e", "\n".join(_fn(f) for f in fns) + f"\nconsole.log(JSON.stringify({expr}));"],
                       capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_front_pick_reason_guide():
    got = _js("""(() => { let p = {};
      p = lprWithReason(p, 'a', '테마', 2); p = lprWithReason(p, 'a', '수급', 2); const two = p;
      p = lprWithReason(p, 'a', '그냥 느낌', 2); const capped = p === two;
      p = lprWithReason(p, 'a', '테마', 2); p = lprWithPick(p, 'a', 'first');
      const items = ['a','b','c'].map(x => ({watch_id: x}));
      return [two.a.reasons, capped, p.a, lprPickOf(p, 'b'), lprFirstGuide(items, p, [3, 7]),
              lprFirstGuide(items, {a:{pick:'first'},b:{pick:'first'},c:{pick:'first'}}, [3, 7]).ok]; })()""")
    assert got[0] == ["테마", "수급"] and got[1] is True and got[2] == {"pick": "first", "reasons": ["수급"]}
    assert got[3] == "normal" and got[4]["n"] == 1 and got[4]["ok"] is False and "범위 밖" in got[4]["text"] and got[5] is True


def test_front_latest_and_status():
    got = _js("""[lprLatest([{tf:'week',label:'2026-10-02'},{tf:'week',label:'2026-10-08'},{tf:'month',label:'2026-09-30'}]).map(r => r.tf + r.label),
                  lprStatusText({confirmed_at:'2026-10-10T10:00:00+09:00'}, true).text,
                  lprStatusText({deadline:'2026-10-12T09:00:00+09:00'}, true).text,
                  lprStatusText({deadline:'2026-10-12T09:00:00+09:00'}, false).text,
                  lprPicksOf({confirmed_at:'x', picks:{a:1}}, {a:2}), lprPicksOf({picks:{a:1}}, {a:2})]""")
    assert got[0] == ["week2026-10-08", "month2026-09-30"]
    assert "수정 불가" in got[1] and "확정 기한 10-12 09:00" in got[2] and "미분류로 채점" in got[3]
    assert got[4] == {"a": 1} and got[5] == {"a": 2}                                       # 확정이면 화면 임시값 무시


def test_front_wiring():
    assert "['rank', '순위']" in SRC.split("const LPT_PAGES = ")[1].split("\n")[0]
    assert "if (v === 'rank') { lprLoad().then(renderLowpointTrack); }" in _fn("lpSetView")
    body = _fn("renderLowpointRank")
    assert "관심 신호 학습용 · 측정 결론 아님." in body and "lprConfirm(" in body and "lprSave(" in body
    rv = _fn("renderLowpointRankReview")
    assert "코호트 4주(월봉은 3개월) 쌓이기 전에는 결론 내지 않음" in rv
    for t in ("① 분류별 성적", "② 오른 종목 vs 안 오른 종목", "③ 놓친 것", "④ 이유 칩별 적중률"):
        assert t in rv, t
    assert "confirm(" in _fn("lprConfirm") and "/confirm'" in _fn("lprConfirm")
    assert re.search(r"/\* v5\.341 저점 순위 분류 표 — \.lpr-table 아래로 한정 \*/", SRC)
