"""v5.268 — KR 스캔 조회 창 730일 → 1900일.

왜 넓혔나: 🔺 ABC 탭의 기준선이 MA200 → MA600으로 바뀌었는데, 730일은
**487봉**이라 MA600이 한 봉도 안 나온다(실측). "마지막 ~130봉은 유효할 것"이라는
초기 추정은 봉↔일 혼동이었다 — 730은 일수고 봉으로는 487이다.

이 파일이 지키는 것:
  1. 창이 MA600을 **실제로 커버**하는가(수식이 아니라 실제 봉 수로).
  2. 창을 바꿀 때 `app._CACHE_NS`를 같이 올렸는가. 안 올리면 **옛 창으로 만든
     디스크 캐시가 조용히 로드**돼, 코드는 1900일인데 데이터는 730일인 상태가
     된다(CLAUDE.md: 주석·경고가 아니라 실패로 만든다).
  3. 창 상수가 한 곳인가 — 호출부에 `days=730` 리터럴이 남아 있으면 그 경로만
     옛 창으로 남는다.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import abc_screener  # noqa: E402
import app  # noqa: E402
import naver_kr  # noqa: E402

ROOT = Path(__file__).resolve().parent
SRC = (ROOT / "naver_kr.py").read_text(encoding="utf-8")


def test_window_covers_the_abc_baseline():
    """봉 환산으로 기준선 기간 + 여유를 덮는가.

    거래일은 달력일의 약 68.5%다(실측: 1900일 → 1,275봉). 수식으로만 두지 않고
    아래 test_real_fetch_has_enough_bars가 실제 조회로 한 번 더 확인한다.
    """
    bars = naver_kr.KR_SCAN_DAYS * 0.685
    assert bars >= abc_screener.ABC_CONFIG["gate_ma_period"] * 1.5, (
        f"{naver_kr.KR_SCAN_DAYS}일 ≈ {bars:.0f}봉 — MA600에 여유가 없다")


def test_old_window_would_not_have_worked():
    """730일이 왜 부족했는지를 테스트로 남긴다(다시 줄이려는 시도를 막는다)."""
    assert 730 * 0.685 < abc_screener.ABC_CONFIG["gate_ma_period"], (
        "730일로 MA600이 된다면 이 확대의 근거가 사라진다 — 재검토할 것")


def test_cache_namespace_moved_with_the_window():
    """**창을 바꿨으면 네임스페이스를 올렸어야 한다.**

    rs7은 730일 시절의 네임스페이스다. 창을 넓히고 이걸 그대로 두면 Railway
    볼륨의 옛 pkl(487봉)이 그대로 로드돼 ABC 탭이 전부 "MA600 불가"가 된다.
    """
    assert app._CACHE_NS != "rs7", "730일 시절 네임스페이스가 그대로다"
    src = Path(app.__file__).read_text(encoding="utf-8")
    i = src.index('_CACHE_NS = ')
    note = src[i:i + 600]
    assert "KR_SCAN_DAYS" in note or "1900" in note, (
        "왜 범프했는지가 상수 옆에 없다 — 다음 사람이 되돌린다")


def test_window_is_a_single_constant():
    """호출부에 옛 창 리터럴이 남아 있으면 그 경로만 730일로 남는다."""
    body = SRC[SRC.index("def fetch(ticker"):]
    assert "days=KR_SCAN_DAYS" in body, "fetch()가 상수를 안 쓴다"
    assert "days=730" not in body, "730 리터럴이 살아 있다"


def test_fetch_history_default_is_untouched():
    """`fetch_history`의 기본값 730은 **그대로 둔다**.

    종가베팅 백필(`_jongga_bars`)·디버그 등 다른 호출부가 이 기본값을 쓰는데,
    그쪽까지 1900일로 끌어올리면 ABC와 무관한 경로의 비용이 3배가 된다.
    넓히는 건 **스캔 번들 경로 하나**다(사용자 지시: "다른 탭 무영향").
    """
    import inspect
    sig = inspect.signature(naver_kr.fetch_history)
    assert sig.parameters["days"].default == 730


def test_real_fetch_has_enough_bars():
    """실제 조회 — 수식이 아니라 봉 수로 확인한다(벤더가 상한을 걸 수도 있다)."""
    import pytest
    try:
        df = naver_kr.fetch("005930.KS")
    except Exception as e:
        pytest.skip(f"조회 실패(망): {e}")
    if df is None or df.empty:
        pytest.skip("빈 응답")
    need = abc_screener._min_bars()
    assert len(df) >= need, f"{len(df)}봉 — {need}봉이 필요한데 벤더가 덜 준다"
    ma = df["Close"].rolling(abc_screener.ABC_CONFIG["gate_ma_period"]).mean()
    assert int(ma.notna().sum()) > 0, "MA600이 한 봉도 안 나온다"
