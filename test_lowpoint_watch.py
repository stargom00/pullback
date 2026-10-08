"""v5.325 — 저점 관찰: 히트 종가(기준가) 대비 +5% 도달 추적.

사용자 지시: "주·월봉 저점 히트가 수십 개라 한번에 진입 불가. 히트 시점 종가를 기준가로 기록하고 이후 +5% 도달 여부를
자동 추적 — 도달한 종목은 활성 목록에서 빠지고, 미도달 종목만 진입 후보로 남는 관찰 페이지."

사보타주 확인(2026-10-05, FAIL 확인 후 원복):
① reach_info 비교를 > 로(정확히 1.05배 미도달) → test_reach_boundary_exact FAIL
② 기준일 당일 고가 포함(>=) → test_base_date_and_earlier_highs_ignored FAIL
③ fetch_daily가 유니버스 전수 조회(lp.kr_universe → fetch_kr) → test_daily_track_fetches_only_active_codes FAIL
④ watch_id에서 기준일 라벨을 뺌(코호트 합쳐짐) → test_cohorts_are_separate_records FAIL
⑤ _lowpoint_job_blocking의 관찰 등록 호출 제거 → test_scan_registers_hits_with_base_price FAIL
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.join(ROOT, "scripts", "screens"), os.path.join(ROOT, "scripts", "measurements")):
    sys.path.insert(0, p)
import harness  # noqa: E402
import lowpoint as lp  # noqa: E402
import lowpoint_eval as ev  # noqa: E402
import lowpoint_watch as w  # noqa: E402

import app  # noqa: E402

KST = timezone(timedelta(hours=9))
SRC = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()


def _k(s):
    return datetime.fromisoformat(s).replace(tzinfo=KST)


def _screen_result(market, rows):
    """screen_market()이 만드는 결과 모양(한글 키 행) — 프로덕션 경로(publish_entry)로 레코드를 만든다."""
    return {"market": market, "universe": 10, "fetched": 10, "failed": [], "stale": {}, "short": {}, "meta": {},
            "rows": [dict(zip(lp.COLS, r)) for r in rows]}


def _entry(label, rows_kr=(), rows_us=()):
    res = []
    if rows_kr:
        res.append(_screen_result("kospi", rows_kr))
    if rows_us:
        res.append(_screen_result("us", rows_us))
    labels = {r["market"]: pd.Timestamp(label) for r in res}
    return lp.publish_entry(res, "week", labels, {"run_at_kst": "x"})


KR_ROW = ["KOSPI", "002320.KS", "한진", "2026-10-02", 15560.0, 14280.0, 30.33, 29.42, 43.88]
US_ROW = ["US", "AVA", "Avista Corporation Common Stock", "2026-10-02", 35.31, 35.11, 33.22, 29.79, 31.13]


def _daily(rows):
    """[(날짜, 고가, 종가)] → 일봉 DataFrame."""
    idx = pd.to_datetime([r[0] for r in rows])
    return pd.DataFrame({"High": [r[1] for r in rows], "Close": [r[2] for r in rows]}, index=idx)


# ── 순수 계산 ─────────────────────────────────────────────────────
def test_reach_pct_reuses_existing_short_goal():
    assert w.REACH_PCT == ev.SHORT_GOAL_PCT == 5.0 and ev.SHORT_VP_UP_PCT == ev.SHORT_GOAL_PCT


def test_records_from_entry_base_price_is_scan_close():
    e = _entry("2026-10-02", rows_kr=[KR_ROW], rows_us=[US_ROW])
    recs = {r["code"]: r for r in w.records_from_entry(e, "week")}
    assert recs["002320.KS"]["base_price"] == 15560.0 and recs["AVA"]["base_price"] == 35.31   # 기준가 = close0
    assert recs["002320.KS"]["base_date"] == "2026-10-02" and recs["002320.KS"]["label"] == "2026-10-02"
    assert (recs["002320.KS"]["mkt"], recs["AVA"]["mkt"]) == ("KR", "US")
    assert all(r["status"] == "active" and r["tf"] == "week" for r in recs.values())


@pytest.mark.parametrize("base", [1000.0, 15560.0, 1662.0, 0.29, 3.13, 121.44, 35.31])
def test_reach_boundary_exact(base):
    thr = base * 1.05
    d = _daily([("2026-10-05", thr * 0.999, base), ("2026-10-06", round(thr, 10), base)])
    info = w.reach_info(base, "2026-10-02", d)
    assert info["reached"] and info["reached_date"] == "2026-10-06" and info["reached_days"] == 4
    below = w.reach_info(base, "2026-10-02", _daily([("2026-10-05", thr * 0.9999, base)]))
    assert not below["reached"]


def test_base_date_and_earlier_highs_ignored():
    d = _daily([("2026-09-30", 2000, 1000), ("2026-10-02", 2000, 1000), ("2026-10-05", 1040, 1030)])
    info = w.reach_info(1000.0, "2026-10-02", d)
    assert not info["reached"] and info["last_close"] == 1030 and info["last_date"] == "2026-10-05"


def test_first_reach_day_counts_calendar_days():
    d = _daily([("2026-10-05", 1010, 1005), ("2026-10-08", 1060, 1040), ("2026-10-12", 1100, 1090)])
    info = w.reach_info(1000.0, "2026-10-02", d)
    assert (info["reached_date"], info["reached_days"]) == ("2026-10-08", 6)


def test_cohorts_are_separate_records():
    """같은 종목이 다음 스캔에 또 나오면 기준일별 별도 레코드 — 각자의 기준가로 판정."""
    a = w.records_from_entry(_entry("2026-10-02", rows_kr=[KR_ROW]), "week")
    b = w.records_from_entry(_entry("2026-10-09", rows_kr=[[*KR_ROW[:3], "2026-10-09", 14000.0, *KR_ROW[5:]]]), "week")
    assert len({r["id"] for r in a + b}) == 2
    d = _daily([("2026-10-12", 14800, 14700), ("2026-10-13", 14900, 14850)])
    ra = w.reach_info(a[0]["base_price"], a[0]["base_date"], d)
    rb = w.reach_info(b[0]["base_price"], b[0]["base_date"], d)
    assert not ra["reached"] and rb["reached"]       # 14000×1.05 = 14700 ≤ 14800, 15560×1.05 > 14900


def test_daily_track_fetches_only_active_codes(monkeypatch):
    """관찰 중(active) 종목만 조회 — 도달 종목·유니버스 전수 조회 없음."""
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("유니버스 전수 조회"))
    for name in ("kr_universe", "us_universe", "fetch_kr", "fetch_us"):
        monkeypatch.setattr(lp, name, boom)
    asked = {"kr": [], "us": []}
    import naver_kr

    def nv(code, days=730):
        asked["kr"].append((code, days))
        return _daily([("2026-10-05", 16400, 16300)]).assign(Open=1, Low=1, Volume=1)

    def yf(tickers, period="2y", auto_adjust=True):
        asked["us"].append((tuple(tickers), period, auto_adjust))
        return {t: _daily([("2026-10-05", 36, 35.9)]) for t in tickers}
    monkeypatch.setattr(naver_kr, "fetch_history", nv)
    monkeypatch.setattr(harness, "_fetch_us_batch", yf)
    recs = w.records_from_entry(_entry("2026-10-02", rows_kr=[KR_ROW], rows_us=[US_ROW]), "week")
    recs.append({**recs[0], "id": "w_done", "code": "005930.KS", "status": "reached"})     # 도달 — 다시 안 본다
    recs[-1]["reach_rule"] = w.REACH_RULE                          # 정규장 규칙으로 이미 도달 — 다시 안 본다
    recs[-1]["stage"] = "invalid"                                  # v5.337: 종료된 출발은 다시 안 본다(v5.342부터 재출발은 이후 결과 때문에 조회)
    minutes = []
    fm = lambda code, day: minutes.append((code, str(day))) or [{"localDateTime": day.strftime("%Y%m%d") + "100000", "highPrice": 16400.0}]
    updates, summary = w.track(recs, date(2026, 10, 5), "2026-10-05T20:10:00+09:00", fetch_min=fm)   # v5.339: KR 10-05 확정 후
    # v5.343: 유형(ABC A — MA600 600봉)용으로 ABC 탭과 같은 KR 창(naver_kr.KR_SCAN_DAYS) + 휴장 여유 10일. US는 같은 일수를 덮는 기간
    import naver_kr
    assert asked["kr"] == [("002320.KS", naver_kr.KR_SCAN_DAYS + 10)] and w.shape_lookback_days() == 70
    assert asked["us"] == [(("AVA",), "5y", lp.US_AUTO_ADJUST)]
    assert minutes == [("002320.KS", "2026-10-05")]                # 분봉도 관찰 종목·필요한 거래일만
    assert set(updates) == {r["id"] for r in recs if r["status"] == "active"}
    assert updates[recs[0]["id"]]["status"] == "reached"          # 정규장 16400 ≥ 15560×1.05 = 16338
    assert "status" not in updates[recs[1]["id"]]                  # 36 < 35.31×1.05 = 37.08
    assert summary["active"] == 2 and summary["reached_new"] == 1


# ── 서버 저장·등록·러너 ───────────────────────────────────────────
@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "LOWPOINT_DATA_PATH", str(tmp_path / "lowpoint_latest.json"))
    monkeypatch.setattr(app, "LOWPOINT_LATEST_PATH", str(tmp_path / "repo_lowpoint.json"))
    monkeypatch.setattr(app, "LOWPOINT_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(app, "LOWPOINT_US_LISTINGS_PATH", str(tmp_path / "us_listings.json"))
    return tmp_path


def _load():
    return app._rec_list_load(app.LP_WATCH_PATH)


class _Req:
    headers = {"user-agent": "pytest"}
    client = type("C", (), {"host": "127.0.0.1"})()


def test_scan_registers_hits_with_base_price(store, monkeypatch):
    """스캔 경로 그대로(_lowpoint_job_blocking → publish_entry → write_publish) 뒤 관찰 등록."""
    e_res = [_screen_result("kospi", [KR_ROW]), _screen_result("us", [US_ROW])]
    monkeypatch.setattr(lp, "screen_all", lambda tf, now, **k: (e_res, {"kospi": pd.Timestamp("2026-10-02"),
                                                                        "us": pd.Timestamp("2026-10-02")}))
    summary = app._lowpoint_job_blocking("week", _k("2026-10-03 09:00"))
    recs = {r["code"]: r for r in _load()}
    assert summary["watch_added"] == 2 and set(recs) == {"002320.KS", "AVA"}
    assert recs["002320.KS"]["base_price"] == 15560.0 and recs["002320.KS"]["rev"] == 1
    # 다시 돌아도(가격 기준 바뀐 재실행 등) 같은 id는 그대로 — 중복 없음
    assert app._lowpoint_job_blocking("week", _k("2026-10-03 13:00"))["watch_added"] == 0 and len(_load()) == 2


def test_seed_from_latest_and_deleted_not_resurrected(store):
    (store / "lowpoint_latest.json").write_text(json.dumps({"week": _entry("2026-10-02", rows_kr=[KR_ROW]),
                                                            "month": _entry("2026-09-30", rows_us=[US_ROW])}))
    assert app._lp_watch_seed_from_latest() == {"week": 1, "month": 1}
    rid = next(r["id"] for r in _load() if r["code"] == "AVA")
    assert json.loads(asyncio.run(app.lp_watch_delete(rid, _Req())).body)["deleted"] == rid
    assert app._lp_watch_seed_from_latest() == {"week": 0, "month": 0}           # 삭제한 건 다시 안 들어온다
    assert [r["code"] for r in _load()] == ["002320.KS"]
    log = (store / "lowpoint_watch_deletions.log").read_text(encoding="utf-8").splitlines()
    assert json.loads(log[0])["record"]["code"] == "AVA"


def test_job_updates_only_active_with_rev_and_persists(store, monkeypatch):
    (store / "lowpoint_latest.json").write_text(json.dumps({"week": _entry("2026-10-02", rows_kr=[KR_ROW], rows_us=[US_ROW])}))
    seen = []

    def fake_fetch(records, today):
        seen.append(sorted(r["code"] for r in records))
        return {"002320.KS": _daily([("2026-10-06", 16400, 16200)]), "AVA": _daily([("2026-10-06", 36, 35.5)])}
    monkeypatch.setattr(w, "fetch_daily", fake_fetch)
    fake_min = lambda code, day: [{"localDateTime": day.strftime("%Y%m%d") + "100000", "highPrice": 16400.0}]
    monkeypatch.setattr(w.track, "__defaults__", (fake_fetch, fake_min))
    s = app._lp_watch_job_blocking("watch", _k("2026-10-07 07:00"))
    assert s["counts"]["reached_new"] == 1 and s["counts"]["seeded"] == {"week": 2, "month": 0}
    recs = {r["code"]: r for r in _load()}
    hj, ava = recs["002320.KS"], recs["AVA"]
    assert (hj["status"], hj["reached_date"], hj["reached_days"], hj["rev"]) == ("reached", "2026-10-06", 4, 2)
    assert (ava["status"], ava["last_close"], ava["rev"]) == ("active", 35.5, 2)
    asked = []
    monkeypatch.setattr(w.track, "__defaults__", (fake_fetch, lambda c, d: asked.append(c) or fake_min(c, d)))
    app._lp_watch_job_blocking("watch", _k("2026-10-08 07:00"))
    # v5.337: 출발(도달) 종목은 단계(숨고르기·재출발·무효) 판정용 일봉만 다시 조회 — 도달 판정(분봉)은 다시 안 한다
    assert seen[-1] == ["002320.KS", "AVA"] and asked == []
    hj2 = _load()[[r["code"] for r in _load()].index("002320.KS")]
    assert (hj2["status"], hj2["reached_date"], hj2["rev"]) == ("reached", "2026-10-06", 3)


def test_runner_daily_slot_at_0700():
    assert app.LOWPOINT_SCHEDULE_HM["watch"] == (7, 0) and "watch" in app.LOWPOINT_JOBS
    assert app._lowpoint_due("watch", _k("2026-10-07 07:00"), {}) == "2026-10-07"
    assert app._lowpoint_due("watch", _k("2026-10-07 06:59"), {}) == "2026-10-06"
    st = {"watch": {"target": "2026-10-07", "status": "ok", "attempts": 1}}
    assert app._lowpoint_due("watch", _k("2026-10-07 20:00"), st) is None             # 하루 1회
    fail = {"watch": {"target": "2026-10-07", "status": "failed", "attempts": 1, "started_at": "2026-10-07T07:00:00+09:00"}}
    assert app._lowpoint_due("watch", _k("2026-10-07 08:01"), fail) == "2026-10-07"   # 기존 재시도 규칙(60분)
    assert app._lowpoint_due("watch", _k("2026-10-07 10:00"), fail) is None           # 기존 KR 장중 차단


def test_runner_picks_watch_after_scans(store, monkeypatch):
    done = {"target": None, "status": "ok", "attempts": 1, "basis": app.LOWPOINT_CALC_BASIS}
    st = {"week": {**done, "target": "2026-10-02"}, "month": {**done, "target": "2026-09-30"},
          "newlisting": {**done, "target": "2026-09-30"}}
    (store / "state.json").write_text(json.dumps(st))
    ran = []
    monkeypatch.setattr(app, "_lowpoint_running", False)
    rec = asyncio.run(app._maybe_run_lowpoint(_k("2026-10-05 07:02"),
                                              _job=lambda tf, now: ran.append(tf) or {"bar_date": "x", "rows": 0}))
    assert ran == ["watch"] and rec["target"] == "2026-10-05" and rec["status"] == "ok"
    assert app._lowpoint_due("watch", _k("2026-10-05 07:06"), app._lowpoint_load_state()) is None


def test_job_dispatch_and_backup_wiring():
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert '"watch": _lp_watch_job_blocking' in src
    assert src.count('_daily_backup(LP_WATCH_PATH, "lowpoint_watch"') == 2               # 스케줄러 1 + 시작 직후 1
    assert src.count("_lp_watch_seed_from_latest()") >= 2                                 # 시작 직후 + 추적 작업
    assert 'LP_WATCH_PATH = _resolve_persistent_path("lowpoint_watch.json")' in src


def test_refresh_busy_is_rejected(monkeypatch):
    monkeypatch.setattr(app, "_lowpoint_running", True)
    r = asyncio.run(app.lp_watch_refresh())
    assert r.status_code == 409


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


def _js(expr):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    p = subprocess.run(["node", "-e", _fn("lpwGroups") + "\n" + _fn("lpwDays") + f"\nconsole.log(JSON.stringify({expr}));"],
                       capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


RECS = [
    {"id": "a", "tf": "week", "label": "2026-10-02", "code": "B", "base_price": 100, "last_close": 98, "status": "active"},
    {"id": "b", "tf": "week", "label": "2026-10-02", "code": "A", "base_price": 100, "last_close": 90, "status": "active"},
    {"id": "c", "tf": "week", "label": "2026-10-02", "code": "C", "base_price": 100, "status": "reached", "reached_date": "2026-10-06"},
    {"id": "d", "tf": "month", "label": "2026-09-30", "code": "A", "base_price": 80, "last_close": 81, "status": "active"},
    {"id": "e", "tf": "week", "label": "2026-10-09", "code": "A", "base_price": 95, "last_close": None, "status": "active"},
    {"id": "f", "tf": "month", "label": "2026-10-02", "code": "Z", "base_price": 1, "last_close": 1, "status": "active"},
]


def test_groups_cohorts_sections_and_order():
    g = _js(f"lpwGroups({json.dumps(RECS)}).map(g => [g.tf, g.label, g.active.map(r => r.id), g.reached.map(r => r.id)])")
    assert g == [["week", "2026-10-09", ["e"], []],
                 ["week", "2026-10-02", ["b", "a"], ["c"]],                  # 기준가 대비 낮은 순 · 도달은 아래 섹션
                 ["month", "2026-10-02", ["f"], []],
                 ["month", "2026-09-30", ["d"], []]]                        # 같은 종목 A가 세 코호트에 따로


def test_days_calendar():
    assert _js("[lpwDays('2026-10-02','2026-10-06'), lpwDays('2026-09-30','2026-10-05'), lpwDays(null,'2026-10-05')]") == [4, 5, None]


def test_ui_wiring():
    track = _fn("renderLowpointTrack")
    assert "LPT_PAGES.map(" in track and "['watch', '관찰']" in SRC.split("const LPT_PAGES = ")[1].split("\n")[0] and "renderLowpointWatch()" in track
    assert "lpwLoad().then(renderLowpointTrack)" in _fn("lpSetView")
    rec = _fn("lpwRecord")
    assert "_lpt.prefill = { q: r.code };" in rec and "_lpt.view = 'trades';" in rec
    assert "_lpt.prefill ? _escapeHtml(_lpt.prefill.q)" in track and "_lpt.prefill = null;" in _fn("lptAdd")
    body = _fn("renderLowpointWatch")
    for s in ("<details", "종료 · 재출발", "숨고르기 · ${rest.length}", "lpwStageSplit(_lpw.recs)", "lpwRecord(", "lpwDelete(", "tvUrl(r.code, r.mkt)",
              "lpwDays(r.base_date, today)", "lpReturnPct(r.last_close, Number(r.base_price))", "lpwRefresh()"):
        assert s in body, s
    assert "fetch('/api/lowpoint/watch/refresh', { method: 'POST' })" in _fn("lpwRefresh")
