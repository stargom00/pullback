"""버전 동기화 검사 (v5.198, 사용자 지시) — app.py의 VERSION과
static/index.html에 하드코딩된 첫 화면 배지(#verBadge)가 어긋나면 실패.

배경: verBadge는 페이지 첫 렌더 시 정적으로 박힌 값이고, JS가 API 응답의
data.version으로 나중에 덮어쓴다(static/index.html의 loadCalendar/load()
참고) — 하지만 그 전까지(또는 API 호출이 늦거나 실패하면) 사용자에게는
이 정적값이 그대로 보인다. v5.195~v5.197에서 VERSION은 계속 올렸는데
verBadge를 안 바꿔서 "배포됐는데 화면은 v5.194"로 보이는 혼란이 있었음
(실제로는 배포된 것과 무관하게 이 정적 텍스트가 안 바뀐 문제) — 재발 방지용.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _app_version() -> str:
    text = (ROOT / "app.py").read_text(encoding="utf-8")
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.M)
    assert m, "app.py에서 VERSION = \"...\" 를 못 찾음"
    return m.group(1)


def _ver_badge_version() -> str:
    text = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    m = re.search(r'id="verBadge">([^<]+)<', text)
    assert m, "static/index.html에서 id=\"verBadge\"> 를 못 찾음"
    return m.group(1).strip()


def test_version_matches_ver_badge():
    app_version = _app_version()
    badge_version = _ver_badge_version()
    assert app_version == badge_version, (
        f"app.py VERSION({app_version!r})과 static/index.html #verBadge({badge_version!r})가 "
        "어긋남 — 버전 올릴 때 둘 다 같이 바꿀 것."
    )
