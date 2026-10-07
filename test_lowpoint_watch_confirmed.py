"""v5.339 — 저점 관찰 출발 판정은 확정 봉만(장중 즉시 인정 제거) + 관찰 페이지 섹션 순서 숨고르기 → 관찰 중 → 종료.

사용자 지시 요지: "꿈비가 10-07 장중 정규장 고가 2,085(기준 2,067)를 터치해 '출발'로 기록됨. 장중 2,030까지 밀린 상태. 출발
판정만 장중 봉을 즉시 인정하고, 숨고르기·재출발·무효는 확정 종가만 쓰고 있어 기준이 어긋남. … 출발을 확정 봉으로만 판정한다."
확정 = confirmed_through(KR app.KR_CLOSE_CONFIRMED_HM 20:10 KST · US 뉴욕 16:00). 판정 값은 그대로(KR 정규장 분봉 고가, US 일봉
고가). 이미 장중 봉으로 출발 처리된 레코드(reach_rule "regular_high")는 다음 추적에서 확정 기준으로 다시 본다 — 확정 전이면
관찰로 되돌리고(로그), 확정 후 다시 판정.

사보타주 확인(2026-10-07, FAIL 확인 후 원복):
① judge_kr_regular의 확정 봉 자르기(`d.index <= through`) 제거 = 장중 즉시 인정 복원 → test_kr_intraday_not_departed_until_confirmed ·
   test_kumbi_1007_reverted_then_departs_after_confirm FAIL
② US 확정 봉 자르기(_confirmed_daily) 대신 전체 일봉 → test_us_intraday_not_departed_until_confirmed FAIL
"""
from __future__ import annotations

import gzip
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.join(ROOT, "scripts", "screens"), os.path.join(ROOT, "scripts", "measurements")):
    sys.path.insert(0, p)
import lowpoint_watch as w  # noqa: E402

import app  # noqa: E402

KST = timezone(timedelta(hours=9))
FX = json.load(gzip.open(os.path.join(ROOT, "test_fixtures", "kumbi_20261006_minutes.json.gz"), "rt", encoding="utf-8"))
SRC = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()


def _bar(day, hhmmss, high):
    return {"localDateTime": day.replace("-", "") + hhmmss, "highPrice": high}


def _kumbi_daily(with_1007=True):
    """실제 꿈비 일봉(09-30~10-06, 기준일 10-02 종가 1,969) + 10-07 봉(장중 통합 — 고가 2,085 · 2,030까지 밀림)."""
    rows = dict(FX["daily"])
    if with_1007:
        rows["2026-10-07"] = dict(Open=2000.0, High=2085.0, Low=2025.0, Close=2030.0, Volume=150000.0)
    d = pd.DataFrame(rows).T[["Open", "High", "Low", "Close", "Volume"]].astype(float)
    d.index = pd.to_datetime(d.index)
    return d


KUMBI = {"id": "w_week_2026-10-02_407400.KQ", "tf": "week", "label": "2026-10-02", "code": "407400.KQ", "name": "꿈비",
         "market": "KOSDAQ", "mkt": "KR", "base_date": "2026-10-02", "base_price": 1969.0, "rev": 1}
MIN_1007 = [_bar("2026-10-07", "090100", 2000), _bar("2026-10-07", "103000", 2085), _bar("2026-10-07", "153000", 2030),
            _bar("2026-10-07", "170000", 2100)]                                   # 애프터 2,100은 정규장 아님
THR = 1969.0 * 1.05                                                                 # 2,067.45


def _mins(day):
    return FX["minutes"]["2026-10-06"] if str(day) == "2026-10-06" else MIN_1007


