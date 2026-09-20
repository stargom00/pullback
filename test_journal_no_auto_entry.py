"""v5.274 — 일지 자동 진입 **전면 제거**(사용자 지시 2026-09-20).

[왜] 가격이 피벗에 닿으면 `updateTracking()`이 status를 'entered'로 바꾸고
`r.date`까지 오늘로 덮어썼다. 실제로 사지 않은 거래가 보유로 올라가 **R 통계를
오염**시키고, 등록일이 덮여 나중에 복구도 안 됐다.

[무엇을] 세 경로를 전부 없앴다:
  · `updateTracking()`의 pending → entered (가격 도달)
  · `_promoteConfirmCloseEntries()` (캘린더 확정가 승격)
  · `_archiveExpiredPending()` (대기 만료 자동 보관)
이제 상태 전환은 **사용자 클릭으로만** 일어난다. 도달 사실은 "피벗 도달 ↑"
배지로만 표시한다.

[관찰] `watch`는 원래도 전환 대상이 아니었다(`updateTracking` 진입부에서
`continue`). 조사에서 확인했고, 되돌아가지 않도록 여기 고정한다.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

INDEX = Path(__file__).resolve().parent / "static" / "index.html"
TEXT = INDEX.read_text(encoding="utf-8")


def _code_only(src: str) -> str:
    """`//` 주석 줄을 걷어낸다.

    주석까지 검사하면 **변경 이력을 적은 주석**에 걸려 오탐한다 — 실제로
    "예전엔 status='entered'로 바꿨다"는 설명에 걸렸다(CLAUDE.md: 검사 범위를
    실행 코드로 좁힐 것).
    """
    out = []
    for line in src.splitlines():
        t = line.lstrip()
        if t.startswith("//"):
            continue
        out.append(line.split("//")[0] if "//" in line and "://" not in line else line)
    return "\n".join(out)


def _fn(name: str) -> str:
    """중괄호 깊이로 함수 본문을 정확히 잘라낸다(정규식은 중첩에 안전하지 않다)."""
    for decl in (f"async function {name}(", f"function {name}("):
        i = TEXT.find(decl)
        if i != -1:
            break
    assert i != -1, f"{name}을 못 찾음"
    b = TEXT.index("{", i)
    d = 0
    for k in range(b, len(TEXT)):
        if TEXT[k] == "{":
            d += 1
        elif TEXT[k] == "}":
            d -= 1
            if d == 0:
                return TEXT[i:k + 1]
    raise AssertionError(name)


def test_tracking_never_assigns_entered():
    """**이번 수정의 핵심.** `updateTracking()` 안에 status를 entered로 바꾸는
    대입이 하나도 없어야 한다.

    `in` 검사가 아니라 **개수 0**으로 고정한다 — 존재 검사는 대입이 여러 곳일 때
    하나만 지워도 통과한다(CLAUDE.md 사보타주 패턴).
    """
    src = _code_only(_fn("updateTracking"))
    hits = re.findall(r"""status\s*=\s*['"]entered['"]""", src)
    assert len(hits) == 0, f"자동 진입 대입이 남아 있다: {hits}"


def test_tracking_does_not_overwrite_the_registration_date():
    """`r.date = 오늘`은 등록일을 지워 복구를 막았다 — 같이 사라져야 한다."""
    src = _code_only(_fn("updateTracking"))
    assert "r.date = kstStr(today)" not in src, "등록일 덮어쓰기가 남아 있다"


def test_the_two_auto_promotion_functions_are_gone():
    for name in ("_promoteConfirmCloseEntries", "_archiveExpiredPending"):
        assert f"async function {name}(" not in TEXT, f"{name}이 아직 있다"
        assert f"await {name}(" not in TEXT, f"{name} 호출이 남아 있다"


def test_watch_still_short_circuits_before_any_transition():
    """관찰은 원래도 전환 대상이 아니었다 — 되돌아가면 안 된다."""
    src = _code_only(_fn("updateTracking"))
    i = src.index("=== 'watch'")
    head = src[:i]
    assert "status = 'entered'" not in head
    # watch 분기가 continue로 끝나는지(전환 로직에 도달하지 않는지)
    block = src[i:i + 500]
    assert "continue;" in block, block[:200]


def test_reached_badge_replaces_the_auto_entry():
    """도달 사실은 표시로만 남는다 — 표시까지 없으면 사용자가 알 길이 없다."""
    assert "피벗 도달 ↑" in TEXT, "도달 표시가 없다"
    src = _fn("updateTracking")
    assert "pivot_reached" in src, "도달 여부를 기록하지 않는다"


def test_pending_still_tracks_price_for_display():
    """자동 전환만 없앤 것이지 가격 추적까지 끈 게 아니다."""
    src = _fn("updateTracking")
    assert "r.last_price" in src


def test_archived_label_says_expiry_not_observation():
    """`archived`는 **pending 만료**에서만 생기는데 라벨이 "관찰종료"였다 —
    관찰(watch)과 무관해 이름이 의미와 어긋났다."""
    assert "🗄️대기만료" in TEXT
    assert "🗄️관찰종료" not in TEXT, "옛 배지 라벨이 남아 있다"
    # `관찰종료(무산)` 같은 closed_reason은 **진짜 watch 레코드**의 사유라
    # 그대로 둔다 — archived(=pending 만료) 배지만 바꾼 것이다.


def test_null_status_is_surfaced_in_the_ui():
    """status가 빈 레코드는 폴백으로 조용히 진입처럼 보였다(프로덕션 2건)."""
    assert "상태 미기록" in TEXT


def test_server_excludes_null_status_from_bot_alerts():
    """봇 R 알림 오염의 실제 경로 — 빈 status를 열린 포지션으로 세면 안 된다."""
    import app
    src = Path(app.__file__).read_text(encoding="utf-8")
    i = src.index("async def watch_positions")
    body = src[i:i + 3000]
    assert 'if status != "entered":' in body, body[:400]
    assert 'if status:' not in body, "빈 status를 통과시키는 분기가 남아 있다"
