"""v5.322 — 홈 달력(시스템 일정 + 내 일정) · 다가오는 일정 카드 흡수 · 오른쪽 열 순서 · 메모 3줄.

서버: /api/user-events — 저점 매매 기록·평가와 같은 레코드 단위 규칙(_rev_store_put·_rec_list_*·삭제 로그·날짜별 사본).
프론트(node, production 원문 실행): calMonthGrid·calShiftMonth·calEventsByDate·calSystemEvents·calApplyEventEdit.

사보타주 확인(2026-10-04, FAIL 확인 후 원복):
① user_event_put이 파일 대신 모듈 메모리 리스트에만 저장(새로고침 유실) → test_saved_event_survives_reload FAIL
② _rev_store_put 대신 base_rev 무시하고 덮어쓰기 → test_stale_rev_is_rejected FAIL
③ calEventsByDate가 같은 날짜의 시스템 일정을 내 일정으로 덮어씀 → test_system_and_user_events_coexist_same_day FAIL
④ calMonthGrid의 today 비교 제거(isToday 항상 false) → test_month_grid_shape_and_today FAIL
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import subprocess as _sp

import pytest

import app

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()


class _Req:
    def __init__(self, body=None):
        self._body = body
        self.headers = {"user-agent": "pytest"}
        self.client = type("C", (), {"host": "127.0.0.1"})()

    async def json(self):
        return self._body


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "USER_EVENTS_PATH", str(tmp_path / "user_events.json"))
    monkeypatch.setattr(app, "USER_EVENTS_DELETE_LOG_PATH", str(tmp_path / "user_events_deletions.log"))
    return tmp_path


def _put(rec, base):
    r = asyncio.run(app.user_event_put(rec["id"], _Req({"record": rec, "base_rev": base})))
    return r.status_code, json.loads(r.body)


def _get():
    return json.loads(asyncio.run(app.user_events_list()).body)["events"]


def _delete(rid):
    return json.loads(asyncio.run(app.user_event_delete(rid, _Req())).body)


EV = {"id": "ue_abc123", "date": "2026-10-08", "title": "FOMC 의사록 확인", "time": "03:00"}


# ── 서버 ───────────────────────────────────────────────────────────
def test_add_edit_delete(store):
    s, d = _put(EV, None)
    assert s == 200 and d["created"] and d["record"]["rev"] == 1
    s, d = _put({**EV, "title": "의사록", "time": None}, 1)
    assert s == 200 and d["record"]["rev"] == 2 and d["record"]["time"] is None
    assert [e["title"] for e in _get()] == ["의사록"]
    assert _delete(EV["id"])["deleted"] == EV["id"] and _get() == []
    log = (store / "user_events_deletions.log").read_text(encoding="utf-8").strip().splitlines()
    assert len(log) == 1 and json.loads(log[0])["record"]["title"] == "의사록"


def test_saved_event_survives_reload(store):
    """저장은 파일(/data) — 프로세스 메모리만 바뀌면 새로고침·재시작에 사라진다."""
    _put(EV, None)
    on_disk = json.loads((store / "user_events.json").read_text(encoding="utf-8"))
    assert [e["id"] for e in on_disk] == [EV["id"]]
    assert [e["id"] for e in app._rec_list_load(app.USER_EVENTS_PATH)] == [EV["id"]]


def test_stale_rev_is_rejected(store):
    _put(EV, None)
    assert _put({**EV, "title": "A"}, 1)[0] == 200            # rev 1 → 2
    s, d = _put({**EV, "title": "낡은 탭"}, 1)
    assert s == 409 and d["code"] == "conflict" and d["record"]["title"] == "A"
    assert _get()[0]["title"] == "A"


def test_deleted_event_is_not_resurrected(store):
    _put(EV, None)
    _delete(EV["id"])
    s, d = _put({**EV, "title": "되살리기"}, 1)
    assert s == 409 and d["code"] == "gone" and _get() == []


@pytest.mark.parametrize("bad,msg", [
    ({"title": "  "}, "제목"), ({"title": "두\n줄"}, "한 줄"), ({"title": "x" * 101}, "100자"),
    ({"time": "24:00"}, "HH:MM"), ({"time": "9:00"}, "HH:MM"), ({"date": "2026-02-30"}, "YYYY-MM-DD"),
    ({"id": "../x"}, "ue_"), ({"color": "red"}, "알 수 없는 필드"),
])
def test_validation(store, bad, msg):
    rec = {**EV, **bad}
    r = asyncio.run(app.user_event_put(rec["id"], _Req({"record": rec, "base_rev": None})))
    assert r.status_code == 400 and msg in json.loads(r.body)["error"]
    assert not (store / "user_events.json").exists()


def test_uses_shared_record_store_functions():
    """새 저장 구조 발명 금지 — 저점 매매 기록·평가와 같은 함수."""
    import inspect
    put, dele = inspect.getsource(app.user_event_put), inspect.getsource(app.user_event_delete)
    assert "_rev_store_put(events, rid, rec, body.get(\"base_rev\"))" in put and "_rec_list_write(USER_EVENTS_PATH" in put
    assert "_rev_store_delete_log(USER_EVENTS_DELETE_LOG_PATH" in dele
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert src.count('_daily_backup(USER_EVENTS_PATH, "user_events"') == 2      # 스케줄러 1 + 시작 직후 1
    assert 'USER_EVENTS_PATH = _resolve_persistent_path("user_events.json")' in src


def test_daily_backup_of_user_events(store):
    _put(EV, None)
    out = app._daily_backup(app.USER_EVENTS_PATH, "user_events", force=True)
    assert out and re.search(r"user_events_\d{8}\.json$", out)
    assert json.loads(open(out, encoding="utf-8").read())[0]["id"] == EV["id"]


def test_no_external_calendar():
    block = SRC[SRC.index("// ── v5.322(사용자 지시): 홈 달력"):SRC.index("function renderCalendar(data) {")]
    for bad in ("googleapis", "calendar.google", "gapi", "ical", "outlook"):
        assert bad not in block.lower()
    code = re.sub(r"//[^\n]*", "", block)
    assert code.count("'/api/user-events") == 3                               # GET · PUT · DELETE — 서버 경로만


# ── 프론트(node) ───────────────────────────────────────────────────
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
    pre = "const _escapeHtml = s => String(s);\n" + "\n".join(
        _fn(f) for f in ("calSystemEvents", "calEventsByDate", "calShiftMonth", "calMonthGrid", "calApplyEventEdit"))
    p = subprocess.run(["node", "-e", pre + f"\nconsole.log(JSON.stringify({expr}));"], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@pytest.mark.parametrize("ym,first,weeks,last", [
    ("2026-10", "2026-09-27", 5, "2026-10-31"),     # 10/1 목요일
    ("2026-02", "2026-02-01", 4, "2026-02-28"),     # 2/1 일요일, 28일 → 딱 4주
    ("2026-08", "2026-07-26", 6, "2026-09-05"),     # 8/1 토요일 → 6주
])
def test_month_grid_shape_and_today(ym, first, weeks, last):
    g = _js(f"calMonthGrid('{ym}', '2026-10-04')")
    assert len(g) == weeks and all(len(w) == 7 for w in g)
    flat = [c for w in g for c in w]
    assert flat[0]["date"] == first and flat[-1]["date"] == last
    assert all(c["inMonth"] == c["date"].startswith(ym) for c in flat)
    today = [c["date"] for c in flat if c["isToday"]]
    assert today == (["2026-10-04"] if "2026-10-04" in [c["date"] for c in flat] else [])
    if ym == "2026-10":
        assert today == ["2026-10-04"]


def test_month_move():
    assert _js("[calShiftMonth('2026-10', 1), calShiftMonth('2026-12', 1), calShiftMonth('2026-01', -1), calShiftMonth('2026-10', -13)]") \
        == ["2026-11", "2027-01", "2025-12", "2025-09"]


CAL_DATA = {"today": "2026-10-04",
            "macro_events": [{"date": "2026-10-08", "country": "US", "event": "FOMC 의사록", "importance": "high"}],
            "holidays": [{"date": "2026-10-09", "market": "KR", "label": "휴장"}],
            "earnings": [{"ticker": "NKE", "date": "2026-10-08", "d_minus": 4}]}


def test_system_events_same_fields_as_old_upcoming_card():
    ev = _js(f"calSystemEvents({json.dumps(CAL_DATA)})")
    assert [(e["date"], e["kind"]) for e in ev] == [("2026-10-08", "sys"), ("2026-10-09", "sys"), ("2026-10-08", "sys")]
    assert "미국 · FOMC 의사록" in ev[0]["html"] and "High" in ev[0]["html"]
    assert ev[1]["html"] == "한국 휴장" and "NKE 실적" in ev[2]["html"] and "D-4" in ev[2]["html"]
    assert _js("calSystemEvents(null)") == []


def test_system_and_user_events_coexist_same_day():
    user = [{"id": "ue_b", "date": "2026-10-08", "title": "B", "time": None},
            {"id": "ue_a", "date": "2026-10-08", "title": "A", "time": "21:30"},
            {"id": "ue_c", "date": "2026-10-08", "title": "C", "time": "09:00"},
            {"id": "ue_d", "date": "2026-10-20", "title": "D", "time": None}]
    by = _js(f"calEventsByDate(calSystemEvents({json.dumps(CAL_DATA)}), {json.dumps(user)})")
    assert len(by["2026-10-08"]["sys"]) == 2
    assert [e["id"] for e in by["2026-10-08"]["user"]] == ["ue_c", "ue_a", "ue_b"]     # 시각순, 시간 없음은 뒤
    assert by["2026-10-09"] == {"sys": by["2026-10-09"]["sys"], "user": []} and len(by["2026-10-09"]["sys"]) == 1
    assert by["2026-10-20"]["sys"] == [] and [e["id"] for e in by["2026-10-20"]["user"]] == ["ue_d"]


def test_apply_event_edit():
    new = _js("calApplyEventEdit(null, {title: '  실적\\n 발표 ', time: '', date: '2026-10-08'}, 'ue_new1')")
    assert new == {"id": "ue_new1", "date": "2026-10-08", "title": "실적 발표", "time": None}
    old = {"id": "ue_x", "date": "2026-10-08", "title": "a", "time": None, "rev": 3}
    e = _js(f"calApplyEventEdit({json.dumps(old)}, {{title: 'b', time: '09:05'}}, 'ue_ignored')")
    assert e == {**old, "title": "b", "time": "09:05"}                               # id·날짜·rev 유지
    for fields, msg in (("{title: ' '}", "제목"), ("{title: 'x', time: '25:00'}", "HH:MM"), ("{title: 'x'}", "날짜")):
        got = _js(f"(() => {{ try {{ calApplyEventEdit(null, {fields}, 'ue_1'); return null; }} catch (e) {{ return e.message; }} }})()")
        assert got and msg in got


def test_client_validation_matches_server_limits():
    assert "title.length > 100" in _fn("calApplyEventEdit") and app.USER_EVENT_TITLE_MAX == 100


# ── 배치 · 제거 · 다른 카드 불변 ──────────────────────────────────
def test_right_column_order_calendar_note_jongga_sector():
    view = SRC[SRC.index('id="calendarView"'):SRC.index('<div class="modal-bg"', SRC.index('id="calendarView"'))]
    side = view[view.index('<aside class="home-col home-side">'):]
    pos = [side.index(x) for x in ('id="homeCalBox"', 'id="dailyNoteBox"', 'id="calendarDocSide"')]
    assert pos == sorted(pos)
    cal = _fn("renderCalendar")
    assert "docSide.innerHTML = renderJonggaForwardCard(data.jongga_forward)\n    + renderSectorAccelCard(data.sector_flow);" in cal
    assert cal.count("renderHomeCal()") == 1


def test_form_lives_outside_rerendered_area():
    """renderCalendar·renderHomeCal은 #homeCalForm을 안 건드린다(입력 중 데이터 재렌더가 폼을 지우지 않게)."""
    code = lambda s: re.sub(r"//[^\n]*", "", s)
    assert "homeCalForm" not in code(_fn("renderCalendar")) and "homeCalForm" not in code(_fn("renderHomeCal"))
    assert SRC.count('id="homeCalForm"') == 1 and "getElementById('homeCalForm')" in _fn("renderHomeCalForm")


