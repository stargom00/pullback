"""_trace_*(app.py, 진단 재현)와 analyze_*(scanner.py, 실제 스캔) 차등 테스트 (v5.63).

배경: test_trace_const_audit.py는 AST로 "리터럴이 CONFIG 밖에 있는지"만 본다 —
어떤 리터럴이 scanner.py의 어느 값과 "짝"인지는 코드 구조만으론 판별 못 해서
완전자동 FAIL은 좁은 패턴(리터럴 삼항식)에만 걸었다. 이 파일은 다른 방식으로
같은 문제를 잡는다: 상수 이름/위치를 아예 안 보고, 실제 KR/US 종목에 두 함수를
똑같이 돌려서 "같은 입력 → 같은 출력"인지 그냥 실행 결과로 비교한다. 상수가
몇 개든 어디 있든 무관 — 둘이 갈라지는 순간(게이트 통과/탈락 불일치, 또는
stop/risk_pct 값 불일치) 바로 잡힌다.

데이터: test_fixtures/sample_tickers.pkl — 실제 KR 23종목 + US 15종목(총 38),
2026-08-07 종가까지 300봉(체크인된 고정 스냅샷 — CI 네트워크 의존 없음, 결정론적).
초기 29종목 표본은 turnaround/breakout/boxbreak 3개 셋업에서 stop/risk_pct
비교 대상(둘 다 통과)이 0건이었음(pullback/imminent만 우연히 걸림) — 5개
셋업 전부 최소 커버리지를 확보하려고 유니버스 전체를 스캔해 각 셋업을
실제로 통과하는 종목을 골라 9개 추가(v5.64). `_summary()`가 셋업별 커버리지를
출력하니, 앞으로 픽스처를 바꿀 일이 있으면 `python3 test_trace_parity.py`로
0건인 셋업이 생기지 않는지 확인할 것.

rs_rank는 두 값을 다 돈다 — 82(모든 셋업의 rs_min 80~88을 넘되 leader_rs=90
미만, "일반" 분기)와 95(leader_rs 이상, "주도주" 분기). 하나만 쓰면 안 되는
이유를 직접 겪었음: 처음엔 95 하나만 썼더니, v5.60 slope_floor 버그(주도주만
0.98 적용하던 옛 코드)를 일부러 재현해 넣어도 이 테스트가 통과해버렸다 — 95는
항상 is_leader=True라 옛 코드의 "if is_leader" 분기만 타서 새 코드(무조건
0.98)와 우연히 같은 값이 나왔기 때문. 두 rs_rank로 양쪽 분기를 다 밟아야 이런
조건부 로직의 분기 불일치를 놓치지 않는다.

비교 범위: (1) 통과/탈락 일치 — 전체 조합에 항상 적용. 이게 갈리면 게이트
순서/조건 자체가 다르다는 뜻이라 가장 근본적인 불일치. (2) stop/risk_pct 값
일치 — 둘 다 통과했을 때만(탈락 시 analyze_*는 bare None이라 비교할 실측값
자체가 없음)."""
import pickle
from pathlib import Path

import pytest

import app
import scanner

FIXTURE_PATH = Path(__file__).parent / "test_fixtures" / "sample_tickers.pkl"
with open(FIXTURE_PATH, "rb") as f:
    SAMPLE = pickle.load(f)

# 82=일반 분기(모든 셋업 rs_min 80~88 통과, leader_rs=90 미만),
# 95=주도주 분기(leader_rs 이상) — 조건부 로직(is_leader 등) 양쪽을 다 밟는다.
RS_VALUES = [82, 95]
RS_MOM_HIGH = 20

# v5.65: 셋업별 최소 커버리지 — stop/risk_pct를 실제로 비교한(둘 다 통과한)
# 건수가 이 아래면 테스트를 FAIL시킨다. v5.64에서 turnaround/breakout/boxbreak가
# 0건인 채로 "통과" 취급되던 걸 발견(통과/탈락 일치만 봤지 값 비교는 한 번도
# 안 돌았음) — 그때는 print 경고만 달아놨는데, CI가 초록불이면 아무도 로그를
# 안 읽는다는 지적을 받고 hard FAIL로 바꿈. 3 = 현재 최소 셋업(turnaround/
# breakout)의 6건보다 낮게 잡은 여유치 — 픽스처를 나중에 줄이더라도 "최소
# 검증은 됐다"를 보장하는 하한선.
MIN_COVERAGE = 3


