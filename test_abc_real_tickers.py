"""v5.267 — 실종목 기준 케이스(사용자가 지목한 3종목).

합성 봉만으로는 "내가 만든 모양을 내가 맞히는" tautology가 된다(CLAUDE.md의
사보타주 실패 패턴 중 "합성 데이터"). 그래서 실제 봉으로 고정한다.

**날짜를 못 박는 이유**: C단계는 이벤트가 아니라 **상태**라 매일 바뀐다.
실제로 LS에코는 09-17에 C2 진돌이(MA200 +9.3%)였다가 09-18엔 +22.5%가 되며
C3 이탈로 넘어갔다 — "오늘" 기준으로 단언하면 이 테스트는 하루 만에 썩는다.
그래서 봉을 2026-09-17까지 자른 뒤 판정한다(자르는 것 자체가 그날 프로덕션이
본 데이터와 같은 상태다).

네트워크가 필요한 테스트라 조회 실패는 skip이다 — 도구/망 부재는 로직 결함과
다른 종류의 문제. 단, **조회는 됐는데 봉이 모자라면 skip이 아니라 실패**다
(그건 데이터 소스가 조용히 죽은 신호일 수 있다).
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import abc_screener as A  # noqa: E402
import app  # noqa: E402
import naver_kr  # noqa: E402

ASOF = pd.Timestamp("2026-09-17")


@pytest.fixture(scope="module")
def bars():
    out = {}
    for t in ("229640.KQ", "218410.KQ", "219130.KQ"):
        try:
            df = app._downcast(naver_kr.fetch_history(t, days=730))
        except Exception as e:                      # 망/벤더 문제 → skip
            pytest.skip(f"{t} 조회 실패: {e}")
        if df is None or df.empty:
            pytest.skip(f"{t} 빈 응답")
        d = df[df.index <= ASOF]
        assert len(d) >= A.ABC_CONFIG["min_bars"], (
            f"{t}: {ASOF.date()}까지 {len(d)}봉뿐 — 소스가 조용히 줄었는지 확인")
        assert d.index[-1].normalize() == ASOF, f"{t}: 마지막 봉이 {d.index[-1]}"
        out[t] = d
    return out


def test_ls_eco_is_c2_jindori(bars):
    """LS에코에너지 — 09-15 MA200 돌파, 09-16 거래량 폭발 → **진돌이**.

    사용자가 든 기준 케이스. 돌파봉 포함 3봉 중 최대 거래량으로 진돌이/가돌이를
    가르므로(N=3, 임의값), 거래량 봉이 돌파 **다음 봉**이어도 진돌이여야 한다.
    """
    r = A.analyze_abc(bars["229640.KQ"])
    assert r["verdict"] == "ABC", r
    assert r["c_stage"] == "C2 진돌이", r["c_stage"]
    bo = r["breakout"]
    assert bo["bars_ago"] == 2, bo               # 09-15 돌파
    assert bo["vol_bar_ago"] == 1, bo            # 거래량은 그 **다음** 봉
    assert bo["vol_mult"] > 10, bo


def test_rfhic_is_c0_waiting(bars):
    """RFHIC — MA200 아래에서 대기(−11.2%). C2로 새면 안 된다."""
    r = A.analyze_abc(bars["218410.KQ"])
    assert r["verdict"] == "ABC", r
    assert r["c_stage"] == "C0 대기", r["c_stage"]
    assert r["ma200_pct"] < 0, r["ma200_pct"]


def test_tiger_elec_is_excluded(bars):
    """타이거일렉 — MA200 +54%로 이미 멀리 간 종목. ABC가 아니다."""
    r = A.analyze_abc(bars["219130.KQ"])
    assert r["verdict"] != "ABC", r
    assert r["c_stage"] is None and r["breakout"] is None


def test_c_stage_is_a_state_that_moves_with_price(bars):
    """같은 종목이 며칠 뒤 다른 단계가 되는 게 **정상**이다.

    이 성질을 테스트가 부정해버리면(오늘 기준으로 못 박으면) 다음 사람이
    "버그"로 오해하고 상태 판정을 이벤트 판정으로 되돌릴 수 있다.
    """
    full = app._downcast(naver_kr.fetch_history("229640.KQ", days=730))
    later = A.analyze_abc(full)
    pinned = A.analyze_abc(bars["229640.KQ"])
    if full.index[-1].normalize() > ASOF:
        assert later["ma200_pct"] != pinned["ma200_pct"], "봉이 늘었는데 값이 같다"


def test_breakout_bar_is_dated_not_looked_ahead(bars):
    """룩어헤드 회귀 — 09-17까지만 본 판정이, 봉을 더 줘도 **같은 돌파봉**을
    가리켜야 한다(날짜 기준). 오늘의 MA200으로 과거를 재면 여기서 어긋난다."""
    d = bars["229640.KQ"]
    r = A.analyze_abc(d)
    idx_pinned = d.index[len(d) - 1 - r["breakout"]["bars_ago"]].normalize()

    d2 = d.iloc[:-1]                              # 09-16까지
    r2 = A.analyze_abc(d2)
    idx_short = d2.index[len(d2) - 1 - r2["breakout"]["bars_ago"]].normalize()
    assert idx_short == idx_pinned, (idx_short, idx_pinned)
