"""v5.365 저점관리 — 바닥선 지킴·이탈·스프링 추적(scripts/screens/lowpoint_floor.py) + API + 탭 필터.

사보타주 확인(2026-11-07, FAIL 확인 후 원복):
  ① classify의 스프링 거래량 조건 `ratio >= 1` → `ratio >= 0` — test_spring_needs_volume_and_upper_close FAIL
  ② SPRING_MAX_DAYS 비교 `days > SPRING_MAX_DAYS` → `days > 99` — test_late_recovery_after_spring_window FAIL
  ③ track이 prev의 first_seen을 버림 — test_track_accumulates_first_seen_and_transitions FAIL
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts", "screens"))
import app  # noqa: E402
import lowpoint as lp  # noqa: E402
import lowpoint_floor as fl  # noqa: E402

KST = timezone(timedelta(hours=9))
SRC = open(os.path.join(os.path.dirname(__file__), "static", "index.html"), encoding="utf-8").read()


def _identity(df):
    return df


# ── classify: 손으로 만든 일봉(히트 이후 구간만 의미 있음) ─────────────────
def _days(rows, start="2026-01-05"):
    """rows = [(o,h,l,c,v)] → 영업일 인덱스 일봉."""
    idx = pd.bdate_range(start, periods=len(rows))
    return pd.DataFrame(rows, index=idx, columns=["Open", "High", "Low", "Close", "Volume"], dtype=float)


HIT = {"tf": "week", "label": "2026-01-09", "bar_date": "2026-01-09", "prev_bar_date": "2026-01-02", "close0": 1000.0}
FLOOR = 950.0


def _with_hit_week(after):
    """1주(히트 주: 저가 950) + 이후 일봉."""
    hit_week = [(960, 1000, 950, 990, 100)] + [(990, 1000, 970, 995, 100)] * 4
    return _days(hit_week + after)


def test_holding_counts_support_tests_and_distance():
    after = [(1000, 1010, 975, 1000, 100),      # 저가 975 ≤ 950×1.03(978.5) · 종가 지킴 → 테스트
             (1000, 1020, 990, 1010, 100),      # 근처 아님
             (1005, 1010, 960, 980, 100),       # 테스트
             (980, 1000, 970, 999, 100)]        # 테스트
    d = _with_hit_week(after)
    out = fl.classify(d, HIT, FLOOR, str(d.index[-1].date()))
    assert out["state"] == "holding" and out["tests"] == 3
    assert out["last_test_date"] == str(d.index[-1].date())
    assert out["dist_pct"] == round((999 / 950 - 1) * 100, 2)
    assert out["days_tracked"] == 4                                   # 히트 주 다음 날부터


def test_spring_needs_volume_and_upper_close():
    brk = [(960, 965, 930, 940, 300)]                                  # 종가 940 < 950 이탈, 거래량 300
    dip = [(940, 945, 900, 930, 200), (930, 940, 910, 935, 200)]       # 이탈 중 최저 900
    rec_ok = [(935, 990, 930, 985, 400)]                               # 3거래일 뒤 회복, 거래량 400 ≥ 300, 종가 위쪽
    d = _with_hit_week(brk + dip + rec_ok)
    out = fl.classify(d, HIT, FLOOR, str(d.index[-1].date()))
    assert out["state"] == "spring" and out["new_line"] == 900 and out["active_line"] == 900
    assert out["recover_days"] == 3 and out["recover_vol_ratio"] == round(400 / 300, 2)
    assert out["dist_pct"] == round((985 / 900 - 1) * 100, 2)
    assert [e["ev"] for e in out["events"]] == ["break", "spring"]
    # 거래량 부족 → 약한 회복
    d2 = _with_hit_week(brk + dip + [(935, 990, 930, 985, 250)])
    assert fl.classify(d2, HIT, FLOOR, str(d2.index[-1].date()))["state"] == "weak_recovery"
    # 종가가 봉 아래쪽(범위 930~1100, 종가 960 → 0.18) → 약한 회복
    d3 = _with_hit_week(brk + dip + [(935, 1100, 930, 960, 500)])
    assert fl.classify(d3, HIT, FLOOR, str(d3.index[-1].date()))["state"] == "weak_recovery"


def test_late_recovery_after_spring_window():
    brk = [(960, 965, 930, 940, 300)]
    under = [(940, 945, 910, 930, 100)] * (fl.SPRING_MAX_DAYS + 1)
    d = _with_hit_week(brk + under + [(930, 1000, 925, 995, 900)])
    out = fl.classify(d, HIT, FLOOR, str(d.index[-1].date()))
    assert out["state"] == "late_recovery" and out["recover_days"] == fl.SPRING_MAX_DAYS + 2
    # 경계: 정확히 SPRING_MAX_DAYS째 회복은 스프링 후보
    under2 = [(940, 945, 910, 930, 100)] * (fl.SPRING_MAX_DAYS - 1)
    d2 = _with_hit_week(brk + under2 + [(930, 1000, 925, 995, 900)])
    assert fl.classify(d2, HIT, FLOOR, str(d2.index[-1].date()))["state"] == "spring"


def test_rebroken_after_recovery_is_terminal():
    brk = [(960, 965, 930, 940, 300), (940, 945, 900, 930, 200)]
    rec = [(935, 990, 930, 985, 400)]
    rebreak = [(985, 990, 880, 890, 300)]                              # 새 바닥선 900 아래 종가
    later = [(890, 1200, 885, 1150, 900)]                              # 다시 올라와도 상태는 재이탈(종료)
    d = _with_hit_week(brk + rec + rebreak + later)
    out = fl.classify(d, HIT, FLOOR, str(d.index[-1].date()))
    assert out["state"] == "rebroken" and out["rebreak_date"] == str(d.index[-2].date())
    assert out["last_close"] == 1150                                  # 가격·거리는 계속 갱신


def test_only_confirmed_bars_count():
    """확정 날짜(through) 이후 봉은 안 본다 — 장중 이탈은 이탈이 아니다."""
    d = _with_hit_week([(1000, 1010, 990, 1000, 100), (990, 995, 900, 920, 500)])
    out = fl.classify(d, HIT, FLOOR, str(d.index[-2].date()))
    assert out["state"] == "holding" and out["last_date"] == str(d.index[-2].date())


def test_intraday_wick_below_is_not_break():
    d = _with_hit_week([(1000, 1010, 900, 960, 500)])                 # 저가 900, 종가 960 ≥ 950
    out = fl.classify(d, HIT, FLOOR, str(d.index[-1].date()))
    assert out["state"] == "holding" and out["tests"] == 1


# ── 히트·바닥선: 저점 스크린 판정 그대로 ──────────────────────────────────
def _hit_series():
    """주봉: 20주 횡보(±2%) → 주당 −3% 하락을 RSI가 30 아래로 처음 들어갈 때까지 → 다음 주 +4% 양봉(히트) → 8주 횡보.
    주마다 5영업일로 펼친다(주 안에서는 선형). RSI는 저점 스크린 함수 그대로로 확인하며 만든다."""
    wk = [1000.0]
    for i in range(20):
        wk.append(wk[-1] * (1.02 if i % 2 else 0.98))
    while True:
        wk.append(wk[-1] * 0.97)
        r = lp.rsi_wilder_sma(pd.Series(wk), lp.RSI_PERIOD)
        if r.iloc[-1] < lp.RSI_LEVEL and r.iloc[-2] >= lp.RSI_LEVEL:
            break
        assert len(wk) < 200
    wk.append(wk[-1] * 1.04)
    for i in range(8):
        wk.append(wk[-1] * (1.01 if i % 2 else 0.99))
    closes = []
    for a_, b_ in zip(wk[:-1], wk[1:]):
        closes += [a_ + (b_ - a_) * (k + 1) / 5 for k in range(5)]
    idx = pd.bdate_range("2024-01-01", periods=len(closes))      # 월요일 시작 → 5영업일 = W-FRI 한 주
    c = pd.Series(closes, index=idx)
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.98, "Close": c, "Volume": 1000.0}, index=idx)


def test_latest_hit_matches_lowpoint_screen_and_floor_is_week_low():
    df = _hit_series()
    now = (df.index[-1] + pd.Timedelta(days=10)).to_pydatetime().replace(tzinfo=KST)
    hit = fl.latest_hit(df["Close"], "kr", now)
    assert hit and hit["tf"] == "week"
    cut = df["Close"][df.index <= pd.Timestamp(hit["bar_date"])]
    ev = lp.evaluate(cut, "week", "kr", now)
    assert ev["status"] == "hit" and str(ev["label"].date()) == hit["label"]      # 저점 스크린과 같은 히트
    week = df[(df.index > pd.Timestamp(hit["prev_bar_date"])) & (df.index <= pd.Timestamp(hit["bar_date"]))]
    assert fl.floor_of(df, hit) == float(week["Low"].min())


def test_evaluate_uses_downcast_so_zero_bars_do_not_make_floor_zero():
    df = _hit_series()
    now = (df.index[-1] + pd.Timedelta(days=10)).to_pydatetime().replace(tzinfo=KST)
    hit = fl.latest_hit(df["Close"], "kr", now)
    bad = df.copy()
    i = bad.index[(bad.index > pd.Timestamp(hit["prev_bar_date"])) & (bad.index <= pd.Timestamp(hit["bar_date"]))][1]
    bad.loc[i, ["Open", "High", "Low"]] = 0.0                          # naver 무거래일 오염 모양(종가만 이월)
    r = fl.evaluate(bad, "kr", now, str(df.index[-1].date()), app._downcast)
    assert r["status"] == "ok" and r["floor_line"] > 0
    raw = fl.evaluate(bad, "kr", now, str(df.index[-1].date()), _identity)
    assert raw["floor_line"] > 0                                       # floor_of 자체도 0 이하 저가는 뺀다(2차 방어)


def test_no_days_after_hit_uses_hit_close_for_distance():
    """히트 봉 직후(추적일 0) — 거리는 히트 봉 마지막 종가로(v5.366, 인바이오젠 11-06 주봉 실데이터에서 '—'로 나옴)."""
    d = _with_hit_week([])
    out = fl.classify(d, HIT, FLOOR, str(d.index[-1].date()))
    assert out["state"] == "holding" and out["days_tracked"] == 0
    assert out["last_close"] == 995.0 and out["last_date"] == "2026-01-09"
    assert out["dist_pct"] == round((995 / 950 - 1) * 100, 2)


def test_latest_hit_takes_most_recent_across_week_and_month(monkeypatch):
    """옛 주봉 히트가 최근 월봉 히트를 가리면 안 된다(v5.366 — 하이딥 실데이터: 7월 주봉 히트로 추적됨)."""
    import numpy as np
    rng = np.random.default_rng(7)
    idx = pd.bdate_range("2023-01-02", periods=700)
    c = pd.Series(1000 * np.exp(np.cumsum(rng.normal(0, 0.01, len(idx)))), index=idx)
    now = (idx[-1] + pd.Timedelta(days=40)).to_pydatetime().replace(tzinfo=KST)
    wk = lp.drop_in_progress(lp.resample_bars(c, "week"), "kr", now)
    mo = lp.drop_in_progress(lp.resample_bars(c, "month"), "kr", now)
    old_week = float(wk["close"].iloc[40])
    j = next(j for j in range(len(mo) - 2, 0, -1) if pd.Timestamp(mo["bar_date"].iloc[j]).weekday() != 4)  # 금요일 아닌 월말
    last_month = float(mo["close"].iloc[j])
    assert last_month not in set(wk["close"].astype(float))                       # 주봉 종가와 겹치지 않는 월봉
    real = lp.lowpoint_signal
    monkeypatch.setattr(lp, "lowpoint_signal", lambda c0, c1, r1, r2: c0 in (old_week, last_month))
    hit = fl.latest_hit(c, "kr", now)
    assert hit["tf"] == "month" and hit["close0"] == last_month
    monkeypatch.setattr(lp, "lowpoint_signal", lambda c0, c1, r1, r2: c0 in (old_week,))
    assert fl.latest_hit(c, "kr", now)["tf"] == "week"
    monkeypatch.setattr(lp, "lowpoint_signal", real)


def test_no_hit_and_no_data():
    up = pd.DataFrame({"Close": [1000 * 1.002 ** i for i in range(400)]}, index=pd.bdate_range("2025-01-06", periods=400))
    up["Open"] = up["High"] = up["Low"] = up["Close"]
    up["Volume"] = 100.0
    now = datetime(2026, 11, 7, 12, tzinfo=KST)
    assert fl.evaluate(up, "kr", now, "2026-11-06", _identity)["status"] == "no_hit"
    assert fl.evaluate(None, "kr", now, "2026-11-06", _identity)["status"] == "no_data"


# ── 대상·누적 ────────────────────────────────────────────────────────────
def test_targets_union_and_keep_previous():
    watch = [{"code": "111111.KQ", "name": "가", "market": "KOSDAQ", "tf": "week"},
             {"code": "111111.KQ", "name": "가", "market": "KOSDAQ", "tf": "month"},
             {"code": "AAA", "name": "A", "market": "US", "tf": "week"}]
    nl = [{"code": "222222.KS", "name": "나", "market": "KOSPI"}, {"code": "AAA", "name": "A", "market": "US"}]
    prev = {"333333.KQ": {"name": "다", "market": "KOSDAQ", "sources": ["저점 주봉"]}}
    t = fl.targets_from(watch, nl, prev)
    assert set(t) == {"111111.KQ", "AAA", "222222.KS", "333333.KQ"}                # 빠진 종목도 계속 추적
    assert t["111111.KQ"]["sources"] == ["저점 주봉", "저점 월봉"] and t["AAA"]["sources"] == ["저점 주봉", "신규상장"]
    assert t["AAA"]["mkt"] == "US" and t["222222.KS"]["mkt"] == "KR"
    assert t["333333.KQ"]["sources"] == ["저점 주봉"]


def test_track_accumulates_first_seen_and_transitions():
    df = _hit_series()
    now = (df.index[-1] + pd.Timedelta(days=10)).to_pydatetime().replace(tzinfo=KST)
    targets = {"111111.KQ": {"code": "111111.KQ", "name": "가", "market": "KOSDAQ", "mkt": "KR", "sources": ["저점 주봉"]},
               "999999.KQ": {"code": "999999.KQ", "name": "실패", "market": "KOSDAQ", "mkt": "KR", "sources": []}}
    hit = fl.latest_hit(df["Close"], "kr", now)
    prev = {"111111.KQ": {"first_seen": "2026-10-01", "state": "broken", "hit": {"label": hit["label"]},
                          "transitions": [{"at": "2026-10-02", "from": "holding", "to": "broken"}]},
            "999999.KQ": {"first_seen": "2026-10-01", "state": "spring", "dist_pct": 3.0, "status": "ok"}}
    thru = lambda mkt, iso: str(df.index[-1].date())                     # noqa: E731
    rows, summ = fl.track(targets, prev, now, _identity, thru, fetch=lambda items, today: {"111111.KQ": df})
    r = rows["111111.KQ"]
    assert r["first_seen"] == "2026-10-01" and r["state"] == "holding"
    assert [t["to"] for t in r["transitions"]] == ["broken", "holding"]
    assert summ["changes"] and summ["counts"]["holding"] == 1
    # 조회 실패 — 이전 결과 유지(빈 값으로 덮지 않음)
    assert rows["999999.KQ"]["state"] == "spring" and rows["999999.KQ"]["dist_pct"] == 3.0 and "fetch_failed_at" in rows["999999.KQ"]
    assert summ["failed"] == ["999999.KQ"]


# ── 서버 ─────────────────────────────────────────────────────────────────
def test_api_missing_then_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "LP_FLOOR_PATH", str(tmp_path / "f.json"))
    d = json.loads(asyncio.run(app.lp_floor_get()).body)
    assert d["ok"] and d["missing"] and d["rows"] == [] and "바닥선" in d["definition"]
    (tmp_path / "f.json").write_text(json.dumps({"checked_at": "2026-11-07T07:00:00+09:00", "summary": {"targets": 1},
                                                 "rows": {"A": {"code": "A", "status": "ok", "state": "holding"}}}))
    d = json.loads(asyncio.run(app.lp_floor_get()).body)
    assert d["rows"] == [{"code": "A", "status": "ok", "state": "holding"}] and d["spring_max_days"] == fl.SPRING_MAX_DAYS


def test_watch_job_runs_floor_and_failure_does_not_break_watch(monkeypatch, tmp_path):
    # 저점 게시 파일을 빈 임시 경로로 — 관찰 등록 0건이라 관찰·보유 추적이 네트워크를 타지 않는다
    monkeypatch.setattr(app, "LOWPOINT_DATA_PATH", str(tmp_path / "d.json"))
    monkeypatch.setattr(app, "LOWPOINT_LATEST_PATH", str(tmp_path / "r.json"))
    monkeypatch.setattr(app, "LOWPOINT_STATE_PATH", str(tmp_path / "s.json"))
    calls = []
    monkeypatch.setattr(app, "_lp_floor_job_blocking", lambda now: calls.append(now) or {"targets": 0})
    out = app._lp_watch_job_blocking("watch", datetime(2026, 11, 7, 7, tzinfo=KST))
    assert calls and out["counts"]["floor"] == {"targets": 0}

    def boom(now):
        raise RuntimeError("x")
    monkeypatch.setattr(app, "_lp_floor_job_blocking", boom)
    out = app._lp_watch_job_blocking("watch", datetime(2026, 11, 7, 7, tzinfo=KST))
    assert "error" in out["counts"]["floor"]


def test_floor_job_writes_file(monkeypatch, tmp_path):
    out = app._lp_floor_job_blocking(datetime(2026, 11, 7, 7, tzinfo=KST))   # conftest가 조회를 빈 결과로 막음
    d = json.load(open(app.LP_FLOOR_PATH, encoding="utf-8"))
    assert "rows" in d and out["targets"] == d["summary"]["targets"]


# ── 프론트(production 원문을 node로 실행) ──────────────────────────────────
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
    p = subprocess.run(["node", "-e", _fn("lfTestCount") + "\n" + _fn("lfFilter") + "\n" + _fn("lfSort") + f"\nconsole.log(JSON.stringify({expr}));"],
                       capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


ROWS = ("[{code:'a',status:'ok',state:'holding',tests:3,dist_pct:2},{code:'b',status:'ok',state:'holding',tests:1,dist_pct:-0.5},"
        "{code:'c',status:'ok',state:'spring',dist_pct:8},{code:'d',status:'ok',state:'weak_recovery',dist_pct:1},"
        "{code:'e',status:'ok',state:'late_recovery',dist_pct:null},{code:'f',status:'no_hit'},{code:'g',status:'ok',state:'broken',dist_pct:-4}]")


def test_front_filters():
    out = _js(f"['all','holding','tested','broken','spring','recovery','rebroken'].map(k=>lfFilter({ROWS},k).map(r=>r.code))")
    assert out == [["a", "b", "c", "d", "e", "g"], ["a", "b"], ["a"], ["g"], ["c"], ["d", "e"], []]


def test_front_sort_by_abs_distance_nulls_last():
    assert _js(f"lfSort(lfFilter({ROWS},'all'),'near','asc').map(r=>r.code)") == ["b", "d", "a", "g", "c", "e"]


def test_front_sort_signed_distance_state_and_prior():
    """v5.370 — 거리 부호 그대로(위로 먼 순/아래로 먼 순), 상태 순서(같은 상태는 가까운 순), 전저점 거리. 값 없음은 항상 뒤."""
    assert _js(f"lfSort(lfFilter({ROWS},'all'),'dist','desc').map(r=>r.code)") == ["c", "a", "d", "b", "g", "e"]
    assert _js(f"lfSort(lfFilter({ROWS},'all'),'dist','asc').map(r=>r.code)") == ["g", "b", "d", "a", "c", "e"]
    assert _js(f"lfSort(lfFilter({ROWS},'all'),'state','asc').map(r=>r.code)") == ["b", "a", "c", "d", "e", "g"]
    assert _js(f"lfSort(lfFilter({ROWS},'all'),'state','desc').map(r=>r.code)") == ["g", "e", "d", "c", "b", "a"]
    rows = "[{code:'p',status:'ok',prior_dist_pct:5},{code:'q',status:'ok',prior_dist_pct:-3},{code:'r',status:'ok'}]"
    assert _js(f"lfSort({rows},'prior','desc').map(r=>r.code)") == ["p", "q", "r"]


def test_front_far_and_near_filters_use_server_thresholds():
    rows = ("[{code:'x',status:'ok',state:'holding',dist_pct:134},{code:'y',status:'ok',state:'holding',dist_pct:29},"
            "{code:'z',status:'ok',state:'broken',dist_pct:-9},{code:'w',status:'ok',state:'holding',dist_pct:null}]")
    assert _js(f"lfFilter({rows},'far',{{far:30,near:10}}).map(r=>r.code)") == ["x"]
    assert _js(f"lfFilter({rows},'near',{{far:30,near:10}}).map(r=>r.code)") == ["z"]
    assert _js(f"lfFilter({rows},'far',{{far:20,near:10}}).map(r=>r.code)") == ["x", "y"]     # 서버 값이 바뀌면 따라간다


def test_floor_api_sends_thresholds_and_state_order():
    import inspect
    src = inspect.getsource(app.lp_floor_get)
    assert '"far_pct": fl.FAR_PCT' in src and '"near_pct": fl.NEAR_PCT' in src and '"state_order": list(fl.STATES)' in src
    body = _fn("renderLowpointFloor")
    assert "d.far_pct" in body and "d.near_pct" in body and "d.state_order" in body


def test_tab_wired():
    assert SRC.count('data-mode="lowpoint_floor"') == 1 and SRC.count('id="lowpointFloorView"') == 1
    assert "onEnterLowpointFloorTab();" in SRC and "'lowpoint_floor'" in SRC


def test_startup_catchup_runs_only_when_file_missing(monkeypatch, tmp_path):
    """v5.367 — 결과 파일이 없을 때만 시작 따라잡기가 돈다(재시작마다 돌면 안 된다)."""
    calls = []
    monkeypatch.setattr(app, "_lp_floor_job_blocking", lambda now: calls.append(now) or {"targets": 0})
    real_sleep = asyncio.sleep
    monkeypatch.setattr(app.asyncio, "sleep", lambda s: real_sleep(0))
    p = tmp_path / "lowpoint_floor.json"
    monkeypatch.setattr(app, "LP_FLOOR_PATH", str(p))
    p.write_text("{}")
    asyncio.run(app._lp_floor_catchup())
    assert calls == []
    p.unlink()
    asyncio.run(app._lp_floor_catchup())
    assert len(calls) == 1 and app._lowpoint_running is False


def test_floor_tab_has_own_refresh_button():
    src = open(os.path.join(os.path.dirname(__file__), "static", "index.html"), encoding="utf-8").read()
    body = _fn("renderLowpointFloor")
    assert body.count('onclick="lfRefresh()"') == 1
    assert "/api/lowpoint/watch/refresh" in _fn("lfRefresh")
    assert "저점일지 관찰 탭의" not in src


def test_floor_tab_market_buttons_use_header_toggle():
    body = _fn("renderLowpointFloor")
    assert body.count("lfSetMarket(") == 1 and "'국장'" in body and "'미장'" in body
    assert "button.tab[data-market=" in _fn("lfSetMarket")


def test_prior_low_is_min_low_before_hit_bar_and_break_date():
    """v5.369 전저점 = 히트 봉 구간 전 PRIOR_LOW_DAYS 최저가(참고선). 히트 이후 종가가 처음 그 아래로 마감한 날 기록."""
    pre = [(1000, 1010, 990, 1000, 100)] * 10 + [(1000, 1005, 900, 980, 100)] + [(990, 1000, 980, 990, 100)] * 4
    hit_week = [(960, 1000, 950, 990, 100)] + [(990, 1000, 970, 995, 100)] * 4
    after = [(990, 995, 920, 930, 100), (930, 940, 880, 890, 100), (890, 900, 880, 895, 100)]
    d = _days(pre + hit_week + after, start="2025-12-08")
    hit = {"tf": "week", "label": "2026-01-02", "bar_date": str(d.index[19].date()), "prev_bar_date": str(d.index[14].date()), "close0": 995.0}
    out = fl.prior_low(d, hit, str(d.index[-1].date()))
    assert out["prior_low"] == 900.0 and out["prior_low_date"] == str(d.index[10].date())
    assert out["prior_break_date"] == str(d.index[21].date())                    # 890 < 900 (920 저가는 장중이라 아님)
    assert out["prior_dist_pct"] == round((895 / 900 - 1) * 100, 2)


def test_regular_frame_drives_floor_and_distance_not_integrated():
    """v5.369 — 히트는 통합(raw) 종가로 찾고, 바닥선·상태·거리는 정규장(reg) 일봉으로(꿈비 11-06 통합 1,770 vs 정규장 1,650)."""
    df = _hit_series()
    now = (df.index[-1] + pd.Timedelta(days=10)).to_pydatetime().replace(tzinfo=KST)
    through = str(df.index[-1].date())
    reg = df.copy()
    reg.loc[reg.index[-1], "Close"] = df["Close"].iloc[-1] * 0.9                   # 마지막 날 정규장 종가만 다름
    a = fl.evaluate(df, "kr", now, through, _identity)
    b = fl.evaluate(df, "kr", now, through, _identity, reg=reg)
    assert a["hit"] == b["hit"]                                                       # 히트는 같은 입력(통합)
    assert b["last_close"] == reg["Close"].iloc[-1] and a["last_close"] == df["Close"].iloc[-1]
    rows, _ = fl.track({"X.KQ": {"code": "X.KQ", "name": "X", "market": "KOSDAQ", "mkt": "KR", "sources": []}}, {}, now,
                       _identity, lambda m, n: through, fetch=lambda items, today: {"X.KQ": {"raw": df, "reg": reg, "note": "경고"}})
    assert rows["X.KQ"]["last_close"] == reg["Close"].iloc[-1] and rows["X.KQ"]["price_note"] == "경고"


def test_floor_table_shows_prior_low_and_price_note():
    body = _fn("_lfTable")
    assert body.count("r.prior_low") >= 2 and "r.prior_break_date" in body and "r.price_note" in body
    assert "sb('prior', '전저점'" in body


def test_spacs_excluded_kr_and_us():
    """v5.370 사용자 지시 "스팩은 제외시켜줘"."""
    t = fl.targets_from([{"code": "1.KQ", "name": "엔에이치스팩31호", "market": "KOSDAQ"},
                         {"code": "2.KQ", "name": "꿈비", "market": "KOSDAQ"},
                         {"code": "SPAQ", "name": "Example Acquisition Corp", "market": "US"},
                         {"code": "ACQ", "name": "Acquisition Holdings Bank", "market": "US"}],
                        [{"code": "3.KS", "name": "기업인수목적9호", "market": "KOSPI"}],
                        {"4.KQ": {"name": "하나스팩5호", "market": "KOSDAQ"}})
    kept, gone = fl.drop_spacs(t)
    assert sorted(kept) == ["2.KQ", "ACQ"] and gone == ["1.KQ", "3.KS", "4.KQ", "SPAQ"]


def test_floor_job_drops_spacs_before_tracking(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(app, "LP_FLOOR_PATH", str(tmp_path / "f.json"))
    monkeypatch.setattr(app, "_rec_list_load", lambda p: [{"code": "1.KQ", "name": "엔에이치스팩31호", "market": "KOSDAQ", "tf": "week"},
                                                          {"code": "2.KQ", "name": "꿈비", "market": "KOSDAQ", "tf": "week"}])
    monkeypatch.setattr(app, "_lowpoint_read", lambda p: {"rows": []})
    fl_mod = app._lp_floor_mod()
    monkeypatch.setattr(fl_mod, "fetch_daily", lambda items, today: seen.setdefault("codes", [i["code"] for i in items]) and {})
    app._lp_floor_job_blocking(datetime.now(KST))
    assert seen["codes"] == ["2.KQ"]
    saved = json.load(open(tmp_path / "f.json"))
    assert saved["summary"]["spac_excluded"] == 1


def test_test_days_grouped_into_episodes_with_volume_low_and_bounce():
    """v5.371 — 연속 테스트 날 = 1회차. 회차별 평균 거래량·최저가·반등, 앞 회차 대비 방향."""
    after = [(1000, 1010, 975, 1000, 300),      # 회차1 (저가 975, 거래량 300)
             (1000, 1005, 970, 990, 100),       # 회차1 이어짐 (저가 970) → 평균 200
             (1000, 1040, 990, 1030, 100),      # 범위 밖 — 반등 +8.42%
             (1030, 1060, 1000, 1050, 100),     # 반등 +10.53%
             (1000, 1010, 972, 985, 80),        # 회차2 (저가 972 > 970 → ↑, 거래량 80 < 200 → ↓)
             (990, 1000, 980, 995, 100)]        # 범위 밖 반등 +4.74%
    d = _with_hit_week(after)
    out = fl.classify(d, HIT, FLOOR, str(d.index[-1].date()))
    eps = out["test_episodes"]
    assert out["tests"] == 3 and len(eps) == 2
    assert eps[0]["days"] == 2 and eps[0]["low"] == 970 and eps[0]["vol_avg"] == 200 and eps[0]["bounce_pct"] == round((1050 / 950 - 1) * 100, 2)
    assert eps[0]["vol_dir"] is None and eps[1]["vol_dir"] == "down" and eps[1]["low_dir"] == "up"
    assert eps[1]["bounce_pct"] == round((995 / 950 - 1) * 100, 2)
    assert not any(k.startswith("_") for e in eps for k in e)      # 내부 키가 저장되지 않는다
    json.dumps(out)


def test_front_chips_and_tested_filter():
    """v5.372 — 내용 칸 칩(lfChips). 상태별 칩 글자·색 방향, 회차 없는 옛 행은 날짜 수로."""
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    src = _fn("lfTestCount") + "\n" + _fn("lfChips") + "\n" + _fn("lfFilter")
    r = ("{code:'a',status:'ok',state:'holding',tests:3,test_episodes:[{start:'d1',end:'d2',days:2,low:970,vol_avg:200,bounce_pct:10.53,vol_dir:null,low_dir:null},"
         "{start:'d5',end:'d5',days:1,low:972,vol_avg:80,bounce_pct:4.74,vol_dir:'down',low_dir:'up'}]}")
    old = "{code:'b',status:'ok',state:'holding',tests:5}"
    nob = "{code:'n',status:'ok',state:'holding',test_episodes:[{start:'d1',end:'d3',days:3,low:1,vol_avg:1,bounce_pct:null}]}"
    brk = "{code:'c',status:'ok',state:'broken',break_date:'2026-11-03',break_close:1818,new_line:1815,prior_break_date:'2026-11-04',prior_low:1800,prior_low_date:'x'}"
    spr = "{code:'s',status:'ok',state:'spring',break_date:'x',recover_date:'y',recover_days:5,recover_vol_ratio:1.57}"
    weak = "{code:'w',status:'ok',state:'weak_recovery',recover_days:1,recover_vol_ratio:0.99}"
    reb = "{code:'r',status:'ok',state:'rebroken',recover_date:'x',rebreak_date:'2026-11-05'}"
    expr = f"[{r},{old},{nob},{brk},{spr},{weak},{reb}].map(x=>lfChips(x).map(c=>c.t+'/'+c.tone))"
    p = subprocess.run(["node", "-e", src + f"\nconsole.log(JSON.stringify([{expr}, lfFilter([{r},{old}],'tested').map(x=>x.code)]));"],
                       capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    chips, tested = json.loads(p.stdout)
    assert chips == [["테스트 2회/", "거래량↓/good", "저가↑/good", "반등 +11%/"],
                     ["테스트 5회/"],
                     ["테스트 1회/", "반등 없음/bad"],
                     ["이탈 11-03/bad", "최저 1815/", "전저점 깸/bad"],
                     ["5일 만에 회복/good", "거래량 1.57배/good"],
                     ["1일 만에 회복/good", "거래량 0.99배/bad"],
                     ["재이탈 11-05/bad"]]
    assert tested == ["a", "b"]
    assert _fn("_lfTable").count("lfChips(r") == 1


def test_pre_low_is_min_low_of_bar_before_hit_bar():
    """v5.373 직전봉 저가 = 신호 봉 바로 앞 봉 구간 (prev2_bar_date, prev_bar_date]의 일봉 최저가(코오롱티슈진 10-15 13,090)."""
    pre = [(1000, 1010, 800, 1000, 100)] * 5 + [(1000, 1005, 900, 980, 100)] + [(990, 1000, 940, 990, 100)] * 4
    hit_week = [(960, 1000, 950, 990, 100)] * 5
    after = [(990, 995, 935, 960, 100), (950, 960, 920, 930, 100)]
    d = _days(pre + hit_week + after, start="2025-12-15")
    hit = {"tf": "week", "label": "x", "bar_date": str(d.index[14].date()), "prev_bar_date": str(d.index[9].date()),
           "prev2_bar_date": str(d.index[4].date()), "close0": 990.0}
    out = fl.pre_low(d, hit, str(d.index[-1].date()))
    assert out["pre_low"] == 900.0 and out["pre_low_date"] == str(d.index[5].date())   # 800은 그 앞 봉 — 제외
    assert out["pre_break_date"] is None                                               # 930 > 900
    assert out["pre_dist_pct"] == round((930 / 900 - 1) * 100, 2)
    assert fl.pre_low(d, {**hit, "prev2_bar_date": None}, str(d.index[-1].date()))["pre_low"] is None


def test_latest_hit_carries_prev2_and_table_shows_pre_low():
    df = _hit_series()
    now = (df.index[-1] + pd.Timedelta(days=10)).to_pydatetime().replace(tzinfo=KST)
    h = fl.latest_hit(df["Close"], "kr", now)
    assert h["prev2_bar_date"] < h["prev_bar_date"] < h["bar_date"]
    body = _fn("_lfTable")
    assert body.count("r.pre_low") >= 2 and "직전봉 저가</th>" in body
    assert "r.pre_break_date" in _fn("lfChips")


def test_history_hits_matches_evaluate_on_every_past_bar():
    """v5.374 history_hits = 각 과거 시점에서 evaluate를 돌린 결과와 같다(같은 판정 — 사본 아님을 동작으로 확인)."""
    df = _hit_series()
    c = df["Close"]
    now = (df.index[-1] + pd.Timedelta(days=10)).to_pydatetime().replace(tzinfo=KST)
    got = lp.history_hits(c, "week", "kr", now, since="2000-01-01")
    assert got, "합성 시계열에 신호가 하나도 없으면 테스트가 무의미"
    bars = lp.drop_in_progress(lp.resample_bars(c, "week"), "kr", now)
    expect = []
    for i in range(len(bars)):
        cut = c[c.index <= pd.Timestamp(bars["bar_date"].iloc[i])]
        r = lp.evaluate(cut, "week", "kr", now)
        if r["status"] == "hit":
            expect.append(str(pd.Timestamp(r["bar_date"]).date()))
    assert [h["bar_date"] for h in got] == expect
    later = lp.history_hits(c, "week", "kr", now, since=got[-1]["label"])
    assert [h["label"] for h in later] == [got[-1]["label"]]                       # since 이전 라벨은 제외


def test_history_flows_into_floor_targets_and_schema_rerun():
    t = fl.targets_from([], [], {}, history=[{"market": "KOSDAQ", "code": "217270.KQ", "name": "넵튠", "tf": "week"},
                                             {"market": "US", "code": "VALE", "name": "Vale", "tf": "month"}])
    assert t["217270.KQ"]["sources"] == ["저점 주봉(과거)"] and t["217270.KQ"]["mkt"] == "KR"
    assert t["VALE"]["mkt"] == "US" and t["VALE"]["sources"] == ["저점 월봉(과거)"]
    assert app.LOWPOINT_RESULT_SCHEMA == lp.RESULT_SCHEMA
    entry = lp.publish_entry([{"market": "kosdaq", "rows": [], "universe": 1, "fetched": 1, "failed": [], "stale": {}, "short": {},
                               "meta": {}, "history": [{"code": "X"}]}], "week", {"kosdaq": pd.Timestamp("2026-11-06")}, {})
    assert entry["history"] == [{"code": "X"}] and entry["history_since"] == lp.HISTORY_SINCE
    assert "history" not in lp.publish_entry([], "month", {"us": pd.Timestamp("2026-10-30")}, {})
    import inspect
    assert "history=hist" in inspect.getsource(app._lp_floor_job_blocking)


def test_week_reruns_once_in_window_when_schema_old():
    """v5.374 — 형식 변경 뒤 첫 실행(창 안). 이미 schema 2면 다시 안 돈다. 창 밖이면 안 돈다."""
    now = datetime(2026, 11, 7, 18, 0, tzinfo=app.KST)
    base = {"target": "2026-11-06", "status": "ok", "attempts": 1, "basis": app.LOWPOINT_CALC_BASIS}
    assert app._lowpoint_due("week", now, {"week": base}) == "2026-11-06"
    assert app._lowpoint_due("week", now, {"week": {**base, "schema": 2}}) is None
    late = datetime(2026, 11, 9, 12, 0, tzinfo=app.KST)                  # 토 09:00 + 48h 지남
    assert app._lowpoint_due("week", late, {"week": base}) is None
    assert app._lowpoint_due("month", now, {"month": {**base, "target": "2026-10-31"}}) is None   # 월봉은 형식 변경과 무관


def test_floor_table_header_is_sticky_below_page_header():
    src = open(os.path.join(os.path.dirname(__file__), "static", "index.html"), encoding="utf-8").read()
    assert _fn("_lfTable").count("lf-table") == 1
    assert "table.lf-table thead th{position:sticky;top:var(--sticky-top,0px)" in src
    assert "'--sticky-top'" in _fn("syncStickyTop")


def _weekly_df(highs, closes=None, start="2025-11-07"):
    """주봉 고가 목록 → 주마다 월~금 5일 일봉(금요일 고가 = 그 주 고가)."""
    rows, idx = [], []
    for k, h in enumerate(highs):
        c = (closes[k] if closes else h * 0.95)
        for dd in range(5):
            day = pd.Timestamp(start) + pd.Timedelta(weeks=k) - pd.Timedelta(days=4 - dd)
            hi = h if dd == 4 else min(h, c) * 0.99
            rows.append((c, hi, min(c, hi) * 0.98, c, 100.0))
            idx.append(day)
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"], index=pd.DatetimeIndex(idx))


def test_downtrend_line_picks_gentlest_line_over_intermediate_highs_and_detects_break():
    """v5.377 자동 빗각: A = 최고 고점, B = 그 사이 고가를 위에서 감싸는 가장 완만한 피벗."""
    highs = [100, 120, 110, 105, 100, 98, 96, 104, 95, 92, 90, 94, 88, 86, 83, 79, 75, 71, 67, 63]
    d = _weekly_df(highs)
    hit = {"bar_date": str(d.index[-1].date())}
    dt = fl.downtrend_line(d, hit, str(d.index[-1].date()))
    assert dt["a_high"] == 120 and dt["b_high"] == 94                       # 120→94가 104(7주차)를 아래에 두는 가장 완만한 선
    assert dt["slope_week"] == round((94 - 120) / 10, 4)
    assert dt["broken_date"] is None
    closes = [h * 0.95 for h in highs[:-1]] + [100.0]                        # 마지막 주 종가가 선 위
    d2 = _weekly_df(highs[:-1] + [101], closes)
    dt2 = fl.downtrend_line(d2, hit, str(d2.index[-1].date()))
    assert dt2["broken_date"] == str(d2.index[-1].date())


def test_converge_info_weeks_and_near():
    dt = {"slope_week": -447.0, "line_now": 9474.0, "week_date": "2026-11-06", "broken_date": None}
    cv = fl.converge_info(dt, 8460.0, 8900.0, "holding")
    assert cv["weeks"] == round(1014 / 447, 2) and cv["meet_week"] == "2026-11-27" and cv["near"] is False
    cv2 = fl.converge_info({**dt, "line_now": 9000.0}, 8460.0, 8900.0, "spring")
    assert cv2["near"] is True and cv2["meet_week"] == "2026-11-20"
    assert fl.converge_info({**dt, "broken_date": "2026-11-06"}, 8460.0, 8900.0, "holding") is None
    assert fl.converge_info(dt, 8460.0, 8300.0, "holding") is None                   # 가격이 기준선 아래
    assert fl.converge_info(dt, 8460.0, 8900.0, "rebroken") is None


def test_converge_events_create_once_and_respect_deletions(monkeypatch, tmp_path):
    p = tmp_path / "user_events.json"
    log = tmp_path / "user_events_deletions.log"
    monkeypatch.setattr(app, "USER_EVENTS_PATH", str(p))
    monkeypatch.setattr(app, "USER_EVENTS_DELETE_LOG_PATH", str(log))
    rows = {"036890.KQ": {"status": "ok", "name": "진성티이씨", "mkt": "KR", "last_close": 8900.0,
                          "dtline": {"a_date": "2026-05-08", "b_date": "2026-08-14"},
                          "converge": {"near": True, "meet_week": "2026-11-20", "weeks": 1.2, "line_now": 9000.0,
                                       "slope_week": -447.0, "base": 8460.0}},
            "VTSI": {"status": "ok", "mkt": "US", "converge": {"near": False, "meet_week": "2027-01-01"}}}
    now = datetime(2026, 11, 8, 7, 0, tzinfo=app.KST)
    made = app._lp_converge_events(rows, now)
    assert [e["id"] for e in made] == ["ue_auto_conv_036890-KQ_20261120"]
    ev = json.load(open(p))
    assert ev[0]["date"] == "2026-11-20" and ev[0]["ticker"] == "036890.KQ" and ev[0]["source"] == "auto_converge"
    assert app._user_event_invalid(ev[0]) is None                          # 사용자가 화면에서 고쳐 저장해도 통과
    assert app._lp_converge_events(rows, now) == []                        # 같은 수렴은 한 번만
    p.write_text("[]")
    log.write_text(json.dumps({"id": "ue_auto_conv_036890-KQ_20261120"}) + "\n")
    assert app._lp_converge_events(rows, now) == []                        # 지운 일정은 다시 안 만든다


def test_added_endpoint_filters_by_source_and_since(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    p = tmp_path / "user_events.json"
    p.write_text(json.dumps([
        {"id": "ue_a", "date": "2026-11-20", "title": "x", "source": "auto_converge", "created_at": "2026-11-08T07:00:00+09:00"},
        {"id": "ue_b", "date": "2026-11-20", "title": "y", "source": "auto_converge", "created_at": "2026-11-01T07:00:00+09:00"},
        {"id": "ue_c", "date": "2026-11-20", "title": "z"}]))
    monkeypatch.setattr(app, "USER_EVENTS_PATH", str(p))
    assert "/api/user-events/added" in app._BOT_READ_EXACT_PATHS
    import asyncio as _a
    res = _a.run(app.user_events_added(since="2026-11-07T08:45:00+09:00"))
    body = json.loads(res.body)
    assert [e["id"] for e in body["events"]] == ["ue_a"]


def test_chip_shows_converge_weeks():
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    src = _fn("lfTestCount") + "\n" + _fn("lfChips")
    r = "{state:'holding',test_episodes:[],converge:{weeks:1.2,near:true,meet_week:'2026-11-20',line_now:9000,slope_week:-447,base:8460}}"
    p = subprocess.run(["node", "-e", src + f"\nconsole.log(JSON.stringify(lfChips({r}).map(c=>c.t+'/'+c.tone)))"],
                       capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    assert "빗각 1.2주/bad" in json.loads(p.stdout)
