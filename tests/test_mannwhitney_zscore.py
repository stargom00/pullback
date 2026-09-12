"""harness.mannwhitney_zscore()가 scipy의 asymptotic MWU와 같은 z를 내는지.

2026-09-13 바닥다지기 측정(docs/stage1to2_base_setup.md §3-2)에서 판정
통계량으로 신설한 함수. scipy는 이 레포의 런타임 의존성이 아니라서
(measurement 스크립트도 안 씀) 설치돼 있을 때만 대조한다 — 없으면 skip.

검증 관례(CLAUDE.md): 통과만 확인하고 끝내면 tautology를 못 거른다.
아래 test_detects_shift가 "차이 없는 표본 vs 밀린 표본"을 갈라내는지까지
본다.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "measurements"))
import harness  # noqa: E402

def _bruteforce_z(a, b):
    """U를 정의대로 완전열거해 구한 참조값 — harness와 코드를 공유하지 않는
    독립 구현이라 "같은 실수를 양쪽이 똑같이 한다"가 불가능하다.

    U_b = #{(x,y) : y > x} + 0.5 × #{(x,y) : y == x}  (x∈a, y∈b).
    순위합 공식으로 구한 harness의 U와 수학적으로 동일해야 한다.
    """
    u_b = 0.0
    for y in b:
        for x in a:
            if y > x:
                u_b += 1.0
            elif y == x:
                u_b += 0.5
    na, nb = len(a), len(b)
    n = na + nb
    mu = na * nb / 2.0
    tie = pd.Series(list(a) + list(b)).value_counts()
    tie_term = float(((tie ** 3 - tie).sum())) / (n * (n - 1))
    var = na * nb / 12.0 * ((n + 1) - tie_term)
    return (u_b - mu) / var ** 0.5


def _scipy_z(a, b):
    """scipy가 있으면 3자 대조용. 없으면 호출부가 skip."""
    scipy_stats = pytest.importorskip("scipy.stats", reason="scipy 미설치 — 대조 생략")
    res = scipy_stats.mannwhitneyu(b, a, alternative="two-sided", method="asymptotic",
                                   use_continuity=False)
    na, nb = len(a), len(b)
    n = na + nb
    mu = na * nb / 2.0
    tie = pd.Series(list(a) + list(b)).value_counts()
    tie_term = float(((tie ** 3 - tie).sum())) / (n * (n - 1))
    var = na * nb / 12.0 * ((n + 1) - tie_term)
    return (res.statistic - mu) / var ** 0.5


CASES = {
    # 동점 없음
    "no_ties": ([1.0, 3.5, 2.2, 8.1, 0.4, 5.5, 6.6, 7.7, 9.9, 2.9],
                [4.4, 10.1, 3.3, 12.5, 6.1, 11.0, 5.9, 13.2, 7.4, 8.8]),
    # 동점 다수 (수익률 0% 같은 값이 잔뜩 겹치는 실제 상황)
    "many_ties": ([0.0] * 12 + [1.0] * 8 + [-1.0] * 5,
                  [0.0] * 10 + [1.0] * 14 + [2.0] * 6),
    # 전 표본이 서로 완전히 겹치는 극단 동점
    "heavy_ties": ([0.0] * 20 + [5.0] * 20, [0.0] * 18 + [5.0] * 22),
    # 표본 크기 불균형
    "lopsided": (list(range(40)), [3.0, 7.0, 11.0, 19.0, 25.0]),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_matches_bruteforce(name):
    """scipy 없이도 항상 도는 본 검증 — 완전열거 U와 대조."""
    a, b = CASES[name]
    z, sig = harness.mannwhitney_zscore(pd.Series(a), pd.Series(b))
    assert z is not None, f"{name}: z 계산 실패"
    expected = _bruteforce_z(a, b)
    assert z == pytest.approx(expected, abs=1e-9), f"{name}: {z} != 완전열거 {expected}"
    assert sig == (abs(expected) >= 1.96)


@pytest.mark.parametrize("name", sorted(CASES))
def test_matches_scipy(name):
    """scipy가 설치된 환경에서만 도는 3자 대조."""
    a, b = CASES[name]
    z, _ = harness.mannwhitney_zscore(pd.Series(a), pd.Series(b))
    expected = _scipy_z(a, b)
    assert z == pytest.approx(expected, abs=1e-9), f"{name}: {z} != scipy {expected}"


def test_sign_direction():
    """z > 0 은 b가 a보다 큰 쪽 — welch_zscore와 같은 방향."""
    low = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    high = pd.Series([11.0, 12.0, 13.0, 14.0, 15.0, 16.0])
    z_up, _ = harness.mannwhitney_zscore(low, high)
    z_down, _ = harness.mannwhitney_zscore(high, low)
    assert z_up > 0 and z_down < 0
    assert z_up == pytest.approx(-z_down)

    wz_up, _ = harness.welch_zscore(low, high)
    assert (z_up > 0) == (wz_up > 0), "welch와 부호 방향이 어긋나면 판정이 뒤집힌다"


def test_detects_shift():
    """탐지력: 같은 분포는 유의하지 않고, 밀린 분포는 유의해야 한다."""
    import numpy as np
    rng = np.random.default_rng(20260913)
    base = rng.normal(0, 10, 400)
    same = rng.normal(0, 10, 400)
    shifted = rng.normal(8, 10, 400)

    z_same, sig_same = harness.mannwhitney_zscore(pd.Series(base), pd.Series(same))
    z_shift, sig_shift = harness.mannwhitney_zscore(pd.Series(base), pd.Series(shifted))
    assert not sig_same, f"차이 없는 표본이 유의로 나왔다 (z={z_same})"
    assert sig_shift and z_shift > 0, f"+8 시프트를 못 잡았다 (z={z_shift})"


def test_returns_none_on_degenerate():
    tiny = pd.Series([1.0, 2.0])
    assert harness.mannwhitney_zscore(tiny, pd.Series([1.0, 2.0, 3.0, 4.0]))[0] is None
    allsame = pd.Series([3.0] * 10)
    assert harness.mannwhitney_zscore(allsame, pd.Series([3.0] * 10))[0] is None


def test_nan_dropped():
    a = pd.Series([1.0, 2.0, None, 3.0, 4.0])
    b = pd.Series([5.0, None, 6.0, 7.0, 8.0])
    z_with_nan, _ = harness.mannwhitney_zscore(a, b)
    z_clean, _ = harness.mannwhitney_zscore(pd.Series([1.0, 2.0, 3.0, 4.0]),
                                            pd.Series([5.0, 6.0, 7.0, 8.0]))
    assert z_with_nan == pytest.approx(z_clean)