def test_upcoming_card_fully_removed():
    code = re.sub(r"//[^\n]*", "", SRC)
    for gone in ("renderUpcomingCard", "_eventsExpanded", "toggleEventsExpanded", 'aria-label="일정"', ">다가오는 일정<"):
        assert gone not in code, gone
    # 흡수한 기능은 달력에 남는다
    rh = _fn("renderHomeCal")
    for kept in ("runMacroCalendarNow(this)", "data.macro_note", "data.macro_error", "macro_generated_at", "calSystemEvents(data)"):
        assert kept in rh, kept
    assert "async function runMacroCalendarNow(" in SRC or "function runMacroCalendarNow(" in SRC


def _head_src(path):
    return _sp.run(["git", "show", f"HEAD:{path}"], capture_output=True, text=True, cwd=ROOT).stdout


def _fn_in(src, name):
    start = src.index(f"function {name}(")
    i = src.index("{", src.index(")", start))
    d = 0
    for j in range(i, len(src)):
        d += {"{": 1, "}": -1}.get(src[j], 0)
        if d == 0:
            return src[start:j + 1]


@pytest.mark.parametrize("name", ["renderJonggaForwardCard", "renderSectorAccelCard", "renderLowpointHtml",
                                  "renderMyTrackBoard", "saveDailyNote", "loadDailyNoteBox", "_dailyNotePastInput",
                                  "copyAllDailyNotes"])
