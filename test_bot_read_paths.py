"""v5.269 — 봇 읽기 토큰으로 열어둔 경로(`_BOT_READ_EXACT_PATHS`).

이 집합은 **세션 로그인 없이 데이터를 내보내는** 목록이라, 한 번 잘못 넣으면
조용히 열린 채로 남는다. 그래서 세 가지를 강제한다:

1. 목록의 모든 경로가 **실제로 등록된 라우트**인가(오타/삭제된 경로가 남으면
   목록만 보고 "열려 있다"고 오판한다).
2. 목록의 모든 경로에 **GET 핸들러가 있는가** — `_is_bot_read_path`가 GET만
   통과시키므로, GET이 없는 경로를 넣는 건 무의미하고 착각을 부른다.
3. 열어둔 핸들러가 **쓰기를 하지 않는가**. 읽기 전용이라는 게 이 목록의 전제다
   (CLAUDE.md 보안 메모: `_SYNC_TOKEN_GATED_PATHS`와 달리 게이트 우회가 아니라
   토큰 + 읽기 전용이라는 점이 안전의 근거).
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app  # noqa: E402

SRC = Path(app.__file__).read_text(encoding="utf-8")
WRITE = re.compile(r"\b(_save_\w+|json\.dump|shutil\.|os\.remove|os\.replace)\b|open\([^)]*['\"][wa]")


def _handlers():
    """{path: 핸들러 함수명} — GET 라우트만."""
    out = {}
    for m in re.finditer(r'@app\.get\("([^"]+)"\)\s*\n(?:async )?def (\w+)', SRC):
        out[m.group(1)] = m.group(2)
    return out


def test_every_listed_path_has_a_get_route():
    h = _handlers()
    missing = [p for p in app._BOT_READ_EXACT_PATHS if p not in h]
    assert not missing, f"GET 라우트가 없는 경로가 목록에 있다: {missing}"


def test_listed_paths_are_registered_on_the_app():
    paths = {r.path for r in app.app.routes if hasattr(r, "path")}
    missing = [p for p in app._BOT_READ_EXACT_PATHS if p not in paths]
    assert not missing, f"앱에 등록되지 않은 경로: {missing}"


# 이미 열려 있던 경로 중 **쓰기가 있는 것**. 숨기지 않고 여기 적어둔다.
# (v5.269 작성 중 이 테스트가 발견했다 — 그 전까지 아무도 몰랐다.)
#
#   /api/market/gate → `_save_index_gate_cache(_index_last_good)`
#     지수 조회 실패 시 쓰는 "직전 정상값" 캐시를 파일로 남긴다. 사용자
#     데이터가 아니라 **서버가 스스로 만든 캐시**이고, 요청 본문·쿼리가 그
#     내용에 전혀 반영되지 않는다(값의 출처는 지수 fetch 결과뿐). 그래서
#     토큰 보유자가 이 경로로 상태를 조작할 수단은 없다 — 그대로 둔다.
#
# **새로 생긴 쓰기는 여기 추가하지 말고 먼저 의심할 것.** 이 목록이 늘어난다는
# 건 "읽기 전용"이라는 개방 근거가 약해진다는 뜻이다.
KNOWN_WRITERS = {"/api/market/gate": "_save_index_gate_cache"}


def test_listed_handlers_do_not_write():
    """열어둔 경로가 쓰기를 하면 '읽기 전용'이라는 전제가 깨진다.

    알려진 예외(KNOWN_WRITERS)만 통과시키고, **그 예외도 예상한 함수를 실제로
    부르는지** 확인한다 — 이름만 적어두고 내용이 바뀌면 의미가 없다.
    """
    h = _handlers()
    bad = {}
    for p in app._BOT_READ_EXACT_PATHS:
        fn = h[p]
        i = SRC.index(f"def {fn}(")
        # 함수 끝 = 다음 **최상위** 정의. `@app.`만 찾으면 중간의 일반 함수들을
        # 통째로 삼켜 엉뚱한 함수의 쓰기가 이 핸들러 것으로 보고된다
        # (작성 중 실제로 /api/watch/positions가 그렇게 오탐됐다).
        m = re.search(r"\n(?:@app\.|def |async def |[A-Z_]+ = )", SRC[i + 1:])
        body = SRC[i:i + 1 + m.start()] if m else SRC[i:]
        hits = [l.strip() for l in body.splitlines() if WRITE.search(l)]
        if not hits:
            continue
        expected = KNOWN_WRITERS.get(p)
        if expected and all(expected in l for l in hits):
            continue
        bad[p] = hits[:3]
    assert not bad, f"쓰기로 보이는 호출이 있다: {bad}"


def test_known_writers_are_still_accurate():
    """예외 목록이 낡으면 조용히 통과 도장이 된다 — 실제로 그 쓰기가 있는가."""
    h = _handlers()
    for p, fn_name in KNOWN_WRITERS.items():
        assert p in app._BOT_READ_EXACT_PATHS, f"목록에 없는 경로의 예외: {p}"
        i = SRC.index(f"def {h[p]}(")
        m = re.search(r"\n(?:@app\.|def |async def |[A-Z_]+ = )", SRC[i + 1:])
        body = SRC[i:i + 1 + m.start()] if m else SRC[i:]
        assert fn_name in body, f"{p}가 더는 {fn_name}을 안 부른다 — 예외를 지울 것"


def test_the_two_new_paths_write_nothing_at_all():
    """v5.269에서 **새로 여는** 둘은 예외 없이 순수 읽기여야 한다."""
    h = _handlers()
    for p in ("/api/jongga/forward", "/api/paper-track"):
        assert p not in KNOWN_WRITERS, f"{p}에 쓰기 예외가 붙었다"
        i = SRC.index(f"def {h[p]}(")
        m = re.search(r"\n(?:@app\.|def |async def |[A-Z_]+ = )", SRC[i + 1:])
        body = SRC[i:i + 1 + m.start()] if m else SRC[i:]
        assert not WRITE.search(body), f"{p}가 쓰기를 한다"


def test_only_get_passes():
    for p in app._BOT_READ_EXACT_PATHS:
        assert app._is_bot_read_path("GET", p) is True, p
        for m in ("POST", "PUT", "DELETE", "PATCH"):
            assert app._is_bot_read_path(m, p) is False, (m, p)


def test_forward_verification_paths_are_open():
    """v5.269(사용자 지시) — 이 둘이 빠지면 상태 확인이 다시 401로 막힌다."""
    for p in ("/api/jongga/forward", "/api/paper-track"):
        assert p in app._BOT_READ_EXACT_PATHS, p


def test_gate_bypass_set_is_untouched():
    """읽기 개방과 **게이트 우회**를 섞지 않는다 — 우회 집합은 그대로여야 한다."""
    assert app._SYNC_TOKEN_GATED_PATHS == {
        "/api/positions/sync", "/api/positions/sync_error"}, app._SYNC_TOKEN_GATED_PATHS
    overlap = app._SYNC_TOKEN_GATED_PATHS & app._BOT_READ_EXACT_PATHS
    assert not overlap, f"우회 경로를 읽기 목록에도 넣었다: {overlap}"
