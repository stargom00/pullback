"""일지 삭제 경로 — v5.247의 deleted_ids 병합 가드는 v5.300에서 은퇴했다.

이력: v5.187 전체 배열 저장 + "최근 5분 갱신이면 되살림" 병합 가드 → 계속 추적 중인
레코드가 안 지워지는 사고(2026-09-11) → v5.247 deleted_ids로 가드 우회. 그러나 전체
배열 저장 자체가 옛 배열 하나로 서버 일지를 지울 수 있는 구조였다(v5.299 재현 60→3).
v5.300부터 삭제는 DELETE /api/journal/{id}로만 일어나고, 옛 body 형식(배열 / {records,
edit_id, deleted_ids})의 POST는 둘 다 410으로 아무것도 바꾸지 않는다. 새 동작의 본
검증은 test_journal_record_api.py — 여기선 "옛 형식으로 보낸 삭제·저장이 조용히 먹히지
않는다"만 고정한다.
"""
import asyncio
import json as _json

import pytest

import app


class _FakeRequest:
    def __init__(self, body):
        self._body = body
        self.headers = {}
        self.client = None

    async def json(self):
        return self._body


@pytest.fixture
def isolated_journal(monkeypatch, tmp_path):
    path = tmp_path / "journal_user.json"
    monkeypatch.setattr(app, "JOURNAL_PATH", str(path))
    monkeypatch.setattr(app, "JOURNAL_DELETE_LOG_PATH", str(tmp_path / "del.log"))
    path.write_text(_json.dumps([{"id": 1, "ticker": "A", "rev": 1}, {"id": 2, "ticker": "B", "rev": 1}]),
                    encoding="utf-8")
    app.load_journal()   # 필드 보정(updated_at 등) 저장을 먼저 끝내 두고 그 상태를 기준으로 비교
    return path


@pytest.mark.parametrize("body", [
    [{"id": 1, "ticker": "A"}],                                              # 옛 배열 형식
    {"records": [{"id": 1, "ticker": "A"}], "edit_id": None, "deleted_ids": [2]},  # v5.247 형식
    {"records": [], "probe": True},                                          # 2026-09-26 탐침 형태
])
def test_legacy_post_bodies_change_nothing(isolated_journal, body):
    before = isolated_journal.read_text(encoding="utf-8")
    r = asyncio.run(app.save_journal_disabled(_FakeRequest(body)))
    assert r.status_code == 410
    assert isolated_journal.read_text(encoding="utf-8") == before


def test_delete_endpoint_is_the_only_deletion(isolated_journal):
    r = asyncio.run(app.journal_delete(2, _FakeRequest(None)))
    assert r.status_code == 200
    left = _json.loads(isolated_journal.read_text(encoding="utf-8"))
    assert [x["id"] for x in left] == [1]


def test_delete_missing_id_is_harmless(isolated_journal):
    before = isolated_journal.read_text(encoding="utf-8")
    r = asyncio.run(app.journal_delete(999, _FakeRequest(None)))
    assert r.status_code == 200 and _json.loads(r.body)["already_gone"] is True
    assert isolated_journal.read_text(encoding="utf-8") == before
