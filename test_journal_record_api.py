"""v5.300 — 일지 저장을 레코드 단위로(PUT/DELETE /api/journal/{id}), 전체 배열 POST는 410.

배경: 예전 POST /api/journal은 배열 전체를 받아 "서버엔 있는데 배열엔 없고 최근 5분 안에
안 바뀐 레코드 = 삭제"로 병합했다(사실상 덮어쓰기). 옛 배열을 보내는 경로 하나로 서버
일지가 60건 → 3건이 됐다(v5.299 로컬 재현).

사보타주 확인(2026-09-30): ① journal_put의 base_rev 비교 제거 → test_stale_put_is_409_and_record_unchanged
FAIL ② POST /api/journal을 옛 병합 저장으로 되돌림 → test_full_array_post_is_410_and_file_unchanged FAIL.
둘 다 원복.
"""
import asyncio
import json
import os

import pytest

import app


class _Req:
    def __init__(self, body=None, host="127.0.0.1"):
        self._body = body
        self.headers = {"user-agent": "pytest"}
        self.client = type("C", (), {"host": host})()

    async def json(self):
        return self._body


def _b(r):
    return json.loads(r.body)


@pytest.fixture
def jfile(monkeypatch, tmp_path):
    path = tmp_path / "journal_user.json"
    monkeypatch.setattr(app, "JOURNAL_PATH", str(path))
    monkeypatch.setattr(app, "JOURNAL_DELETE_LOG_PATH", str(tmp_path / "journal_deletions.log"))
    recs = [{"id": i, "ticker": f"T{i}", "status": "closed", "entry": 100, "stop": 90,
             "result_r": "1.0", "updated_at": "1970-01-01T00:00:00+09:00",
             "entry_source": "actual", "result_source": "plan"} for i in range(1, 61)]
    path.write_text(json.dumps(recs), encoding="utf-8")
    return path


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _put(rid, record, base_rev, edit=False):
    return asyncio.run(app.journal_put(rid, _Req({"record": record, "base_rev": base_rev, "edit": edit})))


def test_schema_migration_adds_rev_only(jfile):
    before = _load(jfile)
    after = app.load_journal()
    assert len(after) == len(before) == 60
    assert [r["id"] for r in after] == [r["id"] for r in before]
    for a, b in zip(after, before):
        assert a["rev"] == 1
        assert {k: v for k, v in a.items() if k != "rev"} == b, "rev 외 필드가 바뀌었다"


def test_put_updates_one_record_and_bumps_rev(jfile):
    j = app.load_journal()
    r = dict(j[4]); r["note"] = "메모"
    res = _put(r["id"], r, 1)
    assert res.status_code == 200 and _b(res)["record"]["rev"] == 2
    after = _load(jfile)
    assert len(after) == 60 and after[4]["note"] == "메모" and after[4]["rev"] == 2
    assert all(x["rev"] == 1 for i, x in enumerate(after) if i != 4)


def test_stale_put_is_409_and_record_unchanged(jfile):
    j = app.load_journal()
    r = dict(j[0]); r["note"] = "첫 변경"
    assert _put(1, r, 1).status_code == 200            # rev 1 → 2
    stale = dict(j[0]); stale["note"] = "낡은 탭의 변경"
    res = _put(1, stale, 1)                             # 아직 rev 1이라고 믿는 탭
    assert res.status_code == 409
    body = _b(res)
    assert body["code"] == "conflict" and body["record"]["note"] == "첫 변경"
    after = _load(jfile)
    assert after[0]["note"] == "첫 변경" and after[0]["rev"] == 2


def test_full_array_post_is_410_and_file_unchanged(jfile):
    app.load_journal()
    before = jfile.read_text(encoding="utf-8")
    old = [{"id": 9001, "ticker": "옛"}]
    res = asyncio.run(app.save_journal_disabled(_Req(old)))
    assert res.status_code == 410 and _b(res)["code"] == "reload_required"
    assert jfile.read_text(encoding="utf-8") == before


