"""v5.267/v5.268 — 실종목 기준 케이스(사용자가 지목한 3종목).

합성 봉만으로는 "내가 만든 모양을 내가 맞히는" tautology가 된다(CLAUDE.md의
사보타주 실패 패턴 중 "합성 데이터"). 그래서 실제 봉으로 고정한다.

**날짜를 못 박는 이유**: C단계는 이벤트가 아니라 **상태**라 매일 바뀐다.
실제로 LS에코는 09-17에 C2 진돌이(MA200 +9.3%)였다가 09-18엔 +22.5%가 되며
C3 이탈로 넘어갔다 — "오늘" 기준으로 단언하면 이 테스트는 하루 만에 썩는다.
그래서 봉을 2026-09-17까지 자른 뒤 판정한다(자르는 것 자체가 그날 프로덕션이
본 데이터와 같은 상태다).

**v5.268에서 세 종목 전부 판정이 바뀌었다** — 기준선이 MA200에서 MA600으로
가면서 같은 날 같은 봉인데 위치가 달라졌기 때문이다(사용자에게 보고한 값):

    LS에코   MA200 +9.3%  C2 진돌이  →  MA600 +39.9%  C3 이탈
    RFHIC   MA200 −11.2% C0 대기    →  MA600 +66.0%  C3 이탈
    타이거일렉 다른 셋업            →  다른 셋업(동일, MA600 +152.1%)

MA600은 2.4년 평균이라 그 사이 크게 오른 종목은 기준선이 한참 아래에 남는다.
"기준선 위/아래"의 의미 자체가 달라진 것이지 버그가 아니다 — 그래서 이
앵커들은 **새 값으로 다시 고정**한다.

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


def test_ls_eco_is_c3_under_the_600_baseline(bars):
    """LS에코에너지 — MA200 기준 C2 진돌이(+9.3%)였으나 **MA600 기준 C3 이탈**.

    기준선이 바뀌면 같은 봉이라도 단계가 달라진다는 걸 실데이터로 고정한다.
    MA200 값도 함께 확인해 "옛 기준이 사라진 게 아니라 보조로 내려갔을 뿐"임을
    드러낸다(그래야 나중에 회귀인지 설계인지 구분된다).
    """
    r = A.analyze_abc(bars["229640.KQ"])
    assert r["verdict"] == "ABC", r
    assert r["c_stage"] == "C3 이탈", r["c_stage"]
    assert r["ma_pct"] > 20, r["ma_pct"]                  # C3 문턱 위
    assert 8 < r["ma200_pct"] < 11, r["ma200_pct"]        # 보조 열은 옛 값 그대로
    assert A.grade(r, {"ok": True, "turnover_fail": False}) == "C급"


def test_rfhic_is_also_c3_under_the_600_baseline(bars):
    """RFHIC — MA200으론 −11.2%(C0 대기)인데 MA600으론 +66%(C3 이탈).

    두 기준선이 **부호까지 반대**로 갈리는 실례다.
    """
    r = A.analyze_abc(bars["218410.KQ"])
    assert r["verdict"] == "ABC", r
    assert r["c_stage"] == "C3 이탈", r["c_stage"]
    assert r["ma_pct"] > 20 and r["ma200_pct"] < 0, (r["ma_pct"], r["ma200_pct"])


def test_tiger_elec_is_excluded(bars):
    """타이거일렉 — 기준선 훨씬 위로 멀리 간 종목. 기준선이 바뀌어도 ABC가 아니다."""
    r = A.analyze_abc(bars["219130.KQ"])
    assert r["verdict"] != "ABC", r
    assert r["c_stage"] is None and r["breakout"] is None


def test_the_two_baselines_actually_disagree(bars):
    """MA200을 그냥 복사해 쓰고 있으면 이 테스트가 잡는다."""
    for t in ("229640.KQ", "218410.KQ"):
        r = A.analyze_abc(bars[t])
        assert abs(r["ma_pct"] - r["ma200_pct"]) > 20, (t, r["ma_pct"], r["ma200_pct"])


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
        assert later["ma_pct"] != pinned["ma_pct"], "봉이 늘었는데 값이 같다"


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
