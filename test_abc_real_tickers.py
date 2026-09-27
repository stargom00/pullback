"""v5.267/v5.268 — 실종목 기준 케이스(사용자가 지목한 3종목).

합성 봉만으로는 "내가 만든 모양을 내가 맞히는" tautology가 된다(CLAUDE.md의
사보타주 실패 패턴 중 "합성 데이터"). 그래서 실제 봉으로 고정한다.

**날짜를 못 박는 이유**: C단계는 이벤트가 아니라 **상태**라 매일 바뀐다.
실제로 LS에코는 09-17에 C2 진돌이(MA200 +9.3%)였다가 09-18엔 +22.5%가 되며
C3 이탈로 넘어갔다 — "오늘" 기준으로 단언하면 이 테스트는 하루 만에 썩는다.
그래서 봉을 2026-09-17까지 자른 뒤 판정한다(자르는 것 자체가 그날 프로덕션이
본 데이터와 같은 상태다).

**v5.291에서 C단계가 MA600 기준으로 돌아갔다(사용자 지시 — 방법론 정정:
"ABC 핵심은 MA600을 거래량 동반 장대양봉으로 뚫느냐").** v5.268이 MA600 단독으로
갔다가 11/13이 이탈로 쏠려 v5.272에서 MA200으로 물러났는데, v5.291은 **판정
순서**로 그 문제를 푼다 — 🩷강돌파(이벤트)를 밴드(위치)보다 먼저 본다.
B 중앙값 밴드·매물대·진돌이/가돌이는 MA200 유지(사용자 확정).

    (2026-09-17 고정봉 기준, v5.291 정의)
    LS에코   MA600 +39.9% · 20봉 내 MA600 돌파 없음 → 이탈
    RFHIC   MA600 +66.0% · 돌파 없음               → 이탈
    타이거일렉 다른 셋업(MA600 +152.1%)

    v5.272(MA200 단계)에선 LS에코 C2 진돌이 · RFHIC C0 대기였다. 두 값(gate
    +39.9% / stage +9.3%)은 그대로이고 **어느 선으로 단계를 매기느냐만** 바뀌었다.

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


def test_ls_eco_is_exit_under_the_ma600_stage(bars):
    """LS에코 — MA600 +39.9%로 이탈 기준(+20%)을 넘었고 최근 20봉 안에 MA600
    돌파가 없어 **이탈**이다. MA200 기준이던 v5.272에선 C2 진돌이였다(+9.3%) —
    두 값은 그대로이고 단계를 매기는 선만 바뀌었다."""
    r = A.analyze_abc(bars["229640.KQ"])
    assert r["verdict"] == "ABC", r
    assert r["gate_pct"] > A.ABC_CONFIG["exit_min"] * 100, r["gate_pct"]
    assert r["gate_break"] is None, "20봉 내 MA600 돌파가 생겼다 — 앵커 재고정 필요"
    assert r["c_stage"] == A.STAGE_EXIT, r["c_stage"]
    # MA200 값은 여전히 계산된다(참고 칸) — 단계와 무관해졌을 뿐이다.
    assert 0 <= r["stage_pct"] < 20, r["stage_pct"]


def test_rfhic_is_exit_by_ma600_even_though_it_sits_below_ma200(bars):
    """RFHIC — MA600 +66%라 이탈인데 **MA200으론 −11.2%**(벽 아래)다. 두 선이
    정반대를 말하는 사례로, v5.291이 "MA600으로 단계를 매긴다"를 지킨다는 증거다.
    v5.272에선 C0 대기였다 — 이 차이가 설계 변경의 결과지 회귀가 아니다."""
    r = A.analyze_abc(bars["218410.KQ"])
    assert r["verdict"] == "ABC", r
    assert r["gate_pct"] > 60, r["gate_pct"]
    assert r["stage_pct"] < 0, r["stage_pct"]
    assert r["c_stage"] == A.STAGE_EXIT, r["c_stage"]


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
    assert r["verdict"] == f"{A._ma_label()} 불가", r["verdict"]
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
