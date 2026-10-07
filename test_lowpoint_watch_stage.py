"""v5.337 — 저점 관찰 출발 이후 단계: 관찰 → 출발 → 숨고르기 → 재출발 / 무효.

사용자 지시 요지: "저점일지 관찰은 지금 +5% 도달을 '이미 올라버림 → 종료'로 처리한다. 그런데 WSI처럼 바닥에서 1차 출발한
뒤 숨고르기하는 종목은 도달 이후가 진입 자리다. … 관심 신호이고 측정 전이다. 새 임계값은 만들지 않는다."
  출발 = 지금의 도달 판정 그대로(v5.333) · 출발 고가 = 출발일 판정 고가(reached_high)
  무효선 = 기준일부터 출발 전날까지 통합 종가 최고값(기준일 포함, 출발일 제외)
  숨고르기 = 출발 다음 거래일부터 무효선 ≤ 종가 ≤ 출발 고가 · 재출발 = 처음 종가 > 출발 고가 · 무효 = 처음 종가 < 무효선
  판정은 일봉 종가만(분봉 금지), 시간 제한 없음. 무효선 > 출발 고가면 판정하지 않고 경고.

사보타주 확인(2026-10-07, 전부 FAIL 확인 후 원복):
① 무효선에 출발일 종가 포함(pre 구간 `d.index < dep` → `<=`) → test_invalid_line_includes_base_excludes_departure ·
   test_transitions[resting] FAIL
② 판정에 종가 대신 고가(after["High"]) → test_judged_on_close_not_high · test_transitions FAIL
③ 경계 비교 바꿔치기(`c > dep` → `>=`, `c < inv` → `<=`) → 각각 test_boundaries FAIL
④ (추가 2 출발일 모양) 윗꼬리를 max(시가,종가) 대신 min(시가,종가)부터 → test_shape_values · test_shape_boundaries FAIL
"""
from __future__ import annotations

import gzip
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
import lowpoint_watch as w  # noqa: E402

import app  # noqa: E402

KST = timezone(timedelta(hours=9))
SRC = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
FX = json.load(gzip.open(os.path.join(ROOT, "test_fixtures", "kumbi_20261006_minutes.json.gz"), "rt", encoding="utf-8"))


def _daily(rows):
    """rows: [(날짜, 종가, 고가, 거래량)] — Open·Low는 판정에 안 쓰여 종가로 채운다."""
    idx = pd.to_datetime([r[0] for r in rows])
    return pd.DataFrame({"Open": [r[1] for r in rows], "High": [r[2] for r in rows], "Low": [r[1] for r in rows],
                         "Close": [r[1] for r in rows], "Volume": [r[3] for r in rows]}, index=idx)


# 기준일 10-01 종가 1,000 · 10-02 1,020(무효선 = 1,020) · 10-05 출발(정규장 고가 1,060, 종가 1,055 — 무효선 계산 제외)
PRE = [("2026-10-01", 1000, 1010, 100), ("2026-10-02", 1020, 1030, 120)]
DEP = ("2026-10-05", 1055, 1080, 500)
DEP_HIGH = 1060.0
THRU = "2026-12-31"


def _si(after, pre=PRE, dep=DEP, dep_high=DEP_HIGH, through=THRU):
    return w.stage_info("2026-10-01", dep[0], dep_high, _daily(pre + [dep] + after), through)


@pytest.mark.parametrize("after,stage,sdate,sclose", [
    ([], "departed", "2026-10-05", None),                                                   # 출발 당일 이후 데이터 없음
    ([("2026-10-06", 1040, 1070, 300), ("2026-10-07", 1030, 1035, 200)], "resting", "2026-10-06", 1030),
    ([("2026-10-06", 1040, 1045, 300), ("2026-10-07", 1061, 1065, 900)], "restart", "2026-10-07", 1061),
    ([("2026-10-06", 1040, 1045, 300), ("2026-10-07", 1019, 1041, 200), ("2026-10-08", 1070, 1070, 1)],
     "invalid", "2026-10-07", 1019),                                                        # 무효 뒤 반등은 안 본다
])
def test_transitions(after, stage, sdate, sclose):
    got = _si(after)
    assert (got["stage"], got["stage_date"], got["stage_close"]) == (stage, sdate, sclose)
    assert got["invalid_line"] == 1020 and got["departure_high"] == DEP_HIGH and got["stage_warning"] is None
    assert got["departure_volume"] == 500