def _is_kr(ticker):
    return ticker.endswith((".KS", ".KQ"))


def _call_pullback(df, is_kr, rs_rank):
    real = scanner.analyze(df, rs_rank=rs_rank, rs_mom=RS_MOM_HIGH, is_kr=is_kr)
    trace = app._trace_pullback(df, is_kr, rs_rank)
    return real, trace


def _call_turnaround(df, is_kr, rs_rank):
    real = scanner.analyze_turnaround(df, rs_rank=rs_rank, rs_mom=RS_MOM_HIGH, is_kr=is_kr)
    trace = app._trace_turnaround(df, is_kr, rs_rank, RS_MOM_HIGH)
    return real, trace


def _call_breakout(df, is_kr, rs_rank):
    real = scanner.analyze_breakout(df, rs_rank=rs_rank, rs_mom=RS_MOM_HIGH, is_kr=is_kr)
    trace = app._trace_breakout(df, is_kr, rs_rank)
    return real, trace


def _call_boxbreak(df, is_kr, rs_rank):
    real = scanner.analyze_boxbreak(df, rs_rank=rs_rank, rs_mom=RS_MOM_HIGH, is_kr=is_kr)
    trace = app._trace_boxbreak(df, is_kr, rs_rank)
    return real, trace


def _call_imminent(df, is_kr, rs_rank):
    real = scanner.analyze_imminent(df, rs_rank=rs_rank, rs_mom=RS_MOM_HIGH, is_kr=is_kr)
    trace = app._trace_imminent(df, is_kr, rs_rank)
    return real, trace


SETUPS = {
    "pullback": _call_pullback,
    "turnaround": _call_turnaround,
    "breakout": _call_breakout,
    "boxbreak": _call_boxbreak,
    "imminent": _call_imminent,
}

CASES = [(setup, ticker, rs) for setup in SETUPS for ticker in SAMPLE for rs in RS_VALUES]


@pytest.mark.parametrize("setup,ticker,rs_rank", CASES,
                         ids=[f"{s}-{t}-rs{r}" for s, t, r in CASES])
def test_trace_matches_analyze(setup, ticker, rs_rank):
    df = SAMPLE[ticker]
    is_kr = _is_kr(ticker)
    real, trace = SETUPS[setup](df, is_kr, rs_rank)

    passed_real = real is not None
    passed_trace = bool(trace["passed"])
    assert passed_real == passed_trace, (
        f"[{setup}/{ticker}/rs{rs_rank}] 통과여부 불일치: analyze_*="
        f"{'통과' if passed_real else '탈락'} vs _trace_*="
        f"{'통과' if passed_trace else '탈락'}(fail_at={trace.get('fail_at')}) — "
        f"게이트 순서/조건 자체가 갈렸을 가능성. steps={trace.get('steps')}"
    )
    if not passed_real:
        return  # 탈락 시 analyze_*는 bare None이라 stop/risk_pct 비교 대상 자체가 없음

    real_stop = real.get("stop")
    trace_stop = trace.get("stop")
    assert real_stop is not None and trace_stop is not None, (
        f"[{setup}/{ticker}/rs{rs_rank}] 둘 다 통과했는데 stop 필드가 없음 — "
        f"real={real_stop}, trace={trace_stop}"
    )
    assert trace_stop == pytest.approx(real_stop, abs=0.01), (
        f"[{setup}/{ticker}/rs{rs_rank}] stop 불일치: analyze_*={real_stop} vs _trace_*={trace_stop}"
    )

    real_risk = real.get("risk_pct")
    trace_risk = trace.get("risk_pct")
    if real_risk is not None or trace_risk is not None:
        assert real_risk is not None and trace_risk is not None, (
            f"[{setup}/{ticker}/rs{rs_rank}] risk_pct 필드 유무 불일치: real={real_risk}, trace={trace_risk}"
        )
        assert trace_risk == pytest.approx(real_risk, abs=0.01), (
            f"[{setup}/{ticker}/rs{rs_rank}] risk_pct 불일치: analyze_*={real_risk} vs _trace_*={trace_risk}"
        )


