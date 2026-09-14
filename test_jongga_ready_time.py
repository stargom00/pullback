"""v5.260 — 종가베팅 "준비 완료" 시각 문턱이 KST 15:20으로 고정돼 있는가.

[버그] v5.236에서 이 문턱이 리터럴 `18*60+20`(18:20)이었다. 비교 대상
`_now_hm`은 `datetime.now(KST)`에서 나온 **KST**인데 문턱만 18:20이라
의도(동시호가 직전 15:20 KST)보다 정확히 **3시간** 늦었다 — 3시간은 이 개발
머신의 NZST − KST 차이다(CLAUDE.md "NZST 시계 함정"). NZST 시계를 보고 적은
값이 KST 비교에 들어간 것.

[증상] 종가베팅 후보가 이미 나온 15:00 KST 이후에도 "종가베팅은 18:20 이후"
안내가 3시간 더 떠 있었다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app  # noqa: E402


def test_threshold_is_kst_1520():
    assert app.JONGGA_READY_HM == 15 * 60 + 20, (
        f"문턱이 KST 15:20이 아니다: {app.JONGGA_READY_HM // 60}:{app.JONGGA_READY_HM % 60:02d}")
    assert app.JONGGA_READY_LABEL == "15:20"


def test_threshold_is_not_the_nzst_reading():
    """18:20(= NZST 표기)으로 되돌아가면 실패해야 한다."""
    assert app.JONGGA_READY_HM != 18 * 60 + 20, "NZST 시각이 다시 들어갔다"
    # 3시간 어긋남 일반형 — KST+3h 어떤 값도 아니어야 한다
    assert app.JONGGA_READY_HM % (24 * 60) != (15 * 60 + 20 + 3 * 60), "KST+3h(NZST) 값이다"


def test_label_matches_number():
    """숫자와 화면 문구가 갈리면 사용자가 보는 시각이 코드와 달라진다."""
    h, m = divmod(app.JONGGA_READY_HM, 60)
    assert app.JONGGA_READY_LABEL == f"{h}:{m:02d}"


def test_no_literal_1820_threshold_left_in_code():
    """실행 코드에 18:20 리터럴 문턱이 남아 있으면 안 된다.
    (모듈 docstring의 변경 이력에는 인용으로 남아 있으므로 코드 영역만 본다.)"""
    src = Path(app.__file__).read_text(encoding="utf-8")
    code = src[src.index('"""', src.index('"""') + 3) + 3:]
    assert "18 * 60 + 20" not in code, "18:20 리터럴 문턱이 살아 있다"


def test_reason_uses_the_constant():
    """안내 문구가 상수를 쓰는지 — 문자열을 따로 하드코딩하면 또 갈린다."""
    src = Path(app.__file__).read_text(encoding="utf-8")
    i = src.index("_reason_parts.append(f\"종가베팅")
    line = src[i:src.index("\n", i)]
    assert "JONGGA_READY_LABEL" in line, line


def test_threshold_is_before_market_close():
    """15:20은 스냅샷 창(14:40~15:00) 직후 · 정규장 마감(15:30) 전.

    v5.264에서 **진입** 시각은 19:50~20:00으로 옮겼지만, 이 문턱은 진입이 아니라
    **후보 등장 시각**이라 그대로다(아래 test_ready_threshold_stays_at_candidate_time).
    """
    assert app.JONGGA_READY_HM < 15 * 60 + 30
    # 스케줄러 스냅샷 창(14:40~15:00 KST) 이후이기도 해야 한다
    assert app.JONGGA_READY_HM >= 15 * 60


# ══════════════════════════════════════════════════════════════════════
# v5.264 — 진입 시각이 애프터마켓(19:50~20:00)으로 이동
# ══════════════════════════════════════════════════════════════════════
from datetime import datetime, timedelta, timezone  # noqa: E402

KST = timezone(timedelta(hours=9))


def _state(s):
    return app._jongga_session_state(datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=KST))


def test_session_active_window_extends_to_after_market_close():
    """활성 창이 15:30에 끝나면 진입 시각(19:50~20:00)을 못 덮는다."""
    assert _state("2026-09-14 14:40")["state"] == "active"
    assert _state("2026-09-14 15:31")["state"] == "active", "구 경계 15:30에서 끊겼다"
    assert _state("2026-09-14 19:55")["state"] == "active", "진입 시각인데 비활성이다"
    assert _state("2026-09-14 20:00")["state"] == "after"


def test_session_label_points_at_after_market_not_closing_auction():
    lab = _state("2026-09-14 19:55")["label"]
    assert "19:50~20:00" in lab and "애프터마켓" in lab, lab
    assert "동시호가" not in lab, "15:20 동시호가 문구가 남아 있다"


def test_before_window_unchanged():
    assert _state("2026-09-14 10:00")["state"] == "before"
    assert "14:40" in _state("2026-09-14 10:00")["label"], "후보 선정 시각은 그대로여야 한다"


def test_weekend_still_after():
    assert _state("2026-09-12 19:55")["state"] == "after"


def test_ready_threshold_stays_at_candidate_time_not_entry_time():
    """진입이 19:50로 바뀌어도 이 문턱은 **후보 등장 시각**이라 15:20 유지.

    19:50으로 올리면 15:00~19:50 사이에 후보가 화면에 떠 있는데도
    "아직 없다"는 안내가 뜨는 모순이 생긴다.
    """
    assert app.JONGGA_READY_HM == 15 * 60 + 20
    assert app.JONGGA_READY_HM < 19 * 60 + 50


def test_reason_text_says_candidates_not_entry():
    """문구가 진입 시각으로 오해되면 안 된다."""
    src = Path(app.__file__).read_text(encoding="utf-8")
    i = src.index("_reason_parts.append(f\"종가베팅")
    line = src[i:src.index("\n", i)]
    assert "후보는" in line, line


def test_docs_record_the_confirmed_close_definition():
    root = Path(app.__file__).parent
    bt = (root / "docs" / "kr_jongga_betting_backtest.md").read_text(encoding="utf-8")
    assert "확정 (2026-09-15" in bt and "248,500" in bt
    assert "정의 미확정" not in bt, "미확정 콜아웃이 남아 있다"
    guide = (root / "GUIDE.md").read_text(encoding="utf-8")
    assert "19:50~20:00 애프터마켓" in guide
    assert "15:20 동시호가 전 진입용" not in guide
