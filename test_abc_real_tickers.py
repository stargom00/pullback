"""v5.267/v5.268 — 실종목 기준 케이스(사용자가 지목한 3종목).

합성 봉만으로는 "내가 만든 모양을 내가 맞히는" tautology가 된다(CLAUDE.md의
사보타주 실패 패턴 중 "합성 데이터"). 그래서 실제 봉으로 고정한다.

**날짜를 못 박는 이유**: C단계는 이벤트가 아니라 **상태**라 매일 바뀐다.
실제로 LS에코는 09-17에 C2 진돌이(MA200 +9.3%)였다가 09-18엔 +22.5%가 되며
C3 이탈로 넘어갔다 — "오늘" 기준으로 단언하면 이 테스트는 하루 만에 썩는다.
그래서 봉을 2026-09-17까지 자른 뒤 판정한다(자르는 것 자체가 그날 프로덕션이
본 데이터와 같은 상태다).

**v5.268에서 셋 다 C3로 쏠렸다가 v5.272에서 다시 갈렸다.** MA600 하나로 전부
판정하니 13종목 중 11개가 C3 이탈이 됐고(2.4년 평균이라 그간 오른 종목은
기준선이 한참 아래 남는다), 그래서 역할을 쪼갰다 — MA600은 후보 게이트,
MA200이 단계를 정한다.

    (2026-09-17 고정봉 기준)
    LS에코   게이트 +39.9% 통과 · MA200  +9.3% → C2 진돌이
    RFHIC   게이트 +66.0% 통과 · MA200 −11.2% → C0 대기(벽 아래)
    타이거일렉 다른 셋업(게이트 +152.1%)

    v5.268(MA600 단독)에선 **셋 다 C3 이탈**이었다. 두 선을 나누자 "장기 추세는
    돌았지만 중기 벽과의 거리는 제각각"이라는 실제 상태가 드러난다.

앵커는 **그때그때의 판정 구조로 다시 고정**한다 — 값이 바뀌는 것 자체는
설계 변경의 결과지 회귀가 아니다.

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
            # 스캔 번들과 같은 창으로 받아야 프로덕션과 같은 봉을 본다(v5.268).
            df = app._downcast(naver_kr.fetch_history(t, days=naver_kr.KR_SCAN_DAYS))
        except Exception as e:                      # 망/벤더 문제 → skip
            pytest.skip(f"{t} 조회 실패: {e}")
        if df is None or df.empty:
            pytest.skip(f"{t} 빈 응답")
        d = df[df.index <= ASOF]
        assert len(d) >= A._min_bars(), (
            f"{t}: {ASOF.date()}까지 {len(d)}봉뿐 — 소스가 조용히 줄었는지 확인")
        assert d.index[-1].normalize() == ASOF, f"{t}: 마지막 봉이 {d.index[-1]}"
        out[t] = d
    return out


def test_ls_eco_is_c2_jindori_again_after_the_split(bars):
    """LS에코 — v5.267에서 C2 진돌이였다가 v5.268(MA600 단독)에서 C3로 밀렸고,
    v5.272에서 **다시 C2 진돌이**로 돌아왔다. 게이트는 +39.9%로 통과하고
    단계는 MA200 +9.3%라 C2 구간이다."""
    r = A.analyze_abc(bars["229640.KQ"])
    assert r["verdict"] == "ABC", r
    assert r["gate_pct"] > 0, "게이트선 아래로 떨어졌다"
    assert r["c_stage"] == "C2 진돌이", r["c_stage"]
    assert 0 <= r["stage_pct"] < A.ABC_CONFIG["c3_min"] * 100, r["stage_pct"]


def test_rfhic_passes_the_gate_but_waits_below_the_stage_wall(bars):
    """RFHIC — 게이트 +66%(장기 추세는 돌았다)인데 **MA200 대비 −11.2%**라
    아직 벽 아래 대기다.

    v5.268은 MA600 하나로 재서 이걸 "C3 이탈"(이미 떠남)로 분류했다. 두 선을
    나눠야 "추세는 돌았지만 아직 안 왔다"가 표현된다 — v5.272의 목적이다.
    """
    r = A.analyze_abc(bars["218410.KQ"])
    assert r["verdict"] == "ABC", r
    assert r["gate_pct"] > 20, r["gate_pct"]
    assert r["c_stage"] == "C0 대기", r["c_stage"]
    assert r["stage_pct"] < A.ABC_CONFIG["c1"][0] * 100, r["stage_pct"]
    assert "벽 아래" in (r["reason"] or ""), r["reason"]


def test_tiger_elec_is_excluded(bars):
    """타이거일렉 — 기준선 훨씬 위로 멀리 간 종목. 기준선이 바뀌어도 ABC가 아니다."""
    r = A.analyze_abc(bars["219130.KQ"])
    assert r["verdict"] != "ABC", r
    assert r["c_stage"] is None and r["breakout"] is None


def test_the_two_baselines_actually_disagree(bars):
    """한 선을 복사해 쓰고 있으면 이 테스트가 잡는다."""
    for t in ("229640.KQ", "218410.KQ"):
        r = A.analyze_abc(bars[t])
        assert abs(r["gate_pct"] - r["stage_pct"]) > 20, (t, r["gate_pct"], r["stage_pct"])


def test_gate_and_stage_disagree_on_rfhic_by_design(bars):
    """게이트만 보면 "많이 올랐다"(+88%), 단계선으로 보면 "벽 바로 위"(+0.5%).
    v5.268이 이 둘을 한 선으로 뭉개 C3로 잘못 분류했던 바로 그 사례다."""
    r = A.analyze_abc(bars["218410.KQ"])
    assert r["gate_pct"] > 60 and r["stage_pct"] < 0, (r["gate_pct"], r["stage_pct"])


def test_short_history_ticker_is_excluded_not_crashed(bars):
    """봉을 600 미만으로 잘라도 예외 없이 '불가'로 빠져야 한다."""
    r = A.analyze_abc(bars["229640.KQ"].iloc[-300:])
    assert r["verdict"] == "MA600 불가", r["verdict"]
    assert A.grade(r, {"ok": True, "turnover_fail": False}) is None


def test_c_stage_is_a_state_that_moves_with_price(bars):
    """같은 종목이 며칠 뒤 다른 단계가 되는 게 **정상**이다.

    이 성질을 테스트가 부정해버리면(오늘 기준으로 못 박으면) 다음 사람이
    "버그"로 오해하고 상태 판정을 이벤트 판정으로 되돌릴 수 있다.
    """
    full = app._downcast(naver_kr.fetch_history("229640.KQ", days=naver_kr.KR_SCAN_DAYS))
    later = A.analyze_abc(full)
    pinned = A.analyze_abc(bars["229640.KQ"])
    if full.index[-1].normalize() > ASOF:
        assert later["stage_pct"] != pinned["stage_pct"], "봉이 늘었는데 값이 같다"


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
