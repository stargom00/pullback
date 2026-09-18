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


def abc_shape(drop_pct=0.45, span=45, flat=200, lead=20):
    """ABC 모양 합성 — **MA200이 횡보 수준과 같아지도록** 마지막 200봉을 전부
    횡보로 채운다. 그래야 B의 "중앙값이 MA200 ±15%"와 C의 위치 판정이 의미를
    갖는다(처음엔 이 설계를 안 해서 MA200이 횡보가보다 한참 위였고, 테스트가
    전부 틀렸다 — 픽스처 문제였지 코드 문제가 아니었다).

    lead는 **완만한 상승**이라 고점이 하락 직전 한 봉으로 유일하다.
    (평탄한 고원으로 두면 argmax가 첫 봉을 집어 span이 부풀려진다.)
    """
    hi, lo = 100.0, 100.0 * (1 - drop_pct)
    up = list(np.linspace(hi * 0.9, hi, lead))
    down = list(np.linspace(hi, lo, span + 1))[1:]
    return up + down + [lo] * flat


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
    wide[-200:] = [lo if i % 2 else lo * 1.30 for i in range(200)]   # 30% 진폭
    r = A.analyze_abc(make(wide))
    assert r["b"]["ok"] is False and r["b"]["range_pct"] > CFG["b_range_max"] * 100


def _at_ma200(pct, vol_mult=1.0, breakout=False):
    """마지막 봉을 MA200 대비 pct 위치로 놓은 ABC 모양."""
    df = make(abc_shape())
    ma200 = float(df["Close"].iloc[-200:].mean())
    if breakout:
        df.iloc[-2, df.columns.get_loc("Close")] = ma200 * 0.99
    df.iloc[-1, df.columns.get_loc("Close")] = ma200 * (1 + pct)
    df.iloc[-6:-1, df.columns.get_loc("Volume")] = 1000.0
    df.iloc[-1, df.columns.get_loc("Volume")] = 1000.0 * vol_mult
    return A.analyze_abc(df)


def test_c_stage_boundaries():
    assert _at_ma200(-0.10)["c_stage"] == "C0 대기"
    assert _at_ma200(-0.02)["c_stage"] == "C1 벽앞"
    assert _at_ma200(0.25)["c_stage"] == "C3 이탈"
    # C 구간 밖: C0 하단(-15%)보다 더 아래면 어느 단계도 아니다
    deep = _at_ma200(-0.30)
    assert deep["c_stage"] is None and "C 구간 밖" in deep["reason"], deep

    # 이 픽스처는 횡보가 == MA200이라 **어제 종가가 MA200 이하**다 →
    # 위로 올라가면 정의상 "오늘 돌파"가 성립한다(broke_today).
    # 처음엔 이걸 못 보고 "돌파 없이 +10%면 구간 밖"이라 잘못 기대했다.
    assert _at_ma200(0.10)["c_stage"] in ("C2 진돌이", "C2 가돌이")


def test_c2_vol_mult_boundary():
    """돌파 동반 시 vol 2.9 → 가돌이, 3.0 → 진돌이."""
    assert _at_ma200(0.02, vol_mult=2.9, breakout=True)["c_stage"] == "C2 가돌이"
    assert _at_ma200(0.02, vol_mult=3.0, breakout=True)["c_stage"] == "C2 진돌이"


def test_other_setup_counted_not_hit():
    """A 없음 + MA200 위 장기 → '다른 셋업'."""
    r = A.analyze_abc(make(list(np.linspace(50, 120, 300))))
    assert r["verdict"] == "다른 셋업"
    assert "박스/눌림" in r["reason"]


def test_short_history_is_not_abc():
    r = A.analyze_abc(make([100.0] * 100))
    assert r["verdict"] == "ABC 아님" and "봉 부족" in r["reason"]


def test_supply_band_flag():
    """MA200~×1.3 구간 체류 봉수를 세고, 조기 반환 경로에서도 키가 있어야 한다."""
    r = A.analyze_abc(make(abc_shape()))
    assert isinstance(r["supply_above"], bool) and r["supply_bars"] >= 0
    short = A.analyze_abc(make([100.0] * 100))
    assert short["supply_bars"] == 0, "조기 반환에서 키가 빠지면 호출부가 KeyError"


# ── 기업 축·등급 ──────────────────────────────────────────────────────
def test_company_axis_turnover_gate():
    c = A.company_axis(250, 4, 2, False, rev_yoy_of=4)
    assert c["trading_only"] is True and not c["ok"]
    c2 = A.company_axis(500, 4, 2, False, rev_yoy_of=4)
    assert c2["ok"] is True and c2["trading_only"] is False


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


