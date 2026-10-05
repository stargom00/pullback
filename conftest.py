"""전 테스트 공통 안전장치(v5.325).

저점 관찰 저장소(app.LP_WATCH_PATH)는 서버 시작·러너·수동 갱신이 쓴다. 테스트가 러너를 돌리다 실제 경로(로컬은 레포
루트, 운영은 /data)에 관찰 파일을 만든 일이 있었다(2026-10-05, v5.325 개발 중 — 레포 루트에 lowpoint_watch.json이
생겼다). 그래서 모든 테스트에서 이 경로를 그 테스트의 임시 폴더로 돌린다. 개별 테스트가 다시 지정해도 된다."""
import sys

import pytest


@pytest.fixture(autouse=True)
def _isolate_lowpoint_watch_store(tmp_path, monkeypatch):
    app = sys.modules.get("app")
    if app is not None and hasattr(app, "LP_WATCH_PATH"):
        monkeypatch.setattr(app, "LP_WATCH_PATH", str(tmp_path / "lowpoint_watch.json"))
        monkeypatch.setattr(app, "LP_WATCH_DELETE_LOG_PATH", str(tmp_path / "lowpoint_watch_deletions.log"))
    yield