def test_invalid_line_includes_base_excludes_departure():
    # 기준일 종가가 최고 → 무효선 = 기준일 종가
    got = w.stage_info("2026-10-01", DEP[0], DEP_HIGH, _daily([("2026-10-01", 1030, 1030, 1), ("2026-10-02", 1010, 1015, 1),
                                                               DEP]), THRU)
    assert got["invalid_line"] == 1030
    # 출발일 종가(1,055)가 기준일~전날보다 높아도 무효선에 들어가지 않는다 → 1,050 종가는 숨고르기
    got = _si([("2026-10-06", 1050, 1050, 1)])
    assert got["invalid_line"] == 1020 and got["stage"] == "resting"
    # 기준일 이전 봉은 무효선에 안 들어간다
    got = w.stage_info("2026-10-01", DEP[0], DEP_HIGH, _daily([("2026-09-30", 1059, 1059, 1)] + PRE + [DEP]), THRU)
    assert got["invalid_line"] == 1020


@pytest.mark.parametrize("close,stage", [
    (1020, "resting"), (1019, "invalid"),          # 종가 = 무효선 → 숨고르기 유지, 1원 아래 → 무효
    (1060, "resting"), (1061, "restart"),          # 종가 = 출발 고가 → 숨고르기, 1원 위 → 재출발
])
def test_boundaries(close, stage):
    assert _si([("2026-10-06", close, close, 1)])["stage"] == stage


def test_judged_on_close_not_high():
    """장중 고가가 출발 고가를 넘거나(1,090) 무효선 근처여도 종가가 구간 안이면 숨고르기 — 분봉·고가 안 씀."""
    got = _si([("2026-10-06", 1040, 1090, 1), ("2026-10-07", 1025, 1100, 1)])
    assert got["stage"] == "resting"


def test_unconfirmed_bar_excluded():
    """확정 전 오늘 봉(장중·애프터 중 종가)은 단계 판정에서 뺀다 — 종료는 되돌리지 않는 판정이다."""
    after = [("2026-10-06", 1040, 1040, 1), ("2026-10-07", 1000, 1000, 1)]
    assert _si(after, through="2026-10-06")["stage"] == "resting"
    assert _si(after, through="2026-10-07")["stage"] == "invalid"
    kr = lambda hm: w.confirmed_through("KR", f"2026-10-07T{hm}:00+09:00")
    assert (kr("07:00"), kr("15:31"), kr("20:09"), kr("20:10")) == ("2026-10-06", "2026-10-06", "2026-10-06", "2026-10-07")
    assert app.KR_CLOSE_CONFIRMED_HM == 20 * 60 + 10                      # 사본 아님 — app 상수를 import
    us = lambda iso: w.confirmed_through("US", iso)
    assert us("2026-10-07T07:00:00+09:00") == "2026-10-06"              # 뉴욕 10-06 18:00 — 10-06 확정
    assert us("2026-10-07T23:30:00+09:00") == "2026-10-06"              # 뉴욕 10-07 10:30 장중 — 10-07 미확정
    assert us("2026-10-08T05:01:00+09:00") == "2026-10-07"              # 뉴욕 10-07 16:01
    assert w.confirmed_through("KR", "not-a-date") is None and w.confirmed_through("KR", "2026-10-07T07:00:00") is None


