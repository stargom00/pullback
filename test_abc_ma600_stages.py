"""v5.291 (사용자 지시) — C단계 MA600 개편.

지키는 것:
  1. **이벤트 우선** — 강돌파는 +20%(이탈 밴드)를 넘어도 강돌파다
  2. 강돌파 3조건 중 하나라도 미달이면 강돌파가 아니다(한선 +6.6% 사례)
  3. A급 후보는 강돌파뿐 — 벽앞·약돌파·대기는 A급 불가
  4. 이탈 → C급
  5. `grade()`가 라벨 **리터럴이 아니라 상수**를 비교한다
  6. 거래량 평균 기준은 **50일**(직전 5일이 아니다)
  7. 수급 조회 대상 = 강돌파 + 벽앞
  8. ★ 트리거 기준선이 MA600(`ma_gate`)이다

합성 봉으로 각 분기를 직접 만든다 — 실데이터만으론 "+20% 넘는 강돌파"처럼
드문 조합을 매일 확보할 수 없다. 실데이터 앵커는 `test_abc_real_tickers.py`가
따로 고정하고, 여기선 **경계 조합**을 만들어 순서 자체를 검증한다.
"""
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import abc_screener as A
import app
from test_helpers import code_only

ROOT = Path(__file__).resolve().parent
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
CFG = A.ABC_CONFIG


def _make_df(*, final_vs_ma600, breakout_bars_ago=None, breakout_day_pct=0.0,
             breakout_vol_mult=1.0, n=900):
    """MA600 대비 최종 위치와 (선택) 돌파봉 성격을 지정해 봉을 만든다.

    실수 두 번을 주석으로 남긴다(둘 다 `verdict != "ABC"`로 조용히 새는 모양):
      ① A 구간(고점→저점 −40%+, 40봉+)은 **`a_lookback`(250봉) 안에** 있어야
         한다. 처음엔 봉 300~420에 뒀다가 탐색창 밖이라 A가 안 보였다.
      ② 그 구간의 **고점이 최종가보다 높아야** 한다. 꼬리 레벨을 목표 gate_pct로
         올리다 보니 고점(150)을 넘겨(+30%면 193) `argmax`가 꼬리로 가고 그 뒤에
         하락이 없어 또 A가 사라졌다. 그래서 고점 300 · 바닥 165 · 그 앞 100으로
         벌려 꼬리가 고점을 못 넘게 한다.
    마지막에 `verdict`를 직접 검사해 **합성이 실패하면 즉시 터지게** 한다 —
    조용히 "다른 셋업"으로 빠지면 테스트가 무엄하게 통과한다.

    검증은 값을 역산하지 않고 `analyze_abc()`가 실제 계산한 `gate_pct`로 한다.
    """
    idx = pd.bdate_range("2021-01-01", periods=n)
    close = np.empty(n, dtype=float)
    win = n - CFG["a_lookback"]          # a_lookback 창 시작 (=650)
    close[:win] = 100.0                  # MA600 기반 구간(고점보다 낮게)
    close[win:win + 10] = 300.0          # 고점 — 꼬리가 절대 못 넘게 높게
    drop_end = win + 10 + 60
    close[win + 10:drop_end] = np.linspace(300.0, 165.0, 60)   # −45%, 60봉
    base_end = drop_end + 60
    close[drop_end:base_end] = 165.0     # 바닥 60봉
    close[base_end:] = 165.0             # 꼬리 — 아래에서 레벨 보정
    vol = np.full(n, 1000.0)
    for _ in range(300):                 # 목표 gate_pct로 수렴
        ma600 = float(pd.Series(close).iloc[-CFG["gate_ma_period"]:].mean())
        cur = close[-1] / ma600 - 1
        if abs(cur - final_vs_ma600) < 1e-4:
            break
        close[base_end:] += (final_vs_ma600 - cur) * ma600 * 0.5
    if breakout_bars_ago is not None:
        i = n - 1 - breakout_bars_ago
        assert i >= base_end, "돌파봉이 꼬리 구간 안에 있어야 한다"
        ma = pd.Series(close, index=idx).rolling(CFG["gate_ma_period"]).mean()
        m = float(ma.iloc[i])
        close[i - 1] = m * 0.99                       # 돌파 직전 봉 = MA600 아래
        close[i] = max(close[i - 1] * (1 + breakout_day_pct), m * 1.001)
        vol[i] = 1000.0 * breakout_vol_mult
    df = pd.DataFrame({"Open": close * 0.995, "High": close * 1.01,
                       "Low": close * 0.98, "Close": close, "Volume": vol},
                      index=idx)
    chk = A.analyze_abc(df)
    assert chk["verdict"] == "ABC", (
        f"합성 봉이 ABC 후보가 아니다({chk['verdict']}: {chk['reason']}) — "
        "이 픽스처가 조용히 새면 아래 단계 검증이 전부 무의미해진다")
    return df