def test_kr_intraday_not_departed_until_confirmed():
    """장중(확정 전) 정규장 고가가 기준을 넘어도 출발 아님 → 20:10 KST 이후 출발."""
    rec = {**KUMBI, "status": "active", "regular_checked_through": "2026-10-06"}
    asked = []
    fm = lambda c, d: asked.append(str(d)) or _mins(d)
    for hm in ("10:31", "15:31", "20:09"):
        up, s = w.track([rec], date(2026, 10, 7), f"2026-10-07T{hm}:00+09:00", fetch=lambda r, t: {rec["code"]: _kumbi_daily()},
                        fetch_min=fm)
        u = up[rec["id"]]
        assert "status" not in u or u["status"] == "active", hm
        assert u["regular_checked_through"] == "2026-10-06" and u["pending_day"] is None and s["reached_new"] == 0
    assert asked == []                                                               # 확정 전 봉은 분봉도 안 받는다
    up, s = w.track([rec], date(2026, 10, 7), "2026-10-07T20:10:00+09:00", fetch=lambda r, t: {rec["code"]: _kumbi_daily()},
                    fetch_min=fm)
    u = up[rec["id"]]
    assert (u["status"], u["reached_date"], u["reached_high"], u["reach_rule"]) == ("reached", "2026-10-07", 2085, w.REACH_RULE)
    assert s["reached_new"] == 1 and asked == ["2026-10-07"]


def test_us_intraday_not_departed_until_confirmed():
    rec = {**KUMBI, "id": "w_us", "code": "WSI", "mkt": "US", "base_price": 10.0, "status": "active", "base_date": "2026-10-02"}
    d = pd.DataFrame({"Open": [10.0, 10.1, 10.2], "High": [10.1, 10.3, 10.6], "Low": [9.9, 10.0, 10.1],
                      "Close": [10.0, 10.2, 10.3], "Volume": [1.0, 1.0, 1.0]},
                     index=pd.to_datetime(["2026-10-02", "2026-10-05", "2026-10-06"]))
    run = lambda iso: w.track([rec], date(2026, 10, 7), iso, fetch=lambda r, t: {"WSI": d})[0]["w_us"]
    during = run("2026-10-06T23:30:00+09:00")                                        # 뉴욕 10-06 10:30 장중 — 고가 10.6 무시
    assert during.get("status", "active") == "active" and during["last_close"] == 10.3   # 현재가 표시는 최신 그대로
    after = run("2026-10-07T05:01:00+09:00")                                         # 뉴욕 10-06 16:01
    assert (after["status"], after["reached_date"], after["reached_high"]) == ("reached", "2026-10-06", 10.6)


def test_kumbi_1007_reverted_then_departs_after_confirm(monkeypatch, tmp_path, capsys):
    """꿈비 10-07 재현 — v5.333 규칙으로 장중 출발 처리된 레코드: 확정 전 실행 → 관찰 복귀(로그), 확정 후 → 출발."""
    for k, v in (("LOWPOINT_DATA_PATH", "d.json"), ("LOWPOINT_LATEST_PATH", "r.json"), ("LOWPOINT_STATE_PATH", "s.json")):
        monkeypatch.setattr(app, k, str(tmp_path / v))
    old = {**KUMBI, "status": "reached", "reach_rule": w.PREV_REACH_RULE, "reached_date": "2026-10-07", "reached_days": 5,
           "reached_high": 2085.0, "reached_pct": 5.89, "regular_checked_through": "2026-10-07", "stage": "departed",
           "invalid_line": 1969.0, "departure_high": 2085.0, "stage_date": "2026-10-07"}
    app._rec_list_write(app.LP_WATCH_PATH, [old])
    asked = []
    monkeypatch.setattr(w.track, "__defaults__", (lambda r, t: {old["code"]: _kumbi_daily()},
                                                  lambda c, d: asked.append(str(d)) or _mins(d)))
    app._lp_watch_job_blocking("watch", datetime(2026, 10, 7, 16, 0, tzinfo=KST))     # 정규장 끝, 애프터 중 — 확정 전
    rec = app._rec_list_load(app.LP_WATCH_PATH)[0]
    assert (rec["status"], rec["stage"], rec["reached_date"], rec["reach_rule"]) == ("active", "watch", None, w.REACH_RULE)
    assert rec["regular_checked_through"] == "2026-10-06" and rec["rev"] == 2 and asked == []
    out = capsys.readouterr().out
    assert "출발 확정 재확인 1건 · 확정 전 출발 → 관찰 복귀 1건: 407400.KQ(2026-10-02 week) 출발 2026-10-07" in out
    app._lp_watch_job_blocking("watch", datetime(2026, 10, 8, 7, 0, tzinfo=KST))      # 다음 날 07:00 — 10-07 확정
    rec = app._rec_list_load(app.LP_WATCH_PATH)[0]
    assert (rec["status"], rec["reached_date"], rec["reached_high"], rec["reach_rule"]) == ("reached", "2026-10-07", 2085, w.REACH_RULE)
    assert rec["reached_high"] >= THR and rec["stage"] == "departed" and asked == ["2026-10-07"]
    assert "확정 전 출발 → 관찰 복귀 0건: 없음" in capsys.readouterr().out


