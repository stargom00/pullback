"""positions_summary의 stop_suggested 누락 버그 (v5.239, 사용자 지시).

배경: 222800.KQ(심텍)·AVGO 둘 다 positions_meta.json에 손절 항목이
없어 get_positions()가 close-ATR×1.5를 임시 손절가로 대입하고
stop_suggested=True를 세운다(app.py _one()). r_progress/dist_to_stop_pct는
이 임시값으로도 정상 계산되는데(계산 자체는 유효한 참고값), 캘린더의
positions_summary.items를 만드는 코드가 stop_suggested 필드를 빼고
옮겨서 — 소비처가 "확정 손절가로 계산된 값"인지 "미입력이라 임시값으로
계산된 값"인지 구분할 방법이 없었다(open_risk만 이 플래그를 봐서 0으로
정확히 빠지는 것과 비대칭).

_positions_summary_from_body()(app.py) — get_calendar() 안에 있던 이
변환 로직을 이름 있는 함수로 뽑은 것(_price_basis_fields()와 동일
원칙) — 을 직접 테스트한다. get_calendar() 전체를 호출하는 시도는
journal/macro_calendar 등 무관한 의존성 때문에 응답 없이 멈춰 실패함을
직접 확인했다(무거운 통합 호출 대신 단위 테스트로 전환한 이유)."""
import app


def _fake_position(ticker, stop_suggested, r_progress=2.53, dist_to_stop_pct=10.97, market="KR"):
    return {
        "ticker": ticker, "name": "심텍" if ticker == "222800.KQ" else None, "market": market,
        "r_progress": r_progress, "dist_to_stop_pct": dist_to_stop_pct,
        "stop_suggested": stop_suggested,
    }


def test_stop_suggested_propagates_to_summary_items():
    """★ 핵심 산출물. stop_suggested=True인 포지션(실측 222800.KQ 재현)이
    positions_summary.items에 그 플래그를 그대로 실어 보내는지."""
    body = {
        "positions": [_fake_position("222800.KQ", stop_suggested=True)],
        "summary": {"open_risk": {"KRW": 0.0, "USD": 0.0}, "positions_missing_stop": 1},
    }
    result = app._positions_summary_from_body(body)
    assert result is not None
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert "stop_suggested" in item, "stop_suggested 필드 자체가 응답에 없음 — 원래 버그 그대로"
    assert item["stop_suggested"] is True


def test_real_stop_shows_false():
    """대조군 — 사용자가 실제로 손절을 입력한 포지션은 stop_suggested=False로
    내려가야 한다(너무 공격적으로 전부 True 처리하는 회귀 방지)."""
    body = {
        "positions": [_fake_position("NVDA", stop_suggested=False, market="US")],
        "summary": {"open_risk": {"KRW": 0.0, "USD": 500.0}, "positions_missing_stop": 0},
    }
    result = app._positions_summary_from_body(body)
    assert result["items"][0]["stop_suggested"] is False


def test_calculations_unchanged():
    """계산 자체는 안 바뀌었는지 회귀 방지 — r_progress/dist_to_stop_pct/
    open_risk/missing_stop_count가 stop_suggested 추가 전과 동일하게
    나오는지(사용자 지시: "계산 자체는 바꾸지 마라")."""
    body = {
        "positions": [
            _fake_position("222800.KQ", stop_suggested=True, r_progress=2.53, dist_to_stop_pct=10.97),
            _fake_position("AVGO", stop_suggested=True, r_progress=5.77, dist_to_stop_pct=3.9, market="US"),
        ],
        "summary": {"open_risk": {"KRW": 0.0, "USD": 0.0}, "positions_missing_stop": 2},
    }
    result = app._positions_summary_from_body(body)
    assert result["missing_stop_count"] == 2
    assert result["open_risk"] == {"KRW": 0.0, "USD": 0.0}
    r_by_ticker = {it["ticker"]: it["r_progress"] for it in result["items"]}
    d_by_ticker = {it["ticker"]: it["dist_to_stop_pct"] for it in result["items"]}
    assert r_by_ticker == {"222800.KQ": 2.53, "AVGO": 5.77}
    assert d_by_ticker == {"222800.KQ": 10.97, "AVGO": 3.9}


def test_empty_positions_returns_none():
    """회귀 방지 — 포지션이 아예 없으면 기존과 동일하게 None."""
    assert app._positions_summary_from_body({"positions": [], "summary": {}}) is None