def test_inverted_lines_not_judged_and_warned():
    """무효선 > 출발 고가(KR 출발 고가 = 정규장 고가 — 출발 전 장외 종가가 더 높을 수 있다) → 판정 안 함 + 경고."""
    got = _si([("2026-10-06", 1000, 1000, 1)], pre=[("2026-10-01", 1000, 1000, 1), ("2026-10-02", 1065, 1065, 1)])
    assert got["stage"] == "departed" and got["stage_date"] == "2026-10-05"
    assert got["stage_warning"] == "무효선 1065 > 출발 고가 1060"
    assert _si([], dep_high=None)["stage_warning"] == "출발 고가 없음"
    assert w.stage_info("2026-09-01", DEP[0], DEP_HIGH, _daily(PRE + [DEP]), THRU)["stage_warning"] == "기준일 2026-09-01 일봉 없음"


# ── track(): KR·US 경로 ───────────────────────────────────────────
def _rec(code, mkt, **k):
    return {"id": f"w_week_2026-10-02_{code}", "tf": "week", "label": "2026-10-02", "code": code, "mkt": mkt,
            "base_date": "2026-10-01", "base_price": 1000.0, "status": "active", **k}


def _bar(day, high):
    return [{"localDateTime": day.replace("-", "") + "100000", "highPrice": high}]


def test_kr_path_departs_on_regular_high_then_stages_on_integrated_close():
    """KR: 출발은 정규장 분봉 고가(10-05 1,060 ≥ 1,050), 단계는 통합 종가 — 분봉은 출발 판정에만."""
    daily = _daily(PRE + [DEP, ("2026-10-06", 1062, 1062, 1)])           # 10-06 통합 종가 1,062 > 정규장 출발 고가 1,060
    asked = []
    fm = lambda c, d: asked.append(str(d)) or _bar(str(d), 1060 if str(d) == "2026-10-05" else 1040)
    up, s = w.track([_rec("111111.KQ", "KR")], date(2026, 10, 7), "2026-10-07T07:00:00+09:00",
                    fetch=lambda recs, t: {"111111.KQ": daily}, fetch_min=fm)
    u = up["w_week_2026-10-02_111111.KQ"]
    assert asked == ["2026-10-02", "2026-10-05"]                         # 출발일에서 멈춘다(이후 분봉 안 받음)
    assert (u["status"], u["reached_date"], u["departure_high"], u["invalid_line"]) == ("reached", "2026-10-05", 1060.0, 1020)
    assert (u["stage"], u["stage_date"], u["stage_close"]) == ("restart", "2026-10-06", 1062)
    assert s["transitions"] == ["111111.KQ(2026-10-02 week) watch→restart"] and s["first_staged"] == []


def test_us_path_departs_on_daily_high_then_rests():
    rec = {**_rec("WSI", "US"), "base_price": 10.0}
    daily = _daily([("2026-10-01", 10.0, 10.1, 100), ("2026-10-02", 10.2, 10.3, 100), ("2026-10-05", 10.4, 10.6, 400),
                    ("2026-10-06", 10.3, 10.45, 100)])
    up, s = w.track([rec], date(2026, 10, 7), "2026-10-07T07:00:00+09:00", fetch=lambda recs, t: {"WSI": daily},
                    fetch_min=lambda c, d: (_ for _ in ()).throw(AssertionError("US는 분봉 없음")))
    u = up[rec["id"]]
    assert (u["reached_date"], u["departure_high"], u["invalid_line"], u["stage"]) == ("2026-10-05", 10.6, 10.2, "resting")
    assert (u["departure_volume"], u["last_volume"], u["last_checked_date"]) == (400, 100, "2026-10-06")


def test_reverted_record_back_to_watch_clears_stage():
    old = {**_rec("407400.KQ", "KR", status="reached", reached_date="2026-10-06", reached_days=4, stage="resting",
                  invalid_line=1969.0), "base_date": "2026-10-02", "base_price": 1969.0}
    daily = pd.DataFrame(FX["daily"]).T.rename_axis(None)
    daily.index = pd.to_datetime(daily.index)
    up, s = w.track([old], date(2026, 10, 7), "2026-10-07T07:00:00+09:00", fetch=lambda recs, t: {old["code"]: daily},
                    fetch_min=lambda c, d: FX["minutes"][str(d)])
    u = up[old["id"]]
    assert u["status"] == "active" and u["stage"] == "watch" and u["invalid_line"] is None and u["departure_high"] is None


