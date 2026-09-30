"""v5.301 — 저점종목 서버 자동 실행(토요일 KST) + /data 저장 + 레포 폴백 + 실패 시 이전 결과 유지.

사보타주 확인(2026-09-30): ① _lowpoint_view의 레포 폴백 제거(data만 읽음) →
test_view_prefers_data_then_repo_per_tf FAIL ② 실행 실패 시 결과 파일을 비우도록 바꿈 →
test_failure_keeps_previous_result_and_flags_card FAIL. 둘 다 원복.
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


@pytest.mark.parametrize("now,target", [
    ("2026-10-03 09:19", "2026-08-31"),   # 10월 첫 토(10-03) 09:20 전 → 9월 첫 토 실행분
    ("2026-10-03 09:20", "2026-09-30"),   # 10월 첫 토 09:20
    ("2026-10-10 09:20", "2026-09-30"),   # 둘째 토요일엔 새 월봉 없음
    ("2026-11-07 09:20", "2026-10-31"),
])
def test_month_slot_first_saturday_kst(now, target):
    assert app._lowpoint_last_slot("month", _k(now))[1] == target


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
    assert app._lowpoint_due("month", _k("2026-09-30 10:00"), {}) is None     # 9월 첫 토(09-05) 한참 지남
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
    # 같은 라벨(주봉 10-02)은 다시 안 돈다 — 다음 틱(09:30)엔 월봉(첫 토 09:20)이 돈다
    rec2 = asyncio.run(app._maybe_run_lowpoint(_k("2026-10-03 09:30"), _job=job))
    assert rec2["target"] == "2026-09-30", rec2
    st = json.loads(paths["state"].read_text())
    assert st["week"]["attempts"] == 1 and st["week"]["status"] == "ok"
    assert asyncio.run(app._maybe_run_lowpoint(_k("2026-10-03 09:40"), _job=job)) is None


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
