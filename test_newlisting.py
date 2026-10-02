"""v5.314 — 신규상장 스크린(상장 13~20개월차) · 서버 러너 · 탭.

개월수 정의(newlisting.py docstring): months = 기준월 − 상장월(월 산술, 기준일=월말 → 상장일의
일(day)은 무관), 13 ≤ months ≤ 20 포함.

사보타주 확인(2026-10-02, FAIL 확인 후 원복):
① in_window의 경계 제거(항상 True) → 8건 FAIL(test_window_boundaries 12·21개월 4건,
   test_window_values_are_user_given, test_select_boundaries_and_order, build 2건)
② _lowpoint_due에서 신규상장의 "월봉 끝난 뒤" 조건 제거 → test_newlisting_waits_for_month 4건 FAIL
③ (하락률 추가 지시) 종가·첫 봉 조회를 결과 종목이 아니라 전체 목록으로(전수 조회) →
   test_first_close_fetch_only_for_result_rows FAIL
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "scripts", "screens"))
import newlisting as nl  # noqa: E402

import app  # noqa: E402

KST = timezone(timedelta(hours=9))
REF = date(2026, 9, 30)


def _k(s):
    return datetime.fromisoformat(s).replace(tzinfo=KST)


# ── 개월수 정의·경계 ───────────────────────────────────────────────
@pytest.mark.parametrize("listed,months,inside", [
    ("2025-09-01", 12, False),   # 12개월 → 제외
    ("2025-09-30", 12, False),
    ("2025-08-01", 13, True),    # 정확히 13 → 포함(월초)
    ("2025-08-31", 13, True),    # 같은 달 말일도 13(일 무관)
    ("2025-01-01", 20, True),    # 정확히 20 → 포함
    ("2025-01-31", 20, True),
    ("2024-12-31", 21, False),   # 21 → 제외
    ("2024-12-01", 21, False),
])
def test_window_boundaries(listed, months, inside):
    m = nl.months_since(date.fromisoformat(listed), REF)
    assert m == months
    assert nl.in_window(m) is inside


def test_window_values_are_user_given():
    assert (nl.MONTHS_MIN, nl.MONTHS_MAX) == (13, 20)
    assert [m for m in range(0, 30) if nl.in_window(m)] == list(range(13, 21))


def test_months_across_year_and_february():
    assert nl.months_since(date(2025, 2, 28), date(2026, 2, 28)) == 12
    assert nl.months_since(date(2024, 12, 15), date(2026, 8, 31)) == 20
    assert nl.month_end(date(2026, 2, 3)) == date(2026, 2, 28)
    assert nl.month_end(date(2026, 12, 9)) == date(2026, 12, 31)


def test_select_boundaries_and_order():
    rows = [{"market": "KOSDAQ", "code": f"{i:06d}.KQ", "name": n, "listed": d}
            for i, (n, d) in enumerate([("a12", "2025-09-15"), ("b13", "2025-08-20"), ("c20", "2025-01-02"),
                                        ("d21", "2024-12-30"), ("e15", "2025-06-05"), ("f13b", "2025-08-01")])]
    got = nl.select(rows, REF)
    assert [r["name"] for r in got] == ["f13b", "b13", "e15", "c20"]   # 개월수 오름차순(같으면 상장일 순)
    assert [r["months"] for r in got] == [13, 13, 15, 20]


def test_build_rejects_non_month_end():
    with pytest.raises(ValueError):
        nl.build(date(2026, 9, 29), check_us_ready=False)


# ── 파싱 ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("v,want", [("2025-03-14", date(2025, 3, 14)), (" 2024-11-07 ", date(2024, 11, 7)),
                                    ("", None), (None, None), (float("nan"), None), ("2025/03/14", None)])
def test_parse_kr_date(v, want):
    assert nl.parse_kr_date(v) == want


def test_us_first_trade_epoch_to_months():
    d = nl.first_trade_date_from_epoch(1749130200)   # CRCL — yahoo firstTradeDate(실측)
    assert d == date(2025, 6, 5)
    assert nl.months_since(d, REF) == 15
    # 저녁 ET 시각이 UTC로 다음날이어도 ET 날짜로 본다
    assert nl.first_trade_date_from_epoch(int(datetime(2025, 1, 2, 2, 0, tzinfo=timezone.utc).timestamp())) == date(2025, 1, 1)
    assert nl.first_trade_date_from_epoch("x") is None


def test_us_lookup_uses_cache_and_separates_not_found_from_rate_limit():
    asked = []

    def lookup(s):
        asked.append(s)
        return {"NEW": date(2025, 5, 1), "GONE": nl.NOT_FOUND, "BUSY": nl.RATE_LIMITED}[s]
    dates, not_found, limited, n = nl.us_first_trade_dates(["OLD", "NEW", "GONE", "BUSY"], {"OLD": "1999-01-04"},
                                                           lookup=lookup, concurrency=2)
    assert sorted(asked) == ["BUSY", "GONE", "NEW"] and n == 3
    assert dates == {"OLD": "1999-01-04", "NEW": "2025-05-01"}
    assert not_found == ["GONE"] and limited == ["BUSY"]


@pytest.mark.parametrize("behavior,want", [
    ({"firstTradeDate": 1749130200}, date(2025, 6, 5)),
    ({}, nl.NOT_FOUND),                       # firstTradeDate 없음
    ("ratelimit", nl.RATE_LIMITED),           # YFRateLimitError(429) — 일시적
    ("curl", nl.RATE_LIMITED),                # 전송 계층 오류 — 일시적
    ("keyerror", nl.NOT_FOUND),               # 없는 심볼(실측: ZZZZQ → KeyError)
])
def test_yahoo_first_trade_classifies_responses(monkeypatch, behavior, want):
    import yfinance as yf
    from yfinance.exceptions import YFRateLimitError
    from curl_cffi.requests.exceptions import RequestException as CurlError

    class T:
        def __init__(self, sym):
            assert sym == "CRCL"

        def get_history_metadata(self):
            if behavior == "ratelimit":
                raise YFRateLimitError()
            if behavior == "curl":
                raise CurlError("conn reset")
            if behavior == "keyerror":
                raise KeyError("exchangeTimezoneName")
            return behavior
    monkeypatch.setattr(yf, "Ticker", T)
    assert nl._yahoo_first_trade("CRCL") == want


def test_first_trade_cache_priority(tmp_path):
    a, b = tmp_path / "data.json", tmp_path / "seed.json"
    a.write_text(json.dumps({"dates": {"X": "2025-01-01"}}))
    b.write_text(json.dumps({"dates": {"X": "2020-01-01", "Y": "2021-01-01"}}))
    assert nl.load_first_trade_cache([str(a), str(b)]) == {"X": "2025-01-01", "Y": "2021-01-01"}


# ── build(원천은 가짜로) ───────────────────────────────────────────
@pytest.fixture
def fake_sources(monkeypatch):
    monkeypatch.setattr(nl, "kr_listings", lambda: ([
        {"market": "KOSPI", "code": "111111.KS", "name": "K13", "listed": "2025-08-14"},
        {"market": "KOSDAQ", "code": "222222.KQ", "name": "K12", "listed": "2025-09-02"},
        {"market": "KOSDAQ", "code": "333333.KQ", "name": "K20", "listed": "2025-01-21"},
    ], {"total": 3, "date_unparsed": []}))
    monkeypatch.setattr(nl.lp, "us_universe", lambda path=None, refresh=False: (
        {"CRCL": "Circle", "AAPL": "Apple", "OLDX": "Old"}, {}))
    monkeypatch.setattr(nl, "_attach_closes", lambda rows, ref: [r.update(close=1.0, close_date="2026-10-01") for r in rows])
    import harness
    monkeypatch.setattr(harness, "run_stamp", lambda: {"run_at_kst": "test"})


def test_build_end_to_end_with_fakes(fake_sources, tmp_path):
    cache = tmp_path / "c.json"
    lookup = {"CRCL": date(2025, 6, 5), "AAPL": date(1980, 12, 12), "OLDX": date(2024, 12, 31)}.get
    e = nl.build(REF, cache_paths=[str(cache)], cache_write_path=str(cache), us_lookup=lookup, check_us_ready=False)
    assert [(r["code"], r["months"]) for r in e["rows"]] == [("111111.KS", 13), ("CRCL", 15), ("333333.KQ", 20)]
    assert e["ref_date"] == "2026-09-30" and e["months_window"] == [13, 20]
    assert "근사치" in e["us_date_caveat"] and "이전상장" in e["kr_date_caveat"]
    assert e["counts"]["kr"]["hits"] == 2 and e["counts"]["us"]["hits"] == 1
    assert json.loads(cache.read_text())["dates"]["CRCL"] == "2025-06-05"   # 다음 달엔 묻지 않는다


def test_build_fails_loudly_when_us_source_is_empty(fake_sources):
    with pytest.raises(nl.NoData):
        nl.build(REF, us_lookup=lambda s: nl.NOT_FOUND, check_us_ready=False)


def test_rate_limit_fails_the_run_but_keeps_progress(fake_sources, tmp_path):
    """429가 하나라도 있으면 실패(조용히 '없음'으로 섞지 않는다) — 받은 것은 캐시에 남아
    다음 시도(기존 재시도 규칙)는 나머지만 묻는다."""
    cache = tmp_path / "c.json"
    first = {"CRCL": date(2025, 6, 5), "AAPL": nl.RATE_LIMITED, "OLDX": nl.RATE_LIMITED}.get
    with pytest.raises(nl.RateLimited) as ei:
        nl.build(REF, cache_paths=[str(cache)], cache_write_path=str(cache), us_lookup=first, check_us_ready=False)
    assert "2건" in str(ei.value)
    assert json.loads(cache.read_text())["dates"] == {"CRCL": "2025-06-05"}
    asked = []

    def second(s):
        asked.append(s)
        return {"AAPL": date(1980, 12, 12), "OLDX": date(2024, 12, 31)}[s]
    e = nl.build(REF, cache_paths=[str(cache)], cache_write_path=str(cache), us_lookup=second, check_us_ready=False)
    assert sorted(asked) == ["AAPL", "OLDX"] and e["counts"]["us"]["hits"] == 1


def test_build_runs_us_data_check_with_injected_calendar(fake_sources, monkeypatch):
    seen = {}

    def chk(tf, label, itd):
        seen.update(tf=tf, label=label, itd=itd)
        raise nl.lp.DataNotReady("미도착")
    monkeypatch.setattr(nl.lp, "check_us_data_ready", chk)
    marker = object()
    with pytest.raises(nl.lp.DataNotReady):
        nl.build(REF, is_trading_day=marker, us_lookup=lambda s: None)
    assert seen == {"tf": "month", "label": REF, "itd": marker}


def test_kr_parse_reuses_lowpoint_kind_definitions():
    src = open(nl.__file__, encoding="utf-8").read()
    assert "lp.KIND_CORPLIST_URL" in src and "lp.KR_BOARDS" in src and "lp._read_kind_table" in src
    assert "kind.krx.co.kr" not in src, "KIND URL 사본"
    assert src.count("lp.FETCH_CONCURRENCY") == 1


# ── /data 우선 · 레포 폴백 ──────────────────────────────────────────
@pytest.fixture
def nl_paths(tmp_path, monkeypatch):
    p = {"data": tmp_path / "data_nl.json", "repo": tmp_path / "repo_nl.json", "state": tmp_path / "state.json"}
    monkeypatch.setattr(app, "NEWLISTING_DATA_PATH", str(p["data"]))
    monkeypatch.setattr(app, "NEWLISTING_LATEST_PATH", str(p["repo"]))
    monkeypatch.setattr(app, "LOWPOINT_STATE_PATH", str(p["state"]))
    return p


def test_view_prefers_data_then_repo(nl_paths):
    assert app._newlisting_view()["missing"] is True
    nl_paths["repo"].write_text(json.dumps({"ref_date": "2026-09-30", "rows": [{"code": "R"}]}))
    v = app._newlisting_view()
    assert v["source"] == "repo" and v["rows"] == [{"code": "R"}]
    nl_paths["data"].write_text(json.dumps({"ref_date": "2026-10-31", "rows": [{"code": "D"}]}))
    v = app._newlisting_view()
    assert v["source"] == "data" and v["ref_date"] == "2026-10-31"


def test_view_flags_failed_server_run(nl_paths):
    nl_paths["repo"].write_text(json.dumps({"ref_date": "2026-09-30", "rows": []}))
    nl_paths["state"].write_text(json.dumps({"newlisting": {"target": "2026-10-31", "status": "failed",
                                                            "error": "RuntimeError: x"}}))
    v = app._newlisting_view()
    assert v["refresh_failed"]["target"] == "2026-10-31" and v["source"] == "repo"


# ── 서버 러너: 저점 월봉 뒤 순차 ─────────────────────────────────────
def test_newlisting_uses_month_slot():
    assert app._lowpoint_last_slot("newlisting", _k("2026-11-01 08:00")) == app._lowpoint_last_slot("month", _k("2026-11-01 08:00"))
    assert app.LOWPOINT_JOBS == ("week", "month", "newlisting")


@pytest.mark.parametrize("month_state,due", [
    (None, None),                                                                         # 월봉 아직
    ({"target": "2026-10-31", "status": "running", "attempts": 1}, None),                # 월봉 실행 중
    ({"target": "2026-10-31", "status": "failed", "attempts": 1}, None),                 # 월봉 재시도 남음
    ({"target": "2026-09-30", "status": "ok", "attempts": 1}, None),                     # 지난달 월봉만 끝남
    ({"target": "2026-10-31", "status": "ok", "attempts": 1}, "2026-10-31"),             # 월봉 성공 → 이어서
    ({"target": "2026-10-31", "status": "failed", "attempts": 3}, "2026-10-31"),         # 월봉 시도 소진 → 이어서
])
def test_newlisting_waits_for_month(month_state, due):
    state = {"month": month_state} if month_state else {}
    assert app._lowpoint_due("newlisting", _k("2026-11-01 08:10"), state) == due


def test_newlisting_inherits_window_retry_and_kr_hours_block():
    ok_month = {"month": {"target": "2026-10-31", "status": "ok", "attempts": 1}}
    assert app._lowpoint_due("newlisting", _k("2026-11-03 08:01"), ok_month) is None    # 48시간 창 밖
    failed = {**ok_month, "newlisting": {"target": "2026-10-31", "status": "failed", "attempts": 1,
                                         "started_at": _k("2026-11-02 08:10").isoformat()}}
    assert app._lowpoint_due("newlisting", _k("2026-11-02 08:50"), failed) is None       # 60분 안 됨
    assert app._lowpoint_due("newlisting", _k("2026-11-02 09:30"), failed) is None       # KR 장중(월요일) 차단
    assert app._lowpoint_due("newlisting", _k("2026-11-02 15:40"), failed) == "2026-10-31"
    done = {**ok_month, "newlisting": {"target": "2026-10-31", "status": "ok", "attempts": 1}}
    assert app._lowpoint_due("newlisting", _k("2026-11-01 09:00"), done) is None


WEEK_DONE = {"week": {"target": "2026-10-30", "status": "ok", "attempts": 1}}   # 10-31(토) 주봉은 끝난 상태


@pytest.fixture
def runner_paths(tmp_path, monkeypatch):
    """11-01(일) 08:00은 10-31(토) 주봉의 따라잡기 창 안이다 — 주봉 완료 상태로 시작하고,
    진짜 작업(네트워크)은 불리면 바로 실패하게 막는다(테스트가 원천을 때리지 않게)."""
    monkeypatch.setattr(app, "LOWPOINT_STATE_PATH", str(tmp_path / "state.json"))
    (tmp_path / "state.json").write_text(json.dumps(WEEK_DONE))

    def real_job_forbidden(tf, now):
        raise AssertionError(f"테스트에서 진짜 작업 호출: {tf}")
    monkeypatch.setattr(app, "_lowpoint_job_blocking", real_job_forbidden)
    monkeypatch.setattr(app, "_newlisting_job_blocking", real_job_forbidden)
    return tmp_path


def test_runner_runs_month_then_newlisting_sequentially(runner_paths, monkeypatch):
    """스케줄러 경로 그대로(4분 틱). 각 틱은 작업 하나만 — 같은 틱에 둘이 돌지 않는다."""
    calls = []

    def month_job(tf, now):
        calls.append(("lowpoint", tf, app._lowpoint_running))
        return {"bar_date": "2026-10-31", "rows": 0, "counts": {}}

    def nl_job(tf, now):
        calls.append(("newlisting", tf, app._lowpoint_running))
        return {"bar_date": "2026-10-31", "rows": 3, "counts": {}}
    monkeypatch.setattr(app, "_lowpoint_job_blocking", month_job)
    monkeypatch.setattr(app, "_newlisting_job_blocking", nl_job)
    r1 = asyncio.run(app._maybe_run_lowpoint(_k("2026-11-01 08:00")))
    assert r1["target"] == "2026-10-31" and calls == [("lowpoint", "month", True)]
    r2 = asyncio.run(app._maybe_run_lowpoint(_k("2026-11-01 08:04")))
    assert r2["status"] == "ok" and calls[-1] == ("newlisting", "newlisting", True)
    assert asyncio.run(app._maybe_run_lowpoint(_k("2026-11-01 08:08"))) is None
    st = json.loads((runner_paths / "state.json").read_text())
    assert st["newlisting"]["status"] == "ok" and st["month"]["status"] == "ok"


def test_runner_never_overlaps(runner_paths, monkeypatch):
    monkeypatch.setattr(app, "_lowpoint_running", True)
    (runner_paths / "state.json").write_text(json.dumps({**WEEK_DONE, "month": {"target": "2026-10-31", "status": "ok", "attempts": 1}}))
    assert asyncio.run(app._maybe_run_lowpoint(_k("2026-11-01 08:04"), _job=lambda tf, now: 1 / 0)) is None


def test_runner_records_data_not_ready_for_newlisting(runner_paths, monkeypatch):
    (runner_paths / "state.json").write_text(json.dumps({**WEEK_DONE, "month": {"target": "2026-10-31", "status": "ok", "attempts": 1}}))

    def nl_job(tf, now):
        import lowpoint as lp
        raise lp.DataNotReady("US 일봉 미도착")
    monkeypatch.setattr(app, "_newlisting_job_blocking", nl_job)
    rec = asyncio.run(app._maybe_run_lowpoint(_k("2026-11-01 08:04")))
    assert rec["status"] == "data_not_ready"


def test_server_job_wires_paths(monkeypatch, tmp_path):
    seen = {}

    def build(ref, **kw):
        seen.update(ref=ref, **kw)
        return {"ref_date": ref.isoformat(), "rows": [], "counts": {}}
    monkeypatch.setattr(nl, "build", build)
    monkeypatch.setattr(app, "NEWLISTING_DATA_PATH", str(tmp_path / "out.json"))
    s = app._newlisting_job_blocking("newlisting", _k("2026-11-01 08:04"))
    assert s["bar_date"] == "2026-10-31" and seen["ref"] == date(2026, 10, 31)
    assert seen["cache_paths"] == [app.US_FIRST_TRADE_PATH, app.US_FIRST_TRADE_SEED_PATH]
    assert seen["cache_write_path"] == app.US_FIRST_TRADE_PATH and seen["is_trading_day"] is app.is_trading_day
    assert json.loads((tmp_path / "out.json").read_text())["ref_date"] == "2026-10-31"


# ── 탭(프론트) ─────────────────────────────────────────────────────
SRC = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()


def _fn(name):
    start = SRC.index(f"function {name}(")
    i = SRC.index("{", SRC.index(")", start))
    d = 0
    for j in range(i, len(SRC)):
        d += {"{": 1, "}": -1}.get(SRC[j], 0)
        if d == 0:
            return SRC[start:j + 1]
    raise AssertionError(name)


def test_split_rows_sorted_by_months():
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    rows = [{"market": "US", "code": "B", "months": 20, "listed": "2025-01-05"},
            {"market": "KOSDAQ", "code": "2.KQ", "months": 15, "listed": "2025-06-01"},
            {"market": "US", "code": "A", "months": 13, "listed": "2025-08-02"},
            {"market": "KOSPI", "code": "1.KS", "months": 13, "listed": "2025-08-20"},
            {"market": "KOSDAQ", "code": "3.KQ", "months": 13, "listed": "2025-08-01"}]
    js = _fn("nlSplitRows") + f"\nconsole.log(JSON.stringify(nlSplitRows({json.dumps(rows)})));"
    p = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert [r["code"] for r in out["KR"]] == ["1.KS", "3.KQ", "2.KQ"] and [r["code"] for r in out["US"]] == ["A", "B"]


def test_tab_wiring_and_shared_tv_link():
    assert 'data-mode="newlisting"' in SRC and 'id="newlistingView"' in SRC
    assert "else if (isNewlisting) { onEnterNewlistingTab(); }" in SRC
    tbl = _fn("_nlTable")
    assert "tvUrl(r.code, mkt)" in tbl and "tradingview.com" not in tbl
    assert "apiJson('/api/newlisting')" in _fn("onEnterNewlistingTab")
    assert "'newlisting'" in SRC[SRC.index("const IDXBAR_HIDDEN_MODES"):][:200]


def test_version_badge_matches_app_version():
    import re
    m = re.search(r'id="verBadge">(v[\d.]+)<', SRC)
    assert m and m.group(1) == app.VERSION


# ── v5.314 추가 지시: 상장 후 하락률 ────────────────────────────────
import pandas as pd  # noqa: E402


@pytest.mark.parametrize("ref_close,first_close,want", [
    (5000, 10000, -50.0), (3000, 10000, -70.0), (12000, 10000, 20.0), (10000, 10000, 0.0),
    (None, 10000, None), (5000, None, None), (5000, 0, None),
])
def test_drawdown_pct(ref_close, first_close, want):
    assert nl.drawdown_pct(ref_close, first_close) == want


def test_apply_series_uses_first_bar_and_ref_date_close():
    idx = pd.to_datetime(["2025-06-05", "2025-06-06", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"])
    c = pd.Series([40.0, 38.0, 22.0, 20.0, 19.0, 18.5], index=idx)
    r = nl.apply_series({"listed": "2025-06-05"}, c, REF)
    assert (r["first_close"], r["first_close_date"]) == (40.0, "2025-06-05")
    assert (r["ref_close"], r["ref_close_date"]) == (20.0, "2026-09-30")      # 기준일 이하 마지막 봉
    assert (r["close"], r["close_date"]) == (18.5, "2026-10-02")              # 최신(표시용)
    assert r["drawdown_pct"] == -50.0
    holiday = c.drop(pd.Timestamp("2026-09-30"))                              # 기준일 휴장 → 그 전 봉
    assert nl.apply_series({}, holiday, REF)["ref_close_date"] == "2026-09-29"
    assert nl.apply_series({}, None, REF)["drawdown_pct"] is None


def test_definition_says_first_day_close_not_ipo_price():
    assert "공모가가 아니다" in nl.DRAWDOWN_DEFINITION and "첫 거래일 '종가'" in nl.DRAWDOWN_DEFINITION
    assert "d.drawdown_definition" in SRC and "공모가 아님" in SRC


def test_first_close_fetch_only_for_result_rows(monkeypatch):
    """첫 봉·기준일 종가 조회는 13~20개월 통과 종목만 — 전수 조회 금지(사용자 지시)."""
    monkeypatch.setattr(nl, "kr_listings", lambda: ([
        {"market": "KOSPI", "code": "111111.KS", "name": "IN", "listed": "2025-08-14"},
        {"market": "KOSDAQ", "code": "222222.KQ", "name": "OUT", "listed": "2023-01-02"},
    ], {"total": 2, "date_unparsed": []}))
    monkeypatch.setattr(nl.lp, "us_universe", lambda path=None, refresh=False: ({"NEWU": "n", "OLDU": "o"}, {}))
    import harness
    monkeypatch.setattr(harness, "run_stamp", lambda: {})
    asked = {"kr": [], "us": []}
    idx = pd.to_datetime(["2025-08-14", "2026-09-30"])

    def fkr(tickers, tf):
        asked["kr"] += list(tickers)
        return {t: pd.Series([1000.0, 300.0], index=idx) for t in tickers}, []

    def fus(tickers, tf):
        asked["us"] += list(tickers)
        return {t: pd.Series([10.0, 5.0], index=idx) for t in tickers}, [], {}
    monkeypatch.setattr(nl.lp, "fetch_kr", fkr)
    monkeypatch.setattr(nl.lp, "fetch_us", fus)
    e = nl.build(REF, us_lookup={"NEWU": date(2025, 3, 3), "OLDU": date(2001, 1, 2)}.get, check_us_ready=False)
    assert asked == {"kr": ["111111.KS"], "us": ["NEWU"]}, "결과 밖 종목까지 조회했다"
    assert {r["code"]: r["drawdown_pct"] for r in e["rows"]} == {"111111.KS": -70.0, "NEWU": -50.0}


def _js(expr):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    src = "\n".join([SRC[SRC.index("const NL_DD_LEVELS"):SRC.index("\n", SRC.index("const NL_DD_LEVELS"))]]
                    + [_fn(n) for n in ("nlFilterByDrawdown", "nlChipCounts", "nlSortRows")])
    p = subprocess.run(["node", "-e", src + f"\nconsole.log(JSON.stringify({expr}));"], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


DD_ROWS = [{"code": c, "drawdown_pct": v} for c, v in
           [("a", -50.0), ("b", -49.99), ("c", -70.0), ("d", -69.99), ("e", -85.2), ("f", 12.0), ("g", None)]]


def test_chip_filters_include_exact_boundaries():
    got = _js(f"[50, 70].map(lv => nlFilterByDrawdown({json.dumps(DD_ROWS)}, lv).map(r => r.code))")
    assert got == [["a", "c", "d", "e"], ["c", "e"]]      # −50·−70 정확히 포함, −49.99·−69.99 제외


def test_chip_counts_match_filtered_rows():
    counts = _js(f"nlChipCounts({json.dumps(DD_ROWS)})")
    assert counts == {"0": 7, "50": 4, "70": 2}
    lens = _js(f"[0, 50, 70].map(lv => nlFilterByDrawdown({json.dumps(DD_ROWS)}, lv).length)")
    assert lens == [counts["0"], counts["50"], counts["70"]]


def test_drawdown_sort_nulls_last():
    asc = _js(f"nlSortRows({json.dumps(DD_ROWS)}, 'dd', 'asc').map(r => r.code)")
    desc = _js(f"nlSortRows({json.dumps(DD_ROWS)}, 'dd', 'desc').map(r => r.code)")
    assert asc == ["e", "c", "d", "a", "b", "f", "g"] and desc == ["f", "b", "a", "d", "c", "e", "g"]
    assert _js(f"nlSortRows({json.dumps(DD_ROWS)}, 'months', 'asc').map(r => r.code)") == [r["code"] for r in DD_ROWS]


def test_drawdown_column_uses_existing_return_colors():
    tbl = _fn("_nlTable")
    assert "_lptPct(r.drawdown_pct)" in tbl and "nlToggleDdSort()" in tbl
    # 첫 봉이 상장일과 다르면(이전상장 등) 조용히 넘기지 않고 첫 봉 날짜를 표에 표시
    assert "r.first_close_date !== r.listed ?" in tbl and "첫 봉 ${r.first_close_date}" in tbl
