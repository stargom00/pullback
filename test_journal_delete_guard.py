"""일지 삭제가 안 되던 사고 — deleted_ids로 병합 가드 우회 (v5.247,
사용자 지시). 2026-09-11 실사고: 계속 추적 중인(자동 가격갱신 대상)
레코드는 updateTracking()이 60초마다 last_price/last_checked를 바꿔
updated_at이 거의 항상 "5분 이내"였다 — 그래서 delJournal()로 지워도
save_journal()의 병합 가드(v5.187, JOURNAL_CONCURRENT_KEEP_WINDOW_SEC=
300초 이내 갱신된 레코드는 "누락"으로 보지 않고 되살림)가 매번 되살려
삭제가 구조적으로 불가능했다.

수정: 클라이언트가 "이건 삭제다"를 명시하는 deleted_ids 필드를
{records, edit_id} body에 추가 — 그 id는 최근 갱신 여부와 무관하게
가드를 건너뛰고 삭제한다. 가드 자체(동시 편집 보호)는 유지 — deleted_ids
가 없는 누락은 여전히 되살아나야 한다(회귀 방지 대상)."""
import asyncio
import json as _json

import pytest

import app


class _FakeRequest:
    """save_journal(request: Request)이 쓰는 건 await request.json()뿐이라
    실제 starlette Request 대신 최소 스텁으로 충분."""
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def _body(response):
    return _json.loads(response.body)


@pytest.fixture
def isolated_journal(monkeypatch, tmp_path):
    path = tmp_path / "journal_user.json"
    monkeypatch.setattr(app, "JOURNAL_PATH", str(path))
    return path


def _seed(path, records):
    with open(path, "w", encoding="utf-8") as f:
        _json.dump(records, f, ensure_ascii=False)


def _recent_record(rid, **overrides):
    rec = {
        "id": rid, "ticker": "008930.KS", "name": "한미사이언스",
        "status": "entered", "tracking": True,
        "updated_at": app._now_iso(),   # 방금 갱신됨 — 자동가격갱신 흉내
    }
    rec.update(overrides)
    return rec


def test_deleted_ids_bypasses_recent_guard(isolated_journal):
    """★ 핵심 재현+수정 확인. 방금 갱신된(5분 이내) 레코드도 deleted_ids에
    있으면 삭제돼야 한다 — 이게 이번 사고의 실제 재현 조건."""
    target = _recent_record(1001)
    other = _recent_record(1002, ticker="005930.KS", name="삼성전자")
    _seed(isolated_journal, [target, other])

    payload = {"records": [other], "edit_id": None, "deleted_ids": [1001]}
    resp = asyncio.run(app.save_journal(_FakeRequest(payload)))
    body = _body(resp)

    assert body["ok"] is True
    ids = {r["id"] for r in body["journal"]}
    assert 1001 not in ids, "deleted_ids로 보냈는데도 병합 가드가 되살림 — 사고 재현"
    assert 1002 in ids

    # 파일에도 실제로 반영됐는지(응답만 그런 게 아니라 저장까지).
    with open(isolated_journal, encoding="utf-8") as f:
        saved = _json.load(f)
    assert 1001 not in {r["id"] for r in saved}


def test_missing_without_deleted_ids_still_revived(isolated_journal):
    """회귀 방지 — deleted_ids 없이 그냥 배열에서 빠지기만 하면(자동저장이
    아직 그 레코드를 모르는 경우 등) 기존처럼 되살아나야 한다. 가드 자체는
    안 없앤다(사용자 지시)."""
    target = _recent_record(2001)
    other = _recent_record(2002)
    _seed(isolated_journal, [target, other])

    payload = {"records": [other], "edit_id": None, "deleted_ids": []}
    resp = asyncio.run(app.save_journal(_FakeRequest(payload)))
    body = _body(resp)

    ids = {r["id"] for r in body["journal"]}
    assert 2001 in ids, "deleted_ids 없는 누락까지 삭제로 처리됨 — 가드 회귀"
    assert 2002 in ids


def test_deleted_ids_omitted_key_defaults_to_empty(isolated_journal):
    """deleted_ids 키 자체가 없는 요청(구형 호출부)도 크래시 없이 기존
    가드 동작 그대로여야 한다."""
    target = _recent_record(3001)
    _seed(isolated_journal, [target])

    payload = {"records": [], "edit_id": None}   # deleted_ids 키 생략
    resp = asyncio.run(app.save_journal(_FakeRequest(payload)))
    body = _body(resp)
    assert 3001 in {r["id"] for r in body["journal"]}


def test_legacy_array_body_still_works(isolated_journal):
    """body가 배열 그대로인 구형 호출(하위호환) — deleted_ids를 보낼 방법이
    없는 경로라 기존 가드 동작 그대로(회귀 없음만 확인)."""
    target = _recent_record(4001)
    _seed(isolated_journal, [target])

    resp = asyncio.run(app.save_journal(_FakeRequest([])))
    body = _body(resp)
    assert body["ok"] is True
    assert 4001 in {r["id"] for r in body["journal"]}


def test_deleted_ids_for_already_old_record_no_error(isolated_journal):
    """이미 5분 넘게 지난(원래도 가드 없이 삭제될) 레코드를 deleted_ids로
    또 보내도 문제없이 삭제돼야 한다(중복 경로 겹침 시 크래시 없음)."""
    old = _recent_record(5001, updated_at="2020-01-01T00:00:00+09:00")
    _seed(isolated_journal, [old])

    payload = {"records": [], "edit_id": None, "deleted_ids": [5001]}
    resp = asyncio.run(app.save_journal(_FakeRequest(payload)))
    body = _body(resp)
    assert body["journal"] == []


def test_deleted_ids_nonexistent_id_is_harmless(isolated_journal):
    """존재하지 않는 id를 deleted_ids로 보내도 조용히 무시(에러 없음)."""
    other = _recent_record(6001)
    _seed(isolated_journal, [other])

    payload = {"records": [other], "edit_id": None, "deleted_ids": [999999]}
    resp = asyncio.run(app.save_journal(_FakeRequest(payload)))
    body = _body(resp)
    assert body["ok"] is True
    assert 6001 in {r["id"] for r in body["journal"]}
