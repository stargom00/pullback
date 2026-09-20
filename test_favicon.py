"""v5.273 — 🚀 파비콘.

[증상] 탭·북마크에 아이콘이 안 뜬다. "전엔 있었다"는 보고였지만 git 이력상
`<link rel="icon">`은 v4.1.1(09853af)에 추가된 뒤 **한 번도 바뀌지 않았다** —
사라진 게 아니라 처음부터 안 먹고 있었다.

[원인] 순서. 그 링크가 `<meta charset>`보다 **앞**에 있었다(실측: 이모지 바이트
136번, charset 선언 202번). 브라우저는 인코딩 선언을 만나기 전까지 폴백
인코딩으로 읽으므로 href 안의 🚀가 깨졌고, data URI 안의 SVG가 망가졌다.

[수정] ① charset을 head 첫 줄로 ② data URI를 퍼센트 인코딩해 **ASCII만** 남김
③ 브라우저가 직접 찍는 `/favicon.ico` 라우트 신설(없으면 `_auth_gate`가
/login으로 302시킨다 — 실측 확인).
"""
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app  # noqa: E402

INDEX = Path(__file__).resolve().parent / "static" / "index.html"
RAW = INDEX.read_bytes()
HEAD = RAW[:RAW.index(b"</head>")] if b"</head>" in RAW else RAW[:4000]
ROCKET = "\U0001F680"


def _link_uri() -> str:
    m = re.search(rb'<link rel="icon" href="data:image/svg\+xml,([^"]+)"', RAW)
    assert m, "index.html에 파비콘 링크가 없다"
    return m.group(1).decode("ascii")


def test_charset_is_declared_before_any_non_ascii():
    """**이번 버그의 핵심.** charset보다 앞에 비ASCII 바이트가 있으면 그 속성은
    폴백 인코딩으로 읽힌다 — 파비콘뿐 아니라 앞으로 추가될 어떤 속성도."""
    i = RAW.find(b"charset")
    assert i > 0, "charset 선언이 없다"
    before = RAW[:i]
    assert before.isascii(), (
        f"charset({i}바이트) 앞에 비ASCII가 있다: {before[-60:]!r}")


def test_favicon_uri_is_pure_ascii():
    """퍼센트 인코딩이라 charset 순서와 **무관**해야 한다(2차 방어)."""
    uri = _link_uri()
    assert uri.isascii(), uri
    assert ROCKET not in uri, "이모지가 생바이트로 들어 있다"
    assert "%F0%9F%9A%80" in uri, "🚀가 퍼센트 인코딩으로 안 들어 있다"


def test_decoded_uri_is_valid_svg_with_the_rocket():
    svg = urllib.parse.unquote(_link_uri())
    ET.fromstring(svg)                       # 깨졌으면 여기서 실패
    assert ROCKET in svg, svg


def test_route_is_registered():
    """브라우저는 링크 태그와 **별개로** /favicon.ico를 찍는다. 없으면
    `_auth_gate`가 /login으로 302시킨다(수정 전 실측: 302, 0바이트)."""
    assert "/favicon.ico" in [r.path for r in app.app.routes if hasattr(r, "path")]


def test_route_serves_svg():
    import asyncio
    r = asyncio.run(app.favicon())
    assert r.media_type == "image/svg+xml", r.media_type
    body = r.body.decode("utf-8")
    ET.fromstring(body)
    assert ROCKET in body


def test_link_and_route_serve_the_same_image():
    """두 곳에 **다른 그림**이 생기면 탭과 북마크가 어긋나고 아무도 모른다."""
    import asyncio
    from_link = urllib.parse.unquote(_link_uri())
    from_route = asyncio.run(app.favicon()).body.decode("utf-8")
    assert from_link == from_route, f"\n링크: {from_link}\n라우트: {from_route}"


def test_route_is_not_a_gate_bypass():
    """파비콘 때문에 로그인 게이트에 구멍을 내지 않았는지 — 데이터가 없는
    경로라도 우회 목록은 건드리지 않는다는 게 이번 수정의 전제다."""
    assert "/favicon.ico" not in app._SYNC_TOKEN_GATED_PATHS
    assert "/favicon.ico" not in app._BOT_READ_EXACT_PATHS


def test_no_stray_favicon_file_route():
    """정적 .ico 파일을 따로 두지 않는다(사용자 지시: "별도 파일 불필요")."""
    assert not (INDEX.parent / "favicon.ico").exists()
