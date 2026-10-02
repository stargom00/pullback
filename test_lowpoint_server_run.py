"""v5.301 — 저점종목 서버 자동 실행(토요일 KST) + /data 저장 + 레포 폴백 + 실패 시 이전 결과 유지.

사보타주 확인(2026-09-30): ① _lowpoint_view의 레포 폴백 제거(data만 읽음) →
test_view_prefers_data_then_repo_per_tf FAIL ② 실행 실패 시 결과 파일을 비우도록 바꿈 →
test_failure_keeps_previous_result_and_flags_card FAIL. 둘 다 원복.
v5.306(월봉 = 매월 1일 09:20 KST) 사보타주(2026-09-30): _lowpoint_last_slot 월봉을 옛 첫 토요일
로직으로 되돌리면 22건 FAIL — 원복.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

import app

KST = timezone(timedelta(hours=9))
NZDT = timezone(timedelta(hours=13))


def _k(s, tz=KST):
    return datetime.fromisoformat(s).replace(tzinfo=tz)


# ── 스케줄 판정(KST 기준) ───────────────────────────────────────────
@pytest.mark.parametrize("now,slot,target", [
    ("2026-10-03 08:59", "2026-09-26 09:00", "2026-09-25"),   # 토 09:00 전 → 지난주
    ("2026-10-03 09:00", "2026-10-03 09:00", "2026-10-02"),   # 토 09:00 정각
    ("2026-10-04 23:00", "2026-10-03 09:00", "2026-10-02"),   # 일요일(따라잡기)
    ("2026-10-09 18:00", "2026-10-03 09:00", "2026-10-02"),   # 다음 금요일
])
def test_week_slot_kst(now, slot, target):
    s, t = app._lowpoint_last_slot("week", _k(now))
    assert s == _k(slot) and t == target


def test_week_slot_is_kst_not_local_tz():
    # NZDT 토 12:59 = KST 토 08:59 → 아직 이번 주 슬롯 전
    s, t = app._lowpoint_last_slot("week", _k("2026-10-03 12:59", NZDT))
    assert t == "2026-09-25"
    s, t = app._lowpoint_last_slot("week", _k("2026-10-03 13:00", NZDT))   # = KST 09:00
    assert t == "2026-10-02"


# v5.306(사용자 지시): 월봉 = 매월 1일(예전 첫 토요일 09:20). v5.307: 1일 09:20 → 08:00 KST
# (KR 장 시작 스캔과 메모리 경합 회피, 월봉 확정은 늦어도 1일 06:00 KST).
@pytest.mark.parametrize("now,slot,target", [
    ("2026-10-01 07:59", "2026-09-01 08:00", "2026-08-31"),   # 1일 08:00 전 → 지난달 1일 슬롯
    ("2026-10-01 08:00", "2026-10-01 08:00", "2026-09-30"),   # 1일 08:00 정각
    ("2026-10-03 09:20", "2026-10-01 08:00", "2026-09-30"),   # 첫 토요일은 더 이상 슬롯이 아니다
    ("2026-11-01 08:00", "2026-11-01 08:00", "2026-10-31"),   # 일요일인 1일도 1일
    ("2027-01-01 08:00", "2027-01-01 08:00", "2026-12-31"),   # 해 넘김
    ("2027-01-01 07:59", "2026-12-01 08:00", "2026-11-30"),
    ("2026-03-01 08:00", "2026-03-01 08:00", "2026-02-28"),   # 2월 말일
])
def test_month_slot_first_day_kst(now, slot, target):
    s, t = app._lowpoint_last_slot("month", _k(now))
    assert s == _k(slot) and t == target


def test_month_slot_is_kst_not_local_tz():
    # NZDT 10-01 11:59 = KST 07:59 → 아직 9월분 슬롯 전 / NZDT 12:00 = KST 08:00
    assert app._lowpoint_last_slot("month", _k("2026-10-01 11:59", NZDT))[1] == "2026-08-31"
    assert app._lowpoint_last_slot("month", _k("2026-10-01 12:00", NZDT))[1] == "2026-09-30"


@pytest.mark.parametrize("now,due", [
    ("2026-10-01 07:59", None),            # 슬롯 전(9월 1일 슬롯은 창 밖)
    ("2026-10-01 08:00", "2026-09-30"),    # 1일 08:00
    ("2026-10-01 09:20", "2026-09-30"),    # 옛 v5.306 시각도 창 안
    ("2026-10-02 12:00", "2026-09-30"),    # 2일 — 창 안(따라잡기)
    ("2026-10-03 07:59", "2026-09-30"),    # 1일 08:00 + 47시간 59분
    ("2026-10-03 08:01", None),            # +48시간 넘음
    ("2026-10-04 08:00", None),            # 4일 — 창 밖
    ("2026-10-03 09:20", None),            # 첫 토요일(옛 기준 시각) — 더 이상 실행 아님
])
def test_month_due_window_first_day(now, due):
    assert app._lowpoint_due("month", _k(now), {}) == due


# 옛 기준(첫 토요일)으로 쓰인 상태 파일이 있어도 새 기준에서 오작동하지 않는가.
# 라벨(전월 말일)은 두 기준이 같다 — target 비교가 그대로 맞는다.
@pytest.mark.parametrize("old_state,due", [
    ({"month": {"target": "2026-08-31", "status": "ok", "attempts": 1,
                "started_at": _k("2026-09-05 09:20").isoformat()}}, "2026-09-30"),   # 지난달 성공 기록
    ({"month": {"target": "2026-08-31", "status": "failed", "attempts": 3,
                "started_at": _k("2026-09-05 11:20").isoformat()}}, "2026-09-30"),   # 지난달 시도 소진 — 새 달엔 무관
    ({"month": {"target": "2026-08-31", "status": "running", "attempts": 1,
                "started_at": _k("2026-09-05 09:20").isoformat()}}, "2026-09-30"),   # 지난달 죽은 실행
    ({"week": {"target": "2026-09-25", "status": "ok", "attempts": 1}}, "2026-09-30"),    # 주봉 기록만(현 운영 형태)
    ({}, "2026-09-30"),                                                                  # 상태 파일 없음/빈 파일
])
def test_old_first_saturday_state_does_not_skip_oct1(old_state, due):
    assert app._lowpoint_due("month", _k("2026-10-01 08:00"), old_state) == due
    assert app._lowpoint_due("week", _k("2026-10-01 08:00"), old_state) is None   # 주봉은 토요일 그대로


def test_month_ok_on_1st_is_not_rerun_on_first_saturday():
    ok = {"month": {"target": "2026-09-30", "status": "ok", "attempts": 1,
                    "started_at": _k("2026-10-01 08:00").isoformat()}}
    for now in ("2026-10-01 08:20", "2026-10-02 08:00", "2026-10-03 07:59", "2026-10-03 09:20"):
        assert app._lowpoint_due("month", _k(now), ok) is None, now


def test_schedule_times_unchanged():
    assert app.LOWPOINT_SCHEDULE_HM == {"week": (9, 0), "month": (8, 0)}   # v5.307: 월봉 08:00
    assert app.LOWPOINT_CATCHUP_HOURS == 48


def test_due_rules():
    now = _k("2026-10-03 10:00")
    assert app._lowpoint_due("week", now, {}) == "2026-10-02"
    assert app._lowpoint_due("week", now, {"week": {"target": "2026-10-02", "status": "ok"}}) is None
    recent_fail = {"week": {"target": "2026-10-02", "status": "failed", "attempts": 1,
                            "started_at": _k("2026-10-03 09:30").isoformat()}}
    assert app._lowpoint_due("week", now, recent_fail) is None           # 60분 안 됨
    assert app._lowpoint_due("week", _k("2026-10-03 10:31"), recent_fail) == "2026-10-02"
    maxed = {"week": {**recent_fail["week"], "attempts": 3}}
    assert app._lowpoint_due("week", _k("2026-10-03 23:00"), maxed) is None
    # 따라잡기 창(48시간) 밖이면 상태가 없어도 안 돈다 — 첫 배포 직후(평일) 즉시 실행 방지
    assert app._lowpoint_due("week", _k("2026-09-30 10:00"), {}) is None      # 수요일, 토 슬롯 +4일
    assert app._lowpoint_due("month", _k("2026-09-30 10:00"), {}) is None     # 9월 1일 슬롯 한참 지남
    assert app._lowpoint_due("week", _k("2026-10-05 08:59"), {}) == "2026-10-02"  # 월 08:59 = 창 안
    assert app._lowpoint_due("week", _k("2026-10-05 09:01"), {}) is None
    running = {"week": {"target": "2026-10-02", "status": "running", "attempts": 1,
                        "started_at": _k("2026-10-03 09:00").isoformat()}}
    assert app._lowpoint_due("week", _k("2026-10-03 10:00"), running) is None   # 아직 도는 중
    assert app._lowpoint_due("week", _k("2026-10-03 11:30"), running) == "2026-10-02"  # 죽은 실행


# ── /data 우선 · 레포 폴백 · 실패 시 이전 결과 유지 ─────────────────
def _entry(bar, n=1, run="2026-10-03 09:15:00 KST"):
    return {"bar_date": bar, "run_stamp": {"run_at_kst": run}, "markets": ["KOSPI"],
            "excluded_counts": {}, "rows": [{"market": "KOSPI", "code": "005930.KS", "name": "삼성전자"}] * n}


@pytest.fixture
def paths(tmp_path, monkeypatch):
    p = {"data": tmp_path / "data_lowpoint.json", "repo": tmp_path / "repo_lowpoint.json",
         "state": tmp_path / "state.json"}
    monkeypatch.setattr(app, "LOWPOINT_DATA_PATH", str(p["data"]))
    monkeypatch.setattr(app, "LOWPOINT_LATEST_PATH", str(p["repo"]))
    monkeypatch.setattr(app, "LOWPOINT_STATE_PATH", str(p["state"]))
    return p


def test_view_prefers_data_then_repo_per_tf(paths):
    paths["repo"].write_text(json.dumps({"week": _entry("2026-09-25"), "month": _entry("2026-08-31")}))
    v = app._lowpoint_view(_k("2026-10-03 10:00"))
    assert v["week"]["source"] == "repo" and v["month"]["source"] == "repo", "첫 배포 직후 카드가 비면 안 된다"
    paths["data"].write_text(json.dumps({"week": _entry("2026-10-02", n=2)}))
    v = app._lowpoint_view(_k("2026-10-03 10:00"))
    assert v["week"]["source"] == "data" and len(v["week"]["rows"]) == 2
    assert v["month"]["source"] == "repo", "서버가 아직 안 돌린 tf는 레포 폴백"


def test_success_writes_data_and_state(paths):
    def job(tf, now):
        app._lowpoint_job_ran = True
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(app.__file__), "scripts", "screens"))
        import lowpoint as lp
        lp.write_publish(tf, _entry("2026-10-02"), path=app.LOWPOINT_DATA_PATH)
        return {"bar_date": "2026-10-02", "rows": 1, "counts": {}}
    rec = asyncio.run(app._maybe_run_lowpoint(_k("2026-10-03 09:05"), _job=job))
    assert rec["status"] == "ok" and rec["target"] == "2026-10-02"
    state = json.loads(paths["state"].read_text())
    assert state["week"]["status"] == "ok"
    assert json.loads(paths["data"].read_text())["week"]["bar_date"] == "2026-10-02"
    # 같은 라벨(주봉 10-02)은 다시 안 돈다. 월봉은 10-01 08:00 슬롯 +48시간이 지나 이날은 없다(v5.307)
    assert asyncio.run(app._maybe_run_lowpoint(_k("2026-10-03 09:30"), _job=job)) is None
    st = json.loads(paths["state"].read_text())
    assert st["week"]["attempts"] == 1 and st["week"]["status"] == "ok"


def test_month_runs_on_the_1st_through_runner(paths):
    """스케줄러가 부르는 경로 그대로: 10-01 08:04(4분 틱) — 주봉은 창 밖이라 건너뛰고 월봉만 돈다."""
    ran = []

    def job(tf, now):
        ran.append(tf)
        return {"bar_date": "2026-09-30", "rows": 0, "counts": {}}
    assert asyncio.run(app._maybe_run_lowpoint(_k("2026-10-01 07:59"), _job=job)) is None   # 슬롯 전
    rec = asyncio.run(app._maybe_run_lowpoint(_k("2026-10-01 08:04"), _job=job))
    assert ran == ["month"] and rec["target"] == "2026-09-30" and rec["status"] == "ok"
    # v5.314: 다음 틱엔 월봉 뒤 순차로 신규상장(같은 러너·같은 기준봉), 그다음 틱엔 할 일 없음
    assert asyncio.run(app._maybe_run_lowpoint(_k("2026-10-01 08:08"), _job=job))["target"] == "2026-09-30"
    assert asyncio.run(app._maybe_run_lowpoint(_k("2026-10-01 08:12"), _job=job)) is None
    # 첫 토요일: 주봉(토 09:00)은 정상 실행, 월봉·신규상장은 재실행 없음
    assert asyncio.run(app._maybe_run_lowpoint(_k("2026-10-03 09:20"), _job=job))["target"] == "2026-10-02"
    assert asyncio.run(app._maybe_run_lowpoint(_k("2026-10-03 09:24"), _job=job)) is None
    assert ran == ["month", "newlisting", "week"]


def test_failure_keeps_previous_result_and_flags_card(paths):
    prev = {"week": _entry("2026-09-25", n=3)}
    paths["data"].write_text(json.dumps(prev))
    before = paths["data"].read_text()

    def boom(tf, now):
        raise RuntimeError("KIND 응답 이상")
    rec = asyncio.run(app._maybe_run_lowpoint(_k("2026-10-03 09:05"), _job=boom))
    assert rec["status"] == "failed" and "KIND" in rec["error"]
    assert paths["data"].read_text() == before, "실패했는데 이전 결과 파일이 바뀌었다"
    v = app._lowpoint_view(_k("2026-10-03 10:00"))
    assert len(v["week"]["rows"]) == 3 and v["week"]["refresh_failed"]["error"].startswith("RuntimeError")


def test_failure_without_previous_result_shows_missing_with_flag(paths):
    def boom(tf, now):
        raise RuntimeError("x")
    asyncio.run(app._maybe_run_lowpoint(_k("2026-10-03 09:05"), _job=boom))
    v = app._lowpoint_view(_k("2026-10-03 10:00"))
    assert v["week"]["missing"] is True and v["week"]["refresh_failed"]


def test_write_publish_is_atomic(tmp_path, monkeypatch):
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(app.__file__), "scripts", "screens"))
    import lowpoint as lp
    path = str(tmp_path / "lp.json")
    lp.write_publish("week", _entry("2026-09-25"), path=path)
    before = open(path).read()

    def bad_dump(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(lp.json, "dump", bad_dump)
    with pytest.raises(OSError):
        lp.write_publish("week", _entry("2026-10-02"), path=path)
    assert open(path).read() == before, "쓰다 실패했는데 원본이 깨졌다"


def test_scheduler_calls_runner_in_background():
    import inspect
    src = inspect.getsource(app._scheduler_loop)
    assert "asyncio.create_task(_maybe_run_lowpoint())" in src
    job = inspect.getsource(app._maybe_run_lowpoint)
    assert "run_in_executor(_LOWPOINT_EXECUTOR" in job and '_release_memory(f"lowpoint {tf}")' in job
    assert "_rss_mb()" in job