def test_other_cards_and_note_saving_unchanged(name):
    """다른 카드·메모 저장 경로는 v5.321(직전 커밋)과 글자 하나 다르지 않다."""
    head = _head_src("static/index.html")
    if "function renderHomeCal(" in head:
        pytest.skip("직전 커밋에 이미 달력이 있다 — 비교 기준이 아님")
    assert _fn_in(SRC, name) == _fn_in(head, name)


def test_note_compact_three_lines():
    assert "const DAILY_NOTE_MAX_ROWS = 3;" in SRC
    box = _fn("renderDailyNoteBox")
    assert 'id="dailyNoteText" rows="3"' in box and "dailyNotePreview" not in box and 'style="display:none;margin-top:6px"' not in box
    assert "saveDailyNote(e.target.value)" in box                                # 자동저장 그대로
    assert "toggleDailyNoteExpanded" not in re.sub(r"//[^\n]*", "", SRC)


def test_calendar_css_uses_theme_tokens():
    css = SRC[SRC.index("/* v5.322 홈 달력"):SRC.index("@media (max-width:899px)", SRC.index("/* v5.322 홈 달력"))]
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", css), "달력 CSS에 하드코딩 색"
    assert "var(--n-accent)" in css and "repeat(7,minmax(0,1fr))" in css


def test_user_events_loaded_on_home_entry():
    assert "loadUserEvents();" in _fn("onEnterCalendarTab")
