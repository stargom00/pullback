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
    p = subprocess.run(["node", "-e", _fn("lfFilter") + "\n" + _fn("lfSort") + f"\nconsole.log(JSON.stringify({expr}));"],
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
    assert _js(f"lfSort(lfFilter({ROWS},'all'),'asc').map(r=>r.code)") == ["b", "d", "a", "g", "c", "e"]
    assert _js(f"lfSort(lfFilter({ROWS},'all'),'desc').map(r=>r.code)") == ["c", "g", "a", "d", "b", "e"]


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