# ══ 1) 이벤트 우선 — 강돌파는 이탈 밴드를 넘어도 강돌파 ══════════════
def test_strong_breakout_wins_over_the_exit_band():
    """+20%(이탈 기준)를 넘어도 강돌파. 실측 근거: 티이엠씨 +22.4%·vol×42,
    우리넷 +57.6%·vol×26 두 건이 밴드 우선 판정에선 이탈에 먹혔다."""
    df = _make_df(final_vs_ma600=0.30, breakout_bars_ago=3,
                  breakout_day_pct=0.15, breakout_vol_mult=5.0)
    r = A.analyze_abc(df)
    assert r["verdict"] == "ABC", r["reason"]
    assert r["gate_pct"] > CFG["exit_min"] * 100, r["gate_pct"]   # 이탈 구간인데
    assert r["c_stage"] == A.STAGE_STRONG, (r["c_stage"], r["gate_pct"], r["gate_break"])


def test_exit_band_applies_when_there_is_no_strong_breakout():
    df = _make_df(final_vs_ma600=0.30)
    r = A.analyze_abc(df)
    assert r["gate_break"] is None
    assert r["c_stage"] == A.STAGE_EXIT, r["c_stage"]


# ══ 2) 강돌파 3조건 — 하나라도 미달이면 아니다 ═══════════════════════
def test_weak_day_percent_is_not_a_strong_breakout():
    """한선엔지니어링 사례: 돌파봉 +6.6% < 기준 +7% → 강돌파 아님(실측)."""
    df = _make_df(final_vs_ma600=0.10, breakout_bars_ago=5,
                  breakout_day_pct=0.066, breakout_vol_mult=5.0)
    r = A.analyze_abc(df)
    gb = r["gate_break"]
    assert gb is not None and gb["vol_ok"] and not gb["day_ok"], gb
    assert gb["strong"] is False
    assert r["c_stage"] == A.STAGE_WEAK, r["c_stage"]


def test_weak_volume_is_not_a_strong_breakout():
    df = _make_df(final_vs_ma600=0.10, breakout_bars_ago=5,
                  breakout_day_pct=0.15, breakout_vol_mult=1.2)
    gb = A.analyze_abc(df)["gate_break"]
    assert gb["day_ok"] and not gb["vol_ok"], gb
    assert gb["strong"] is False


def test_breakout_outside_the_window_is_not_found():
    df = _make_df(final_vs_ma600=0.10,
                  breakout_bars_ago=CFG["strong_window"] + 5,
                  breakout_day_pct=0.15, breakout_vol_mult=5.0)
    assert A.analyze_abc(df)["gate_break"] is None


# ══ 3) 밴드 — 벽앞 / 대기 ════════════════════════════════════════════
@pytest.mark.parametrize("vs600,expected", [
    (0.10, A.STAGE_WEAK),
    (-0.02, A.STAGE_WALL),
    (-0.04, A.STAGE_WALL),
    (-0.10, A.STAGE_WAIT),
    (-0.30, A.STAGE_WAIT),
])
def test_band_stages(vs600, expected):
    r = A.analyze_abc(_make_df(final_vs_ma600=vs600))
    assert r["c_stage"] == expected, (vs600, r["c_stage"], r["gate_pct"])


def test_wait_and_wall_are_visible_not_gated_out():
    """MA600 게이트 제거 — 아래에 있어도 후보로 남아야 화면에 보인다(사용자 지시)."""
    for vs in (-0.02, -0.30):
        r = A.analyze_abc(_make_df(final_vs_ma600=vs))
        assert r["verdict"] == "ABC" and r["c_stage"] is not None, (vs, r)


# ══ 4) 등급 — A급은 강돌파만 ═════════════════════════════════════════
_COMP_OK = {"ok": True, "fails": [], "turnover_fail": False, "turnover_large": False}


@pytest.mark.parametrize("stage,expected", [
    (A.STAGE_STRONG, "A급"),
    (A.STAGE_WALL, "B급"),
    (A.STAGE_WEAK, "B급"),
    (A.STAGE_WAIT, "B급"),
    (A.STAGE_EXIT, "C급"),
])
def test_only_strong_breakout_can_be_a_grade(stage, expected):
    res = {"verdict": "ABC", "c_stage": stage, "b": {"ok": True}}
    assert A.grade(res, _COMP_OK) == expected, stage


def test_exit_is_c_grade_even_with_perfect_chart_and_company():
    res = {"verdict": "ABC", "c_stage": A.STAGE_EXIT, "b": {"ok": True}}
    assert A.grade(res, _COMP_OK) == "C급"


def test_turnover_large_still_caps_strong_breakout_at_b():
    comp = dict(_COMP_OK, turnover_large=True)
    res = {"verdict": "ABC", "c_stage": A.STAGE_STRONG, "b": {"ok": True}}
    assert A.grade(res, comp) == "B급"