# 기존 도달 레코드(v5.333 정규장 규칙으로 도달, 단계 필드 없음) 재분류 픽스처 — 일봉은 이후 종가만 다르다
EXISTING = [
    ("A01.KS", "KR", [("2026-10-06", 1040, 1040, 250)], "resting", "2026-10-06"),
    ("A02.KQ", "KR", [("2026-10-06", 1040, 1040, 250), ("2026-10-07", 1065, 1065, 900)], "restart", "2026-10-07"),
    ("A03.KQ", "KR", [("2026-10-06", 1010, 1010, 250)], "invalid", "2026-10-06"),
    ("B01", "US", [], "departed", "2026-10-05"),
    ("B02", "US", [("2026-10-06", 1050, 1050, 50), ("2026-10-07", 1018, 1018, 50)], "invalid", "2026-10-07"),
]


def _existing():
    recs, data = [], {}
    for code, mkt, after, _, _ in EXISTING:
        recs.append({**_rec(code, mkt), "status": "reached", "reach_rule": w.REACH_RULE, "reached_date": "2026-10-05",
                     "reached_days": 4, "reached_high": DEP_HIGH, "reached_pct": 6.0, "rev": 1, "name": code})
        data[code] = _daily(PRE + [DEP] + after)
    return recs, data


def test_existing_reached_records_reclassified():
    recs, data = _existing()
    up, s = w.track(recs, date(2026, 10, 8), "2026-10-08T21:00:00+09:00", fetch=lambda r, t: data,
                    fetch_min=lambda c, d: (_ for _ in ()).throw(AssertionError("분봉 재조회 금지")))
    got = {i.split("_")[-1]: (u["stage"], u["stage_date"]) for i, u in up.items()}
    assert got == {c: (st, sd) for c, _, _, st, sd in EXISTING}
    assert s["staged"] == 5 and s["active"] == 0 and s["rejudged"] == 0 and s["transitions"] == []
    assert sorted(s["first_staged"]) == sorted((st, f"{c}(2026-10-02 week)") for c, _, _, st, _ in EXISTING)


def test_app_job_classifies_logs_and_freezes_terminal(monkeypatch, tmp_path, capsys):
    for k, v in (("LOWPOINT_DATA_PATH", "d.json"), ("LOWPOINT_LATEST_PATH", "r.json"), ("LOWPOINT_STATE_PATH", "s.json")):
        monkeypatch.setattr(app, k, str(tmp_path / v))
    recs, data = _existing()
    app._rec_list_write(app.LP_WATCH_PATH, recs)
    seen = []
    monkeypatch.setattr(w.track, "__defaults__", (lambda r, t: seen.append(sorted(x["code"] for x in r)) or data,
                                                  lambda c, d: (_ for _ in ()).throw(AssertionError("분봉"))))
    app._lp_watch_job_blocking("watch", datetime(2026, 10, 8, 21, 0, tzinfo=KST))
    out = capsys.readouterr().out
    assert ("기존 도달 레코드 단계 분류 5건 — 출발 1건(B01(2026-10-02 week)) · 숨고르기 1건(A01.KS(2026-10-02 week)) · "
            "재출발 1건(A02.KQ(2026-10-02 week)) · 무효 2건(A03.KQ(2026-10-02 week), B02(2026-10-02 week))") in out
    assert "단계 — 출발 레코드 5건 확인 · 전환 0건: 없음" in out
    by = {r["code"]: r for r in app._rec_list_load(app.LP_WATCH_PATH)}
    assert {c: (r["stage"], r["rev"]) for c, r in by.items()} == {c: (st, 2) for c, _, _, st, _ in EXISTING}
    # 다음 추적: 종료(재출발·무효)는 조회하지 않고 그대로, 남은 것만 단계 전환 로그
    data["A01.KS"] = _daily(PRE + [DEP, ("2026-10-06", 1040, 1040, 250), ("2026-10-08", 1061, 1061, 1)])
    app._lp_watch_job_blocking("watch", datetime(2026, 10, 9, 7, 0, tzinfo=KST))
    assert seen[-1] == ["A01.KS", "B01"]
    out = capsys.readouterr().out
    assert "전환 1건: A01.KS(2026-10-02 week) resting→restart" in out and "기존 도달 레코드 단계 분류 0건" in out
    by = {r["code"]: r for r in app._rec_list_load(app.LP_WATCH_PATH)}
    assert by["A02.KQ"]["rev"] == 2 and by["A01.KS"]["stage"] == "restart" and by["A01.KS"]["rev"] == 3