def test_only_delete_removes_and_logs(jfile, tmp_path):
    app.load_journal()
    # PUT으로 배열에 없는 레코드가 "지워지는" 일은 없다(다른 59건 그대로)
    r = dict(app.load_journal()[9]); r["note"] = "x"
    _put(10, r, 1)
    assert len(_load(jfile)) == 60
    res = asyncio.run(app.journal_delete(10, _Req()))
    assert res.status_code == 200
    after = _load(jfile)
    assert len(after) == 59 and all(x["id"] != 10 for x in after)
    log = (tmp_path / "journal_deletions.log").read_text(encoding="utf-8").strip().splitlines()
    assert len(log) == 1
    e = json.loads(log[0])
    assert e["id"] == 10 and e["record"]["ticker"] == "T10" and e["client"] == "127.0.0.1" and e["ts"]


def test_concurrent_puts_on_different_records_both_apply(jfile):
    j = app.load_journal()
    a = dict(j[1]); a["note"] = "A"
    b = dict(j[2]); b["note"] = "B"

    async def both():
        return await asyncio.gather(app.journal_put(2, _Req({"record": a, "base_rev": 1})),
                                    app.journal_put(3, _Req({"record": b, "base_rev": 1})))
    r1, r2 = asyncio.run(both())
    assert r1.status_code == r2.status_code == 200
    after = _load(jfile)
    assert after[1]["note"] == "A" and after[2]["note"] == "B" and len(after) == 60


def test_create_new_record(jfile):
    app.load_journal()
    res = _put(99999, {"id": 99999, "ticker": "NEW", "status": "pending"}, None)
    assert res.status_code == 200 and _b(res)["created"] is True
    after = _load(jfile)
    assert len(after) == 61 and after[-1]["rev"] == 1


def test_put_for_deleted_record_is_gone_not_revived(jfile):
    app.load_journal()
    asyncio.run(app.journal_delete(5, _Req()))
    res = _put(5, {"id": 5, "ticker": "T5"}, 1)
    assert res.status_code == 409 and _b(res)["code"] == "gone"
    assert all(x["id"] != 5 for x in _load(jfile))


def test_protected_fields_need_explicit_edit(jfile):
    j = app.load_journal()
    r = dict(j[0]); r["entry_actual"] = 123; r["entry_source"] = "toss"
    rec = _b(_put(1, r, 1))["record"]
    assert rec.get("entry_actual") is None and rec["entry_source"] == "actual", "자동저장이 실체결 필드를 바꿨다"
    r2 = dict(rec); r2["entry_actual"] = 123
    rec2 = _b(_put(1, r2, rec["rev"], edit=True))["record"]
    assert rec2["entry_actual"] == 123


def test_backups_keep_14_newest(jfile, monkeypatch):
    d = os.path.dirname(str(jfile))
    for day in range(1, 20):   # 19개 오래된 사본
        open(os.path.join(d, f"journal_202608{day:02d}.json"), "w").write("[]")
    wrote = app._journal_daily_backup(force=True)
    assert wrote and os.path.exists(wrote)
    backups = sorted(f for f in os.listdir(d) if f.startswith("journal_2") and f.endswith(".json"))
    assert len(backups) == 14
    assert os.path.basename(wrote) in backups
    assert "journal_20260801.json" not in backups and "journal_20260806.json" not in backups
    assert json.loads(open(wrote).read()) == _load(jfile)


def test_server_internal_writers_bump_rev(jfile):
    """토스 자동채움·손절 동기화도 rev를 올려야 그 사이 낡은 탭의 PUT이 409로 막힌다."""
    import inspect
    assert "_journal_bump(r)" in inspect.getsource(app.positions_sync)
    assert "_journal_bump(r)" in inspect.getsource(app.positions_set_stop)
    assert '"rev": 1' in inspect.getsource(app.watch_quick)