def _compute_coverage():
    """전 조합을 돌려 셋업별 (통과/탈락 일치 여부, 둘다통과 목록, 값불일치 목록)을 계산.
    test_min_coverage_per_setup와 _summary()가 같은 계산을 공유 — 둘이
    따로 계산하다 갈리는 일이 없게."""
    both_passed_by_setup = {s: [] for s in SETUPS}
    pass_disagreements = []
    value_mismatches = []
    for setup, ticker, rs_rank in CASES:
        df = SAMPLE[ticker]
        is_kr = _is_kr(ticker)
        real, trace = SETUPS[setup](df, is_kr, rs_rank)
        passed_real = real is not None
        passed_trace = bool(trace["passed"])
        if passed_real != passed_trace:
            pass_disagreements.append((setup, ticker, rs_rank))
            continue
        if not passed_real:
            continue
        both_passed_by_setup[setup].append(f"{ticker}/rs{rs_rank}")
        stop_ok = trace.get("stop") == pytest.approx(real.get("stop"), abs=0.01)
        risk_ok = trace.get("risk_pct") == pytest.approx(real.get("risk_pct"), abs=0.01)
        if not (stop_ok and risk_ok):
            value_mismatches.append((setup, ticker, rs_rank,
                                     real.get("stop"), real.get("risk_pct"),
                                     trace.get("stop"), trace.get("risk_pct")))
    return both_passed_by_setup, pass_disagreements, value_mismatches


def test_min_coverage_per_setup():
    """셋업별로 stop/risk_pct를 실제로 비교한(둘 다 통과한) 건수가
    MIN_COVERAGE 미만이면 FAIL — v5.64에서 turnaround/breakout/boxbreak가
    0건인 채 발견됐던 사고(경고 print만 있어서 CI 초록불에 묻혔음) 재발 방지.
    이 테스트가 잡는 것: "픽스처를 나중에 손대다가(종목 교체·축소 등) 특정
    셋업의 값 비교 표본이 조용히 사라지는" 케이스. test_trace_matches_analyze
    자체는 값 비교 대상이 0건이어도 전부 통과해버리므로(비교할 게 없으니
    fail할 것도 없음) 이 테스트가 없으면 아무도 못 잡는다."""
    both_passed_by_setup, _, _ = _compute_coverage()
    under_threshold = {s: len(v) for s, v in both_passed_by_setup.items() if len(v) < MIN_COVERAGE}
    all_counts = {s: len(v) for s, v in both_passed_by_setup.items()}
    assert not under_threshold, (
        f"셋업별 커버리지(stop/risk_pct 값을 실제로 비교한 건수) 미달 "
        f"(기준 MIN_COVERAGE={MIN_COVERAGE}):\n"
        + "\n".join(f"  {s}: {n}건 (부족 {MIN_COVERAGE - n}건)" for s, n in under_threshold.items())
        + f"\n전체 분포: {all_counts}\n"
        + "→ test_fixtures/sample_tickers.pkl에 이 셋업을 실제로 통과시키는 "
          "종목을 추가할 것 (유니버스 스캔으로 후보 탐색, v5.64 커밋 참고)."
    )


def _summary():
    """pytest 없이 직접 실행 시 통과/탈락/비교 분포 요약 출력."""
    both_passed_by_setup, pass_disagreements, value_mismatches = _compute_coverage()
    n_both_passed = sum(len(v) for v in both_passed_by_setup.values())
    print(f"총 {len(CASES)}조합 — 통과/탈락 일치 {len(CASES) - len(pass_disagreements)}, "
          f"불일치 {len(pass_disagreements)}, 둘다통과(값비교대상) {n_both_passed}, "
          f"값불일치 {len(value_mismatches)}")
    print(f"\n셋업별 커버리지(stop/risk_pct 값을 실제로 비교한 건수 — MIN_COVERAGE={MIN_COVERAGE} "
          f"미만이면 test_min_coverage_per_setup가 FAIL):")
    for setup in SETUPS:
        n = len(both_passed_by_setup[setup])
        flag = f"  ⚠️ 기준({MIN_COVERAGE}) 미달!" if n < MIN_COVERAGE else ""
        print(f"  {setup}: {n}건{flag}")
    for setup, ticker, rs_rank in pass_disagreements:
        print(f"  [{setup}/{ticker}/rs{rs_rank}] PASS/FAIL 불일치")
    for setup, ticker, rs_rank, rs_stop, rs_risk, tr_stop, tr_risk in value_mismatches:
        print(f"  [{setup}/{ticker}/rs{rs_rank}] stop/risk_pct 불일치 "
              f"real={rs_stop}/{rs_risk} trace={tr_stop}/{tr_risk}")


if __name__ == "__main__":
    _summary()
