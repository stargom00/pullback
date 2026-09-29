"""v5.294 — /api/calendar의 last_scan(홈 헤더 띠 스캔 메타).

홈만 열면 헤더 스캔 메타가 비어 있던 문제: /api/calendar에 스캔 시각·종목수가
없었다. last_scan은 **이미 메모리에 있는** /api/scan 결과 캐시(_cache)만 읽는다
(새 스캔·fetch 금지) — 캐시가 없으면 null이고 화면은 빈칸 그대로.

사보타주 확인(2026-09-30): get_calendar 응답에서 "last_scan" 키를 지우면
test_key_present_and_null_without_cache·test_picks_most_recent_scan FAIL — 원복.
"""
import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from test_fetch_market_data_all_merge import mocked_env  # noqa: F401  (fixture 재사용)

import app

IDX = Path(__file__).resolve().parent / "static" / "index.html"


def _calendar(monkeypatch, cache):
    monkeypatch.setattr(app, "_cache", cache)
    r = asyncio.run(app.get_calendar())
    assert r.status_code == 200
    return json.loads(r.body)


def test_key_present_and_null_without_cache(mocked_env, monkeypatch):
    body = _calendar(monkeypatch, {})
    assert "last_scan" in body
    assert body["last_scan"] is None


def test_picks_most_recent_scan(mocked_env, monkeypatch):
    cache = {
        "all:pullback": {"generated_at": "2026-09-29 18:39:02", "scanned": 3266, "fetched": 3241,
                         "ts": 200.0, "market": "all", "mode": "pullback", "hits": []},
        "all:imminent": {"generated_at": "2026-09-29 09:10:00", "scanned": 3266, "fetched": 3200,
                         "ts": 100.0, "market": "all", "mode": "imminent", "hits": []},
        # generated_at 없는 항목(종가베팅 대기 뷰 등)은 무시
        "kr:jongga": {"generated_at": None, "scanned": 0, "ts": 999.0},
    }
    ls = _calendar(monkeypatch, cache)["last_scan"]
    assert ls == {"generated_at": "2026-09-29 18:39:02", "scanned": 3266, "fetched": 3241,
                  "market": "all", "mode": "pullback"}


def test_helper_never_triggers_a_scan():
    import inspect
    src = inspect.getsource(app._last_scan_meta)
    for bad in ("run_scan", "_fetch", "await ", "requests."):
        assert bad not in src, bad


def _extract(name):
    src = IDX.read_text(encoding="utf-8")
    i = src.index(f"function {name}(")
    b = src.index("{", i)
    d = 0
    for k in range(b, len(src)):
        d += {"{": 1, "}": -1}.get(src[k], 0)
        if d == 0:
            return src[i:k + 1]
    raise AssertionError(name)


@pytest.mark.skipif(shutil.which("node") is None, reason="node 미설치")
def test_home_uses_the_same_display_function():
    """홈 경로도 스캔 탭과 같은 scanMetaText()를 쓴다(사본 금지) — 실제로 실행해 본다."""
    src = IDX.read_text(encoding="utf-8")
    enter = _extract("onEnterCalendarTab")
    assert "scanMetaText(_calendarData.last_scan)" in enter
    assert src.count("function scanMetaText(") == 1
    js = _extract("scanMetaText") + """
console.log(JSON.stringify({
  full: scanMetaText({generated_at:'2026-09-29 18:39:02', scanned:3266, fetched:3241}),
  none: scanMetaText(null)}));"""
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["full"] == "스캔 09-29 18:39 KST · 3,241 / 3,266종목"
    assert got["none"] == ""