# ══ 5) 라벨 상수화 — 리터럴 비교 금지 ════════════════════════════════
def test_grade_uses_stage_constants_not_literals():
    """`grade()` 본문에 단계 라벨 **문자열 리터럴**이 있으면 실패. 라벨을 바꿀 때
    비교가 조용히 항상 False가 되어 이탈이 A급까지 올라가는 회귀를 막는다."""
    import inspect
    src = code_only(inspect.getsource(A.grade), comment_markers=("//", "#"))
    body = src[src.index('"""', src.index('"""') + 3) + 3:]      # docstring 제외
    for label in A.C_STAGES:
        assert f'"{label}"' not in body and f"'{label}'" not in body, (
            f"grade() 본문에 라벨 리터럴 {label!r}이 있다 — STAGE_* 상수를 쓸 것")
    assert "STAGE_EXIT" in body and "STAGE_STRONG" in body, body


def test_app_consumers_use_constants_not_old_labels():
    """app.py의 우선순위·수급대상·정렬·섹터묶음이 구 라벨을 안 쓰는지."""
    src = code_only((ROOT / "app.py").read_text(encoding="utf-8"), comment_markers=("//", "#"))
    src = src[src.index('VERSION = "v5.'):]        # changelog 제외(인용문 오탐 방지)
    for old in ("C0 대기", "C1 벽앞", "C2 진돌이", "C2 가돌이", "C2 돌파 없음", "C3 이탈"):
        assert old not in src, f"app.py 실행 코드에 구 라벨 {old!r}이 남아 있다"
    assert 'startswith(("C1", "C2"))' not in src


def test_priority_map_uses_constants():
    assert set(app._ABC_STAGE_PRIORITY) == set(A.C_STAGES)
    P = app._ABC_STAGE_PRIORITY
    assert (P[A.STAGE_STRONG] < P[A.STAGE_WALL] < P[A.STAGE_WEAK]
            < P[A.STAGE_WAIT] < P[A.STAGE_EXIT])


# ══ 6) 거래량 평균 = 50일 ════════════════════════════════════════════
def test_strong_breakout_volume_uses_the_50day_average():
    """사용자 확정: 50일 평균. 5일 평균으로 바꾸면 여기서 값이 달라진다 —
    직전 5일만 거래량을 올려두고 배수를 확인한다."""
    df = _make_df(final_vs_ma600=0.10, breakout_bars_ago=5,
                  breakout_day_pct=0.15, breakout_vol_mult=1.0)
    i = len(df) - 1 - 5
    v = df["Volume"].values
    v[i - 5:i] = 4000.0          # 직전 5일만 4배 — 50일 평균은 거의 안 움직인다
    v[i] = 3000.0                # 돌파봉 3배(50일 기준) / 0.75배(5일 기준)
    gb = A.analyze_abc(df)["gate_break"]
    # 50일 평균 기준이면 배수 > 2, 5일 평균 기준이면 < 1
    assert gb["vol_mult"] > 2.0, f"50일 평균을 안 쓴다: {gb['vol_mult']}"
    assert gb["vol_ok"] is True
    assert A.ABC_CONFIG["gate_break_vol_avg"] == 50


def test_ma200_breakout_still_uses_5day_average():
    """MA200 쪽(`_find_breakout`, 진돌이/가돌이)은 직전 5일 유지 — 사용자 확정."""
    assert A.ABC_CONFIG["vol_avg_bars"] == 5
    import inspect
    assert 'cfg["vol_avg_bars"]' in inspect.getsource(A._find_breakout)


# ══ 7) 수급 조회 대상 = 강돌파 + 벽앞 ════════════════════════════════
def test_flow_targets_are_strong_and_wall():
    import inspect
    src = code_only(inspect.getsource(app.api_abc), comment_markers=("//", "#"))
    assert "_FLOW_STAGES = {abc_screener.STAGE_STRONG, abc_screener.STAGE_WALL}" in src
    assert 'r["c_stage"] in _FLOW_STAGES' in src


# ══ 8) ★ 트리거 기준선 = MA600 ═══════════════════════════════════════
def test_star_trigger_uses_ma_gate():
    watch = code_only(HTML[HTML.index("async function abcWatch("):
                            HTML.index("// v4.83: 코스피/코스닥 마감 정리")])
    assert "my_trigger_price: h.ma_gate," in watch
    assert "h.close > h.ma_gate" in watch
    assert "ma_stage" not in watch, "★ 트리거에 MA200 참조가 남아 있다"


def test_ma600_column_warns_about_dividend_adjustment():
    """사용자 지시: MA600 칸 툴팁에 배당 미조정 안내 한 줄."""
    assert "현금배당" in HTML and "TradingView" in HTML
    i = HTML.index("(단계기준)")
    head = HTML[i - 700:i]
    assert "현금배당" in head and "2~3%" in head, head[-300:]
