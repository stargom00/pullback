"""v5.263 — KR 애프터마켓(16:00~20:00 KST) 대응 경계 상수.

[배경] 2026-09-14 KRX 애프터마켓 도입. 실측(19:34→19:35, 75초 간격 재조회)에서
naver siseJson **일봉의 Close·Volume이 애프터마켓 중 계속 갱신**됐다
(삼성전자 거래량 +5,417주, SK하이닉스 종가 +1,000원).
→ **15:30 종가는 더 이상 그날 봉의 끝이 아니다.**

그래서 "KR 마감 확정" 시각을 20:10(애프터 종료 + 여유 10분)으로 옮겼다.
이 테스트는 그 값이 되돌아가지 않도록 고정한다.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app  # noqa: E402

KST = timezone(timedelta(hours=9))


def test_constant_is_2010_kst():
    assert app.KR_CLOSE_CONFIRMED_HM == 20 * 60 + 10, (
        f"{app.KR_CLOSE_CONFIRMED_HM // 60}:{app.KR_CLOSE_CONFIRMED_HM % 60:02d}")


def test_old_boundaries_are_gone():
    """15:40(구 EOD)·19:00(구 캘린더 전환)으로 되돌아가면 실패."""
    assert app.KR_CLOSE_CONFIRMED_HM != 15 * 60 + 40, "구 EOD 경계 15:40이 부활했다"
    assert app.KR_CLOSE_CONFIRMED_HM != 19 * 60, "구 캘린더 경계 19:00이 부활했다"
    src = Path(app.__file__).read_text(encoding="utf-8")
    code = src[src.index('"""', src.index('"""') + 3) + 3:]
    assert "hm >= 15 * 60 + 40" not in code, "15:40 리터럴 경계가 코드에 남아 있다"
    assert "hm < 19 * 60" not in code, "19:00 리터럴 경계가 코드에 남아 있다"


def test_after_market_is_not_treated_as_closed(monkeypatch):
    """애프터마켓 한복판(19:00 KST 평일)은 '마감 확정'이 아니어야 한다."""
    _freeze(monkeypatch, "2026-09-14 19:00")      # 월요일
    assert app._market_session_key("kr") is None


def test_after_market_end_is_treated_as_closed(monkeypatch):
    _freeze(monkeypatch, "2026-09-14 20:10")
    assert app._market_session_key("kr") == "2026-09-14"


def test_1540_is_no_longer_closed(monkeypatch):
    """구 경계 시각엔 아직 봉이 4시간 더 변한다 — 확정으로 보면 안 된다."""
    _freeze(monkeypatch, "2026-09-14 15:40")
    assert app._market_session_key("kr") is None


def test_calendar_keeps_kr_through_after_market(monkeypatch):
    """애프터 종료 전까지는 KR 카드를 유지한다."""
    for hhmm, want in (("2026-09-14 19:00", "kr"),      # 구 경계였던 시각
                       ("2026-09-14 20:09", "kr"),
                       ("2026-09-14 20:10", "us"),
                       ("2026-09-14 08:00", "kr")):
        _freeze(monkeypatch, hhmm)
        assert app._calendar_default_market_session() == want, hhmm


def test_weekend_still_confirmed(monkeypatch):
    """주말은 시각과 무관하게 확정(데이터가 안 바뀜) — 기존 동작 유지."""
    _freeze(monkeypatch, "2026-09-12 10:00")          # 토요일
    assert app._market_session_key("kr") is not None


def test_measurement_window_rule_updated_in_claude_md():
    """CLAUDE.md 측정 실행 창이 새 경계와 어긋나면 사람이 옛 창을 따른다."""
    md = (Path(app.__file__).parent / "CLAUDE.md").read_text(encoding="utf-8")
    assert "KST 20:10 이후 ~ 22:30" in md
    assert "안전 시각: KST 16:00 이후" not in md, "옛 창이 남아 있다"


def _freeze(monkeypatch, s: str):
    """datetime.now(KST)를 고정 — app 모듈이 쓰는 datetime만 바꾼다."""
    fixed = datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=KST)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz else fixed.replace(tzinfo=None)

    monkeypatch.setattr(app, "datetime", _DT)