def test_app_job_warns_inverted(monkeypatch, tmp_path, capsys):
    for k, v in (("LOWPOINT_DATA_PATH", "d.json"), ("LOWPOINT_LATEST_PATH", "r.json"), ("LOWPOINT_STATE_PATH", "s.json")):
        monkeypatch.setattr(app, k, str(tmp_path / v))
    recs, _ = _existing()
    app._rec_list_write(app.LP_WATCH_PATH, recs[:1])
    bad = {"A01.KS": _daily([("2026-10-01", 1000, 1000, 1), ("2026-10-02", 1070, 1070, 1), DEP])}
    monkeypatch.setattr(w.track, "__defaults__", (lambda r, t: bad, lambda c, d: []))
    app._lp_watch_job_blocking("watch", datetime(2026, 10, 8, 21, 0, tzinfo=KST))
    assert "⚠️ 단계 판정 보류 1건: A01.KS(2026-10-02 week) 무효선 1070 > 출발 고가 1060" in capsys.readouterr().out


def test_no_minutes_in_stage_judgement():
    import inspect
    src = inspect.getsource(w.stage_info).split('"""')[2]
    assert "fetch_min" not in src and "minute" not in src and '"High"' not in src
    assert set(w.TERMINAL_STAGES) == {"restart", "invalid"} and w.STAGES[0] == "watch"


# ── 프론트(node, production 원문 실행) ─────────────────────────────
def _fn(name):
    start = SRC.index(f"function {name}(")
    i = SRC.index("{", SRC.index(")", start))
    d = 0
    for j in range(i, len(SRC)):
        d += {"{": 1, "}": -1}.get(SRC[j], 0)
        if d == 0:
            return SRC[start:j + 1]
    raise AssertionError(name)


FNS = ("lpwEndedLabel", "lpwDepHigh", "lpwRestPos", "lpwVolRatio", "lpwStageSplit", "lpwShapeText")


