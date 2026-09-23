"""v5.285 — /api/apiguard/status가 배포된 VERSION을 함께 돌려준다.

[왜] 2026-09-24 v5.284 배포 확인에서, 토큰(`API_READ_TOKEN`) 헤더로 열리는
경로 중 `"version"`을 주는 엔드포인트가 **하나도 없다**는 걸 전수 확인했다
(`/api/jongga/candidates`는 주는 분기가 있지만 스냅샷 없는 날엔 version 없는
조기 반환으로 빠진다). 페이지 경로는 전부 `/login` 302라, 배포된 버전 문자열을
확인하려면 매번 사람이 브라우저를 열어야 했다.

이 경로는 이미 `_BOT_READ_EXACT_PATHS`에 있고 읽기 전용이라 성격이 안 바뀐다.
"""
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import app  # noqa: E402


def test_status_includes_the_deployed_version(monkeypatch):
    monkeypatch.setattr(app, "APP_PASSWORD", "", raising=False)   # 게이트 끔
    r = TestClient(app.app).get("/api/apiguard/status")
    assert r.status_code == 200
    body = r.json()
    assert body.get("version") == app.VERSION, body


def test_the_guard_fields_are_still_there(monkeypatch):
    """version을 얹느라 기존 필드를 밀어내면 얼마냐봇 폴링이 깨진다."""
    monkeypatch.setattr(app, "APP_PASSWORD", "", raising=False)
    body = TestClient(app.app).get("/api/apiguard/status").json()
    for k in ("date", "count", "limit", "warn_threshold", "warned", "blocked", "pending_alert"):
        assert k in body, k


def test_the_path_is_still_token_readable():
    """토큰으로 못 읽으면 이 변경의 목적(로그인 없이 버전 확인) 자체가 사라진다."""
    assert "/api/apiguard/status" in app._BOT_READ_EXACT_PATHS