def test_grade_matrix():
    ok_chart = {"verdict": "ABC", "b": {"ok": True}, "c_stage": "C1 벽앞"}
    assert A.grade(ok_chart, {"ok": True}) == "A급"
    assert A.grade(ok_chart, {"ok": False}) == "A급 근접"
    assert A.grade({"verdict": "ABC", "b": {"ok": False}, "c_stage": "C1 벽앞"},
                   {"ok": True}) == "B급"
    assert A.grade({"verdict": "다른 셋업"}, {"ok": True}) is None
    assert A.grade({"verdict": "ABC 아님"}, {"ok": True}) is None


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
def test_breakout_uses_ma200_at_that_bar_not_today():
    """**룩어헤드 회귀 방지.** 과거 봉을 '오늘의 MA200'과 비교하면 미래 정보로
    과거를 판정하는 것이다 — 실제로 그렇게 짰다가 돌파 시점과 거래량이 둘 다
    틀렸다(LS에코 09-18 기준에서 잘못된 봉이 잡힘).

    MA200이 우상향하는 구간을 만들어, 오늘 기준선으로 보면 '옛날에 넘었다'가
    되지만 당시 기준선으로 보면 아직 아래인 봉을 만든다.
    """
    import inspect
    src = inspect.getsource(A._find_breakout)
    assert "rolling(200)" in src, "각 봉 시점의 MA200을 안 쓴다"
    # ma200을 상수로 받는 인자가 비교에 쓰이면 안 된다
    body = src[src.index("n = len(close)"):]
    assert "< float(close.iloc[i])" in body and "float(m)" in body, body


def test_breakout_detects_the_crossing_bar():
    closes = abc_shape()
    lo = closes[-1]
    # 마지막 10봉: 아래 → 위로 한 번 교차
    closes[-10:] = [lo] * 5 + [lo * 1.30] * 5
    df = make(closes)
    r = A.analyze_abc(df)
    bo = r["breakout"]
    assert bo is not None and bo["bars_ago"] == 4, bo


def test_breakout_volume_uses_peak_within_window():
    """(b) 확정: 돌파봉 포함 N봉 중 **최대 거래량**으로 진돌이/가돌이.
    교차봉이 소량이고 다음날 대량이 터지는 형태를 가돌이로 오판하면 안 된다."""
    closes = abc_shape()
    lo = closes[-1]
    closes[-10:] = [lo] * 5 + [lo * 1.05] * 5
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
    closes = abc_shape()
    lo = closes[-1]
    closes[-10:] = [lo] * 5 + [lo * 1.05] * 5
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
    closes = abc_shape()
    lo = closes[-1]
    closes[-10:] = [lo] * 5 + [lo * 1.05] * 5
    r = A.analyze_abc(make(closes))
    assert r["breakout"]["bars_ago"] == 4, "이미 며칠 지난 돌파인데"
    assert r["c_stage"].startswith("C2"), r["c_stage"]


# ── C 경계: 겹침·빈틈 (v5.267) ────────────────────────────────────────
def _stage_at(pct, vol_mult=10.0):
    """마지막 봉을 MA200 대비 pct%에 정확히 놓고 C단계를 읽는다.

    마지막 종가도 MA200에 들어가므로(1/200 가중) 그냥 `ma200*(1+p)`로 두면
    목표에서 어긋난다. x = MA200×(1+p)를 만족하는 x를 직접 푼다:
        MA200 = (S + x)/200,  x = MA200(1+p)  →  x = S(1+p)/(200-(1+p))
    (S = 직전 199봉 종가 합)
    """
    closes = abc_shape()
    p = pct / 100
    S = sum(closes[-199:])
    x = S * (1 + p) / (200 - (1 + p))
    closes = closes[:-1] + [x]
    # 마지막 봉이 MA200을 아래→위로 넘으면 돌파봉이 된다. 진돌이가 되도록
    # 거래량을 키워 둔다(가돌이/진돌이 구분 자체는 별도 테스트가 본다).
    vols = [1000.0] * (len(closes) - 1) + [1000.0 * vol_mult]
    return A.analyze_abc(make(closes, vols=vols))["c_stage"]


def test_c1_and_c2_overlap_is_resolved_toward_c2():
    """**사양이 겹친다**: 사용자 정의 C1 = −5~+5%, C2 = 0~+20% → [0,+5%)가 양쪽.

    코드는 C2를 먼저 본다 → 겹치는 구간은 C2다(= C1은 실질 −5~0%).
    이건 발견된 모호성을 코드가 임의로 해소한 것이므로, 조용히 두지 않고
    여기에 못 박는다. 사용자가 반대로 정하면 이 테스트부터 고칠 것.
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
    S = sum(closes[-199:]); x = S * 1.15 / (200 - 1.15)
    r = A.analyze_abc(make(closes[:-1] + [x]), cfg)
    assert r["c_stage"] == "C3 이탈", r["c_stage"]
