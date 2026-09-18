"""v5.267 ABC 스크리너 — 경계값과 실제 종목 고정.

⚠️ 임계값은 전부 2026-09-18 초기 임의값(측정 근거 없음). 이 테스트는 "그 값이
옳은가"가 아니라 **"코드가 그 값대로 판정하는가"**만 본다.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import abc_screener as A  # noqa: E402

CFG = A.ABC_CONFIG


def make(closes, vols=None, highs=None, lows=None):
    n = len(closes)
    idx = pd.bdate_range("2023-01-02", periods=n)
    c = pd.Series(closes, index=idx, dtype="float64")
    return pd.DataFrame({
        "Open": c, "Close": c,
        "High": pd.Series(highs if highs is not None else closes, index=idx, dtype="float64"),
        "Low": pd.Series(lows if lows is not None else closes, index=idx, dtype="float64"),
        "Volume": pd.Series(vols if vols is not None else [1000] * n, index=idx, dtype="float64"),
    }, index=idx)


def abc_shape(drop_pct=0.45, span=45, flat=None, lead=20):
    """ABC 모양 합성 — 기준선이 바닥 수준과 같아지도록 **앞쪽을 역산해 채운다**.

    v5.268 재설계. 이전엔 "마지막 200봉을 전부 횡보로 채워 MA200 == 횡보가"로
    만들었는데, 기준선이 600으로 올라가자 그 방식이 **스스로 모순**이 됐다:
    MA600을 맞추려면 600봉을 횡보로 채워야 하는데, 그러면 A 패턴(고점→저점)이
    `a_lookback`(250봉) **밖으로 밀려나** A가 아예 안 잡힌다. 실제로 이 단계에서
    테스트 10개가 한꺼번에 깨졌고, **코드가 아니라 픽스처가 원인**이었다.

    새 구성:
      · 최근 `a_lookback`봉 = lead(완만한 상승) + down(하락) + flat(바닥 횡보)
        → A 패턴이 항상 탐색창 안에 들어온다.
      · 그 **앞쪽**을 상수 P로 채우되, P는 MA(ma_period) == 바닥가가 되도록
        역산한다. 기준선보다 더 앞의 봉은 MA에 안 들어가므로 여유분은 자유.

    lead는 완만한 상승이라 고점이 하락 직전 한 봉으로 유일하다.
    (평탄한 고원으로 두면 argmax가 첫 봉을 집어 span이 부풀려진다.)
    """
    n_ma = CFG["ma_period"]
    hi, lo = 100.0, 100.0 * (1 - drop_pct)
    up = list(np.linspace(hi * 0.9, hi, lead))
    down = list(np.linspace(hi, lo, span + 1))[1:]
    if flat is None:
        flat = CFG["a_lookback"] - lead - span        # A가 탐색창에 꼭 맞게
    recent = up + down + [lo] * flat
    pre_n = max(0, n_ma - len(recent))
    if pre_n:
        # (pre_n·P + sum(recent)) / n_ma == lo  →  P를 푼다
        pre_v = (n_ma * lo - sum(recent)) / pre_n
        assert pre_v > 0, f"픽스처 역산이 음수({pre_v:.1f}) — flat/span 조합을 줄여라"
        pre = [pre_v] * (pre_n + 30)                  # +30은 min_bars 여유
    else:
        pre = []
    return pre + recent


def with_crossing(closes, above=1.05, below=0.95, n=5):
    """마지막 2n봉을 **기준선 아래 n봉 → 위 n봉**으로 바꿔 돌파를 하나 만든다.

    v5.268에 필요해진 헬퍼. 이전엔 바닥 횡보값을 그대로 두고 뒤쪽만 올렸는데,
    새 픽스처에서 바닥가와 기준선이 **거의 같아** `close[i-1] <= ma`가 소수점
    수준에서 갈렸다(실측: 바닥 55.000 vs MA 54.993 → 크로싱 미검출). 돌파
    직전 봉을 기준선 아래로 확실히 내려 **의도가 값에 드러나게** 한다.

    below/above는 대칭이라 합이 보존돼 기준선 자체는 거의 안 움직인다.
    """
    lo = closes[-1]
    out = list(closes)
    out[-2 * n:] = [lo * below] * n + [lo * above] * n
    return out


def ma_of(closes):
    """픽스처가 의도한 기준선 값 — 테스트가 직접 200/600을 쓰지 않게."""
    n = CFG["ma_period"]
    return float(np.mean(closes[-n:]))


def test_a_boundary_drop():
    """하락 39% → A 미달, 40% → A 충족."""
    for drop, want in ((0.39, False), (0.40, True)):
        r = A.analyze_abc(make(abc_shape(drop_pct=drop)))
        assert (r["verdict"] == "ABC") is want, (drop, r["verdict"], r["a"])


def test_a_boundary_span():
    """고점→저점 39봉 → 미달, 40봉 → 충족."""
    for span, want in ((39, False), (40, True)):
        r = A.analyze_abc(make(abc_shape(span=span)))
        assert (r["verdict"] == "ABC") is want, (span, r["a"])


def test_b_boundary_bars():
    """저점 직후로 20봉을 못 채우면 B 판정 자체를 안 한다(range_pct None).

    v5.267 확정 정의(안 b): 창은 **저점 직후** 20~60봉이다. 저점 봉을 포함해
    세므로 저점 이후 19봉이면 창이 딱 20봉으로 성립한다 — 그보다 짧아야 미달.
    """
    for flat, computed in ((10, False), (19, True), (25, True)):
        closes = abc_shape(flat=flat, lead=250 - flat)
        r = A.analyze_abc(make(closes))
        assert r["verdict"] == "ABC", r
        assert (r["b"]["range_pct"] is not None) is computed, (flat, r["b"])


def test_b_window_is_right_after_the_low_not_the_whole_span():
    """저점 **직후**만 본다 — 이후 상승분은 B에서 제외(상승은 C가 담당).

    저점 이후 전 구간으로 재면 −70% 빠졌다가 MA200까지 올라온 종목의 범위가
    60%대가 되어 **C2에 도달한 종목은 B를 통과할 수 없었다**(A급이 원리적으로
    불가능). 그 설계 결함을 막는 회귀 테스트다.
    """
    closes = abc_shape(flat=200)
    lo = closes[-1]
    closes[-40:] = [lo * (1 + 0.02 * i) for i in range(40)]   # 저점 한참 뒤 급등
    r = A.analyze_abc(make(closes))
    assert r["b"]["ok"] is True, r["b"]
    assert r["b"]["bars"] <= A.ABC_CONFIG["b_max_bars"]


def test_b_range_boundary():
    """횡보 범위가 좁고 중앙값이 MA200 ±15%면 통과."""
    tight = A.analyze_abc(make(abc_shape()))
    assert tight["b"]["ok"] is True, tight["b"]
    assert tight["b"]["bars"] == A.ABC_CONFIG["b_max_bars"], "가장 긴 구간을 골라야 한다"

    wide = abc_shape()
    lo = wide[-1]
    # 저점 **직후**(= B 탐색 구간)를 흔든다. 리터럴 200봉이 아니라 픽스처가
    # 실제로 만든 바닥 구간 길이를 써야 기준선 기간이 바뀌어도 따라온다.
    flat = CFG["a_lookback"] - 20 - 45
    wide[-flat:] = [lo if i % 2 else lo * 1.30 for i in range(flat)]   # 30% 진폭
    r = A.analyze_abc(make(wide))
    assert r["b"]["ok"] is False and r["b"]["range_pct"] > CFG["b_range_max"] * 100


def _at_ma(pct, vol_mult=1.0, breakout=False):
    """마지막 봉을 **기준선** 대비 pct 위치로 놓은 ABC 모양."""
    df = make(abc_shape())
    n = CFG["ma_period"]
    ma = float(df["Close"].iloc[-n:].mean())
    if breakout:
        df.iloc[-2, df.columns.get_loc("Close")] = ma * 0.99
    df.iloc[-1, df.columns.get_loc("Close")] = ma * (1 + pct)
    df.iloc[-6:-1, df.columns.get_loc("Volume")] = 1000.0
    df.iloc[-1, df.columns.get_loc("Volume")] = 1000.0 * vol_mult
    return A.analyze_abc(df)


def test_c_stage_boundaries():
    assert _at_ma(-0.10)["c_stage"] == "C0 대기"
    assert _at_ma(-0.02)["c_stage"] == "C1 벽앞"
    assert _at_ma(0.25)["c_stage"] == "C3 이탈"
    # C 구간 밖: C0 하단(-15%)보다 더 아래면 어느 단계도 아니다
    deep = _at_ma(-0.30)
    assert deep["c_stage"] is None and "C 구간 밖" in deep["reason"], deep

    # 이 픽스처는 횡보가 == MA200이라 **어제 종가가 MA200 이하**다 →
    # 위로 올라가면 정의상 "오늘 돌파"가 성립한다(broke_today).
    # 처음엔 이걸 못 보고 "돌파 없이 +10%면 구간 밖"이라 잘못 기대했다.
    assert _at_ma(0.10)["c_stage"] in ("C2 진돌이", "C2 가돌이")


def test_c2_vol_mult_boundary():
    """돌파 동반 시 vol 2.9 → 가돌이, 3.0 → 진돌이."""
    assert _at_ma(0.02, vol_mult=2.9, breakout=True)["c_stage"] == "C2 가돌이"
    assert _at_ma(0.02, vol_mult=3.0, breakout=True)["c_stage"] == "C2 진돌이"


def test_other_setup_counted_not_hit():
    """A 없음 + 기준선 위 장기 → '다른 셋업'."""
    r = A.analyze_abc(make(list(np.linspace(50, 120, CFG["ma_period"] + 100))))
    assert r["verdict"] == "다른 셋업", r
    assert "박스/눌림" in r["reason"]


def test_short_history_gets_its_own_verdict():
    """v5.268(사용자 지시): 기준선을 못 그리는 종목은 **등급 제외, 카운트만**.

    "ABC 아님"에 섞어 넣으면 화면에서 "패턴이 아니라서 빠진 것"과
    "데이터가 짧아서 못 본 것"이 구분되지 않는다.
    """
    r = A.analyze_abc(make([100.0] * (CFG["ma_period"] - 1)))
    assert r["verdict"] == f"MA{CFG['ma_period']} 불가", r["verdict"]
    assert "계산 불가" in r["reason"] and str(CFG["ma_period"]) in r["reason"]
    assert A.grade(r, {"ok": True, "turnover_fail": False}) is None, "등급이 매겨졌다"


def test_min_bars_follows_the_ma_period():
    """최소 봉수가 기준선 기간과 따로 놀면 MA가 NaN인 종목이 게이트를 통과한다."""
    assert A._min_bars() >= CFG["ma_period"]
    assert A._min_bars({**CFG, "ma_period": 900}) == 900


def test_supply_band_flag():
    """MA200~×1.3 구간 체류 봉수를 세고, 조기 반환 경로에서도 키가 있어야 한다."""
    r = A.analyze_abc(make(abc_shape()))
    assert isinstance(r["supply_above"], bool) and r["supply_bars"] >= 0
    short = A.analyze_abc(make([100.0] * 100))
    assert short["supply_bars"] == 0, "조기 반환에서 키가 빠지면 호출부가 KeyError"


# ── 기업 축·등급 ──────────────────────────────────────────────────────
def test_company_axis_turnover_gate():
    c = A.company_axis(250, 4, 2, False, rev_yoy_of=4)
    assert c["turnover_fail"] is True and not c["ok"]
    c2 = A.company_axis(500, 4, 2, False, rev_yoy_of=4)
    assert c2["ok"] is True and c2["turnover_fail"] is False


def test_company_axis_collects_all_fails():
    c = A.company_axis(100, 1, 0, True, rev_yoy_of=4)
    assert len(c["fails"]) == 4, c["fails"]


def test_rev_yoy_denominator_is_required_not_defaulted():
    """기본값을 두면 호출부가 빼먹었을 때 **매출 축이 조용히 사라진다**.
    (실제로 naver가 6분기만 줘서 이 분모가 자주 부족하다 — 넘기는 걸 강제한다.)"""
    import inspect
    sig = inspect.signature(A.company_axis)
    p = sig.parameters["rev_yoy_of"]
    assert p.default is inspect.Parameter.empty, "기본값이 생겼다"
    assert p.kind is inspect.Parameter.KEYWORD_ONLY
    with pytest.raises(TypeError):
        A.company_axis(500, 1, 2, False)


def test_company_axis_ignores_market_cap():
    """시총은 판정에 안 들어간다(번들이 이미 필터 통과분이라 변별력 없음)."""
    import inspect
    src = inspect.getsource(A.company_axis)
    assert "mcap" not in src and "시총" in src, "시총 제외 근거 주석이 없다"


def _g(b_ok, stage, comp_ok, turnover_fail=False, verdict="ABC"):
    return A.grade({"verdict": verdict, "b": {"ok": b_ok}, "c_stage": stage},
                   {"ok": comp_ok, "turnover_fail": turnover_fail})


def test_grade_matrix_is_three_tiers():
    """사용자 확정 매트릭스(2026-09-18) 전수. "A급 근접"은 삭제됐다."""
    assert _g(True, "C1 벽앞", True) == "A급"          # 차트 전부 & 기업 전부
    assert _g(True, "C1 벽앞", False) == "B급"         # 차트 전부 & 기업 감점
    assert _g(False, "C1 벽앞", True) == "B급"         # 기업 충족 & B 미달
    assert _g(True, "C1 벽앞", True, turnover_fail=True) == "C급"
    assert _g(True, "C3 이탈", True) == "C급"
    assert _g(True, "C1 벽앞", True, verdict="다른 셋업") is None
    assert _g(True, "C1 벽앞", True, verdict="ABC 아님") is None


def test_obsolete_labels_are_gone():
    """라벨이 코드에 남아 있으면 화면에 다시 샌다(필터 칩·색 테이블 모두)."""
    import inspect
    src = inspect.getsource(A)
    assert "A급 근접" not in src
    assert "trading_only" not in src.replace(
        "`trading_only`를 개명한 것이다", "")      # 개명 근거 주석만 예외


def test_demotion_beats_a_good_chart():
    """거래대금 미달·C3은 **강등** 조건이다 — 차트가 완벽해도 위로 못 간다."""
    assert _g(True, "C2 진돌이", True, turnover_fail=True) == "C급"
    assert _g(True, "C3 이탈", True) == "C급"


def test_spec_gap_both_failing_lands_in_c():
    """**사양에 없던 조합**: B 미달 + 기업 감점. B급 두 갈래 어디에도 안 맞아
    최하위 C급으로 뒀고 사용자가 확정했다("둘 다 못 하면 최하위", 2026-09-18).
    제외는 A가 없을 때만이다."""
    assert _g(False, "C1 벽앞", False) == "C급"


def test_grade_never_returns_an_unknown_label():
    """등급 문자열이 늘어나면 화면 색 테이블·필터 칩이 조용히 어긋난다."""
    allowed = {"A급", "B급", "C급", None}
    for b in (True, False):
        for stage in ("C0 대기", "C1 벽앞", "C2 진돌이", "C2 가돌이",
                      "C2 돌파 없음", "C3 이탈", None):
            for ok in (True, False):
                for tf in (True, False):
                    assert _g(b, stage, ok, tf) in allowed


def test_config_is_single_source():
    """임계값이 코드에 흩어져 있으면 다음에 바꿀 때 어긋난다."""
    import inspect
    src = inspect.getsource(A.analyze_abc)
    for lit in ("0.40", "0.25", "250", "30"):
        assert f"= {lit}" not in src, f"리터럴 {lit}이 함수 안에 박혀 있다"
    assert "근거가 없다" in A.__doc__ or "근거가 없" in A.__doc__


# ══════════════════════════════════════════════════════════════════════
# 돌파봉 탐지 — **룩어헤드 금지**가 핵심
# ══════════════════════════════════════════════════════════════════════
def test_breakout_uses_the_ma_at_that_bar_not_today():
    """**룩어헤드 회귀 방지.** 과거 봉을 '오늘의 MA200'과 비교하면 미래 정보로
    과거를 판정하는 것이다 — 실제로 그렇게 짰다가 돌파 시점과 거래량이 둘 다
    틀렸다(LS에코 09-18 기준에서 잘못된 봉이 잡힘).

    기준선이 우상향하는 구간을 만들어, 오늘 기준선으로 보면 '옛날에 넘었다'가
    되지만 당시 기준선으로 보면 아직 아래인 봉을 만든다.
    """
    import inspect
    src = inspect.getsource(A._find_breakout)
    assert 'rolling(cfg["ma_period"])' in src, "각 봉 시점의 기준선을 안 쓴다"
    assert "rolling(200)" not in src, "기간이 리터럴로 굳었다"
    # 기준선을 상수로 받는 인자가 비교에 쓰이면 안 된다
    body = src[src.index("n = len(close)"):]
    assert "< float(close.iloc[i])" in body and "float(m)" in body, body


def test_breakout_detects_the_crossing_bar():
    closes = with_crossing(abc_shape(), above=1.30)
    df = make(closes)
    r = A.analyze_abc(df)
    bo = r["breakout"]
    assert bo is not None and bo["bars_ago"] == 4, bo


def test_breakout_volume_uses_peak_within_window():
    """(b) 확정: 돌파봉 포함 N봉 중 **최대 거래량**으로 진돌이/가돌이.
    교차봉이 소량이고 다음날 대량이 터지는 형태를 가돌이로 오판하면 안 된다."""
    closes = with_crossing(abc_shape())
    vols = [1000.0] * len(closes)
    vols[-5] = 2000.0          # 교차봉: 2배
    vols[-4] = 12000.0         # 다음 봉: 12배 ← 이게 잡혀야 한다
    r = A.analyze_abc(make(closes, vols=vols))
    bo = r["breakout"]
    assert bo["vol_bar_ago"] == 3, bo
    assert bo["vol_mult"] >= A.ABC_CONFIG["c2_vol_mult"], bo
    assert r["c_stage"] == "C2 진돌이", r["c_stage"]


def test_breakout_volume_window_is_bounded():
    """N봉을 벗어난 대량은 잡지 않는다(N=3, 임의값)."""
    closes = with_crossing(abc_shape())
    vols = [1000.0] * len(closes)
    vols[-5] = 1500.0
    vols[-1] = 50000.0         # 돌파 4봉 뒤 — 창 밖
    r = A.analyze_abc(make(closes, vols=vols))
    assert r["breakout"]["vol_mult"] < 10, r["breakout"]
    assert r["c_stage"] == "C2 가돌이"


def test_no_breakout_when_always_above():
    """60봉 내내 MA200 위 → '돌파 없음'(등급엔 무관)."""
    closes = abc_shape(flat=200)
    closes[-60:] = [closes[-1] * 1.05] * 60
    r = A.analyze_abc(make(closes))
    if r["c_stage"] and r["c_stage"].startswith("C2"):
        assert r["c_stage"] == "C2 돌파 없음", r["c_stage"]
        assert r["breakout"] is None


def test_c2_stage_is_state_not_event():
    """돌파 다음날에도 C2가 유지돼야 한다(이벤트로 두면 탭에서 사라진다)."""
    r = A.analyze_abc(make(with_crossing(abc_shape())))
    assert r["breakout"]["bars_ago"] == 4, "이미 며칠 지난 돌파인데"
    assert r["c_stage"].startswith("C2"), r["c_stage"]


# ── C 경계: 겹침·빈틈 (v5.267) ────────────────────────────────────────
def _stage_at(pct, vol_mult=10.0):
    """마지막 봉을 **기준선** 대비 pct%에 정확히 놓고 C단계를 읽는다.

    마지막 종가도 기준선에 들어가므로(1/N 가중) 그냥 `ma*(1+p)`로 두면 목표에서
    어긋난다. x = MA×(1+p)를 만족하는 x를 직접 푼다:
        MA = (S + x)/N,  x = MA(1+p)  →  x = S(1+p)/(N-(1+p))
    (S = 직전 N−1봉 종가 합)
    """
    closes = abc_shape()
    p = pct / 100
    N = CFG["ma_period"]
    S = sum(closes[-(N - 1):])
    x = S * (1 + p) / (N - (1 + p))
    closes = closes[:-1] + [x]
    # 마지막 봉이 MA200을 아래→위로 넘으면 돌파봉이 된다. 진돌이가 되도록
    # 거래량을 키워 둔다(가돌이/진돌이 구분 자체는 별도 테스트가 본다).
    vols = [1000.0] * (len(closes) - 1) + [1000.0 * vol_mult]
    return A.analyze_abc(make(closes, vols=vols))["c_stage"]


def test_c1_and_c2_overlap_is_resolved_toward_c2():
    """**사양이 겹친다**: 사용자 정의 C1 = −5~+5%, C2 = 0~+20% → [0,+5%)가 양쪽.

    코드는 C2를 먼저 본다 → 겹치는 구간은 C2다(= C1은 실질 −5~0%).
    내가 임의로 해소한 뒤 사용자가 확정했다("C2 우선 유지. 확정", 2026-09-18).
    바꾸려면 이 테스트부터 고칠 것.
    """
    assert _stage_at(2.0) == "C2 진돌이", "겹침 구간이 C1로 넘어갔다"
    assert _stage_at(-2.0) == "C1 벽앞"
    cfg = A.ABC_CONFIG
    assert cfg["c1"][1] > cfg["c2"][0], "겹침이 사라졌다면 이 테스트를 지울 것"


def test_c_bands_have_no_silent_gap():
    """−15%~+20% 안에서 단계가 비면(None) 종목이 **조용히 탭에서 사라진다**."""
    for pct in range(-15, 20):
        assert _stage_at(float(pct)) is not None, f"{pct}%에서 단계가 비었다"


def test_outside_the_bands_is_excluded_with_a_reason():
    closes = abc_shape()
    closes = closes[:-1] + [closes[-1] * 0.5]      # −50% — C 구간 밖
    r = A.analyze_abc(make(closes))
    assert r["c_stage"] is None and "C 구간 밖" in r["reason"]


def test_c2_c3_boundary_sits_at_the_config_value():
    """경계가 상수를 따라간다 — 리터럴로 새면 CONFIG를 고쳐도 안 움직인다.

    정확히 c3_min인 지점은 재지 않는다: 마지막 종가를 역산해 만들기 때문에
    부동소수점 오차로 어느 쪽에 떨어질지 갈린다(실제로 20.00%가 C2로 나왔다).
    경계가 **그 값 근처에서 갈리는지**를 본다.
    """
    pct = A.ABC_CONFIG["c3_min"] * 100
    assert _stage_at(pct + 0.5) == "C3 이탈"
    assert _stage_at(pct - 0.5).startswith("C2")
    # 상수를 낮추면 경계도 따라 내려와야 한다(리터럴 하드코딩 감지)
    cfg = dict(A.ABC_CONFIG, c3_min=0.10)
    closes = abc_shape()
    N = CFG["ma_period"]
    S = sum(closes[-(N - 1):]); x = S * 1.15 / (N - 1.15)
    r = A.analyze_abc(make(closes[:-1] + [x]), cfg)
    assert r["c_stage"] == "C3 이탈", r["c_stage"]


# ══════════════════════════════════════════════════════════════════════
# v5.268 — 기준선 600 (사용자 지시: "200으로 되돌리면 FAIL")
# ══════════════════════════════════════════════════════════════════════
def test_ma_period_is_600():
    """사용자 지시로 확정된 값. 되돌리면 여기서 걸린다.

    "ABC 탭 기준선 200MA → 600MA. 핫핑크 = 더양봉맨 장기 추세 전환선."
    (2026-09-18). 측정 근거는 **없다** — 나머지 ABC 임계값과 같은 초기 임의값.
    """
    assert CFG["ma_period"] == 600, f"기준선이 {CFG['ma_period']}로 돌아갔다"


def test_ma200_is_display_only_never_a_gate():
    """MA200은 보조 표시 열 하나로만 남는다(사용자 지시) — 판정에 쓰면 안 된다.

    `ma200_pct`를 만드는 두 줄 말고 다른 곳에서 200을 기준으로 쓰면 실패한다.
    """
    import inspect
    src = inspect.getsource(A.analyze_abc)
    uses = [l.strip() for l in src.splitlines()
            if "_ma(close, 200)" in l or "rolling(200)" in l]
    assert len(uses) == 1, f"200 기준 계산이 여러 곳이다: {uses}"
    assert "보조" in uses[0], "보조 표시 전용이라는 표시가 없다"
    # 보조값을 **계산한 이후**로는 ma200을 다시 읽으면 안 된다.
    # (앵커를 "ma200_pct" 첫 등장으로 잡으면 out 딕셔너리 초기화가 먼저
    #  걸려 검사 범위가 통째로 어긋난다 — 작성 중 실제로 그랬다.)
    anchor = 'out["ma200_pct"] ='
    body = src[src.index(anchor) + len(anchor):]
    body = body[body.index("\n"):]
    assert "ma200" not in body, f"판정 구간에서 ma200을 다시 읽는다: {body[:200]}"


def test_every_judgement_axis_uses_the_config_period():
    """C·B·매물대·돌파 네 축이 전부 기준선을 따라가는가 — 하나라도 200에
    묶여 있으면 기간을 바꿨을 때 그 축만 조용히 옛 기준으로 남는다."""
    closes = abc_shape()
    base = A.analyze_abc(make(closes))
    short_cfg = {**CFG, "ma_period": 300}
    other = A.analyze_abc(make(closes), short_cfg)
    # 기간이 다르면 기준선 값이 달라야 한다(= 축이 cfg를 실제로 읽는다)
    assert base["ma"] != other["ma"], "기간을 바꿔도 기준선이 그대로다"
    assert base["ma_period"] == 600 and other["ma_period"] == 300
    # 그리고 그 차이가 판정 축들에 전달돼야 한다
    assert base["ma_pct"] != other["ma_pct"]
    assert base["b"]["median_vs_ma_pct"] != other["b"]["median_vs_ma_pct"]


def test_supply_band_is_anchored_to_the_config_ma():
    """매물대 밴드가 기준선을 따라가는지 — 리터럴 MA200에 묶이면 안 된다."""
    import inspect
    src = inspect.getsource(A.analyze_abc)
    i = src.index("lo_b, hi_b =")
    line = src[i:src.index("\n", i)]
    assert "ma *" in line and "200" not in line, line


def test_config_has_no_ma200_named_keys():
    """키 이름에 200이 박혀 있으면 값(600)과 이름이 어긋난다."""
    bad = [k for k in CFG if "ma200" in k or "200" in k]
    assert not bad, f"기간이 박힌 키가 남아 있다: {bad}"
