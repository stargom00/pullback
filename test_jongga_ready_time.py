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
    i = src.index("_reason_parts.append(f\"종가베팅은")
    line = src[i:src.index("\n", i)]
    assert "JONGGA_READY_LABEL" in line, line


def test_threshold_is_before_market_close():
    """15:20은 KR 정규장 마감(15:30) 전이어야 한다 — 동시호가 직전이 의도."""
    assert app.JONGGA_READY_HM < 15 * 60 + 30
    # 스케줄러 스냅샷 창(14:40~15:00 KST) 이후이기도 해야 한다
    assert app.JONGGA_READY_HM >= 15 * 60