def _js(expr):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    p = subprocess.run(["node", "-e", "\n".join(_fn(f) for f in FNS) + f"\nconsole.log(JSON.stringify({expr}));"],
                       capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


R = [
    {"id": "w1", "code": "W1", "status": "active"},
    {"id": "r1", "code": "R1", "status": "reached", "stage": "resting", "invalid_line": 100, "departure_high": 110, "last_close": 108},
    {"id": "r2", "code": "R2", "status": "reached", "stage": "resting", "invalid_line": 100, "departure_high": 110, "last_close": 101},
    {"id": "r3", "code": "R3", "status": "reached", "stage": "departed", "invalid_line": 50, "departure_high": 60, "last_close": 52},
    {"id": "r4", "code": "R4", "status": "reached", "reached_high": 70, "last_close": 65},                  # 분류 전
    {"id": "e1", "code": "E1", "status": "reached", "stage": "restart", "stage_date": "2026-10-06"},
    {"id": "e2", "code": "E2", "status": "reached", "stage": "invalid", "stage_date": "2026-10-08"},
]


def test_stage_split_and_rest_sort():
    got = _js(f"(s => [s.rest.map(r => r.id), s.ended.map(r => r.id)])(lpwStageSplit({json.dumps(R)}))")
    assert got == [["r2", "r3", "r1", "r4"], ["e2", "e1"]]           # 무효선에 가까운 순(1% · 4% · 8% · 모름), 종료는 최근 위


def test_rest_position_and_volume():
    got = _js("[lpwRestPos({invalid_line: 100, departure_high: 110, last_close: 105}),"
              " lpwRestPos({invalid_line: 100, departure_high: 110, last_close: 99}),"
              " lpwRestPos({invalid_line: 100, departure_high: 110, last_close: 120}),"
              " lpwRestPos({invalid_line: 100, reached_high: 110, last_close: 110}),"
              " lpwRestPos({invalid_line: null, departure_high: 110, last_close: 105}),"
              " lpwVolRatio({last_volume: 50, departure_volume: 200}), lpwVolRatio({last_volume: 50}),"
              " lpwEndedLabel('restart'), lpwEndedLabel('invalid'), lpwEndedLabel('resting')]")
    assert got[0] == {"pos": 50, "dist": 5} and got[1]["pos"] == 0 and got[1]["dist"] == -1 and got[2]["pos"] == 100
    assert got[3] == {"pos": 100, "dist": 10} and got[4] == {"pos": None, "dist": None}
    assert got[5:] == [0.25, None, "재출발", "무효", None]


def test_page_sections_and_definitions():
    body = _fn("renderLowpointWatch")
    order = [body.index(s) for s in ("${groups ||", "${restCard}", "${endCard}")]
    assert order == sorted(order)                                        # 관찰 중 → 숨고르기 → 종료
    assert "<details><summary" in body and "종료 · 재출발" in body          # 종료는 접힘
    assert body.count("<details") == 1                                    # 숨고르기는 펼침(접힘은 종료뿐)
    for col in ("경과", "무효선", "출발 고가", "현재가", "무효선 ~ 출발 고가", "거래량"):
        assert f">{col}</th>" in body, col
    for col in ("결과", "전환일", "출발일"):
        assert f">{col}</th>" in body, col
    assert "관심 신호 · 측정 전" in body
    defs = re.findall(r"<li><b>(.*?)</b>", body)
    assert defs == ["출발", "무효선", "숨고르기", "재출발", "무효"]


# ── v5.337 "추가 2" 출발일 모양 — 기록·표시 전용(판정 반영 금지) ─────────────
def _ohlcv(n_prev, prev_vol, dep_bar, after=()):
    """출발일(2026-10-05) 앞 n_prev거래일(거래량 prev_vol) + 출발 봉(o,h,l,c,v) + 이후 봉."""
    days = pd.bdate_range(end="2026-10-02", periods=n_prev)
    rows = [(d, 100.0, 100.0, 100.0, 100.0, prev_vol) for d in days] + [(pd.Timestamp("2026-10-05"), *dep_bar)]
    rows += [(pd.Timestamp(d), *b) for d, b in after]
    return pd.DataFrame({k: [r[i + 1] for r in rows] for i, k in enumerate(("Open", "High", "Low", "Close", "Volume"))},
                        index=pd.DatetimeIndex([r[0] for r in rows]))


def test_shape_values():
    # 시가 100 · 고가 110 · 저가 95 · 종가 104 → 위치 (104−95)/15 = 0.6, 윗꼬리 (110−104)/15 = 0.4, 거래량 600/250 = 2.4
    s = w.departure_shape(_ohlcv(50, 250, (100, 110, 95, 104, 600)), "2026-10-05", THRU)
    assert s == {"departure_vol_mult": 2.4, "departure_close_pos": pytest.approx(0.6), "departure_upper_wick": pytest.approx(0.4)}
    # 음봉(시가 > 종가)이면 윗꼬리는 시가부터: (110−108)/15
    s = w.departure_shape(_ohlcv(50, 250, (108, 110, 95, 104, 600)), "2026-10-05", THRU)
    assert s["departure_upper_wick"] == pytest.approx(2 / 15) and s["departure_close_pos"] == pytest.approx(0.6)


def test_shape_boundaries():
    flat = w.departure_shape(_ohlcv(50, 250, (100, 100, 100, 100, 600)), "2026-10-05", THRU)
    assert flat["departure_close_pos"] is None and flat["departure_upper_wick"] is None   # 고가 = 저가 → None
    assert flat["departure_vol_mult"] == 2.4
    top = w.departure_shape(_ohlcv(50, 250, (95, 110, 95, 110, 600)), "2026-10-05", THRU)
    assert (top["departure_close_pos"], top["departure_upper_wick"]) == (1.0, 0.0)         # 고가 마감 = 위치 100% · 꼬리 0
    assert w.departure_shape(_ohlcv(49, 250, (100, 110, 95, 104, 600)), "2026-10-05", THRU)["departure_vol_mult"] is None
    assert w.departure_shape(_ohlcv(50, 0, (100, 110, 95, 104, 600)), "2026-10-05", THRU)["departure_vol_mult"] is None
    # 직전 50거래일만(출발일·그 이후 제외) — 51번째 앞 봉의 거대 거래량은 안 들어간다
    d = _ohlcv(51, 250, (100, 110, 95, 104, 600), after=[("2026-10-06", (100, 100, 100, 100, 10 ** 9))])
    d.iloc[0, d.columns.get_loc("Volume")] = 10 ** 9
    assert w.departure_shape(d, "2026-10-05", THRU)["departure_vol_mult"] == 2.4
    # 출발일 봉 미확정(오늘 장중 출발) → 전부 None
    assert w.departure_shape(_ohlcv(50, 250, (100, 110, 95, 104, 600)), "2026-10-05", "2026-10-04") == \
        {"departure_vol_mult": None, "departure_close_pos": None, "departure_upper_wick": None}
    assert w._vol_avg_bars() == 50                                     # abc_screener.ABC_CONFIG["gate_break_vol_avg"] 재사용


def test_shape_does_not_affect_stage():
    """판정 반영 금지 — stage_info는 모양 값을 보지 않고, track()의 단계는 모양이 달라도 같다."""
    import inspect
    assert "departure_shape" not in inspect.getsource(w.stage_info) and "_vol_avg_bars" not in inspect.getsource(w.stage_info)
    after = [("2026-10-06", (100, 104, 100, 102, 50))]
    stages = []
    for dep_bar in ((100, 110, 95, 104, 600), (100, 110, 95, 98, 5), (100, 104, 104, 104, 10 ** 6)):
        d = _ohlcv(50, 250, dep_bar, after)
        rec = {**_rec("S1.KS", "KR", status="reached", reach_rule=w.REACH_RULE, reached_date="2026-10-05",
                      reached_high=104.0), "base_date": "2026-10-02", "base_price": 100.0}
        up, _ = w.track([rec], date(2026, 10, 8), "2026-10-08T07:00:00+09:00", fetch=lambda r, t: {"S1.KS": d},
                        fetch_min=lambda c, dd: [])
        u = up[rec["id"]]
        stages.append((u["stage"], u["stage_date"], u["invalid_line"]))
        assert "departure_vol_mult" in u and "departure_upper_wick" in u
    assert len(set(stages)) == 1 and stages[0][0] == "resting"


def test_shape_text_and_column():
    got = _js("[lpwShapeText({departure_vol_mult: 2.4, departure_upper_wick: 0.6}),"
              " lpwShapeText({departure_vol_mult: null, departure_upper_wick: null}),"
              " lpwShapeText({departure_vol_mult: 2.44, departure_upper_wick: 0.004})]")
    assert got == ["출발 vol 2.4× · 윗꼬리 60%", "출발 vol — · 윗꼬리 —", "출발 vol 2.4× · 윗꼬리 0%"]
    body = _fn("renderLowpointWatch")
    assert '<th class="r">출발일 모양</th><th class="r" title="최근 거래량 ÷ 출발일 거래량">거래량</th>' in body   # 나란히
    assert body.count("${lpwShapeText(r)}") == 1