def test_confirmed_regular_high_below_threshold_stays_watch():
    """확정 정규장 고가 < 2,067.45 → 출발 아님(장중엔 넘은 것처럼 보였어도 판정 값은 확정 분봉)."""
    rec = {**KUMBI, "status": "active", "regular_checked_through": "2026-10-06"}
    low = [_bar("2026-10-07", "103000", 2060), _bar("2026-10-07", "170000", 2100)]
    up, _ = w.track([rec], date(2026, 10, 8), "2026-10-08T07:00:00+09:00", fetch=lambda r, t: {rec["code"]: _kumbi_daily()},
                    fetch_min=lambda c, d: low)
    u = up[rec["id"]]
    assert u.get("status", "active") == "active" and u["regular_checked_through"] == "2026-10-07"


def test_recheck_confirmed_old_rule_record_keeps_or_waits():
    """v5.333 규칙으로 도달 + 도달일 봉 확정 → 확정 고가로 유지(고가 갱신). 분봉을 못 받으면 저장값 유지·규칙 그대로(다음에 다시)."""
    old = {**KUMBI, "status": "reached", "reach_rule": w.PREV_REACH_RULE, "reached_date": "2026-10-07", "reached_days": 5,
           "reached_high": 2070.0, "reached_pct": 5.13}
    daily = {old["code"]: _kumbi_daily()}
    up, s = w.track([old], date(2026, 10, 8), "2026-10-08T07:00:00+09:00", fetch=lambda r, t: daily,
                    fetch_min=lambda c, d: MIN_1007)
    u = up[old["id"]]
    assert (u["status"], u["reached_high"], u["reach_rule"]) == ("reached", 2085, w.REACH_RULE)
    assert s["rechecked"] == 1 and s["unconfirmed_reverted"] == [] and s["reached_new"] == 0
    up, s = w.track([old], date(2026, 10, 8), "2026-10-08T07:00:00+09:00", fetch=lambda r, t: daily,
                    fetch_min=lambda c, d: [])
    u = up[old["id"]]
    assert (u["status"], u["reached_high"], u["reach_rule"]) == ("reached", 2070.0, w.PREV_REACH_RULE)
    assert s["recheck_kept_stored"] == ["407400.KQ(2026-10-02 week)@2026-10-07"]
    assert w.is_target({**old, "stage": "restart"})                                  # 옛 규칙이면 종료 단계여도 다시 본다


def test_page_section_order():
    body = SRC[SRC.index("function renderLowpointWatch("):]
    body = body[:body.index("\nfunction ", 10)]
    tail = body[body.rindex("return `"):]
    order = [tail.index(x) for x in ("${restCard}", "${groups ||", "${endCard}")]
    assert order == sorted(order)                                                     # 숨고르기 → 관찰 중 → 종료
    assert "<details><summary" in body and body.count("<details") == 1                # 종료만 접힘(그대로)
    assert re.search(r"<li><b>출발</b>.*확정된 봉만", body)
