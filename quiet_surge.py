"""조용하던 종목의 거래량 폭발 — v5.379 (사용자 지시 2026-11-10 "거래량이 마르고 조용하던 종목이 어는날 거래량이 터졌을때, 그거잡을수있어?"
→ 시점 "c"(장중+장마감 둘 다), 대상 "c 나스닥도"(KR 전체 + US, 둘 다 따로 표시)).

**관심 신호 · 측정 전.** 아래 숫자는 전부 AI 어림값이다(웹 검색·백테스트 근거 없음, 사용자에게 고지). 승률·우위 주장 금지.
판정 함수는 순수 함수 — 일봉 df와 봉 위치만 받는다(app.py 엔드포인트·테스트가 같은 함수를 쓴다).
"""
from __future__ import annotations

import pandas as pd

QUIET_SHORT = 20        # 조용한 구간 = 직전 20봉 평균 거래량 (AI 어림값)
QUIET_LONG = 60         # 비교 구간 = 그 앞 60봉 평균 (AI 어림값)
QUIET_MAX = 0.6         # 20봉 평균 ≤ 60봉 평균 × 0.6이면 "조용했다" (AI 어림값)
SURGE_MIN = 5.0         # 그날 거래량 ≥ 20봉 평균 × 5 (장중은 경과 시간 비율로 환산) (AI 어림값)
CLOSE_POS_MIN = 0.5     # 양봉 + 종가가 그날 범위 위쪽 절반 (AI 어림값)
HALT_GAP_DAYS = 7       # 급등일 포함 최근 6봉 사이에 달력 7일 넘는 공백 = 거래정지 후 재개로 보고 제외 (AI 어림값)
FOLLOW_DAYS = 3         # 급등 뒤 며칠 거래량을 보여줄지(사용자 확인 — "다음 3일 거래량 유지" 표시)
MIN_VALUE = {"kr": 5e8, "us": 2e6}   # 그날(지금까지) 거래대금 하한 — KR 5억원·US 200만달러 (AI 어림값, 초소형 잡음 제외)
MIN_BARS = QUIET_SHORT + QUIET_LONG + 1


def evaluate_at(df: pd.DataFrame, i: int, market: str, elapsed: float = 1.0) -> "dict | None":
    """i번째 봉(위치)을 급등일로 볼 때 조건을 모두 만족하면 dict, 아니면 None.
    elapsed = 장중 경과 비율(0~1). 마감 봉은 1.0. 내부 요구 봉 수 = MIN_BARS(i ≥ 80)."""
    if i < MIN_BARS - 1 or i >= len(df):
        return None
    v = df["Volume"].astype(float).to_numpy()
    short = float(v[i - QUIET_SHORT:i].mean())
    long = float(v[i - QUIET_SHORT - QUIET_LONG:i - QUIET_SHORT].mean())
    if not (short > 0 and long > 0):
        return None
    quiet = short / long
    if quiet > QUIET_MAX:
        return None
    ratio = float(v[i]) / (short * max(elapsed, 0.01))
    if ratio < SURGE_MIN:
        return None
    o, h, lo, c = (float(df[k].iloc[i]) for k in ("Open", "High", "Low", "Close"))
    if not (c > o > 0):
        return None
    pos = (c - lo) / (h - lo) if h > lo else 1.0
    if pos < CLOSE_POS_MIN:
        return None
    if c * v[i] < MIN_VALUE[market]:
        return None
    idx = pd.DatetimeIndex(df.index[max(0, i - 5):i + 1])
    if len(idx) > 1 and (idx[1:] - idx[:-1]).max() > pd.Timedelta(days=HALT_GAP_DAYS):
        return None
    prev = float(df["Close"].iloc[i - 1])
    return {"date": str(pd.Timestamp(df.index[i]).date()), "close": c, "change_pct": (c / prev - 1) * 100 if prev > 0 else None,
            "surge_ratio": ratio, "quiet_ratio": quiet, "avg20": short, "close_pos": pos, "value": c * float(v[i])}


def followup(df: pd.DataFrame, i: int, market: str) -> "dict | None":
    """i번째 봉이 (마감 기준) 급등일이었으면 그 뒤 봉들의 거래량(급등 전 20봉 평균 대비 배수)과 종가(급등일 종가 대비 %)."""
    hit = evaluate_at(df, i, market)
    if not hit:
        return None
    after = []
    for j in range(i + 1, min(len(df), i + 1 + FOLLOW_DAYS)):
        after.append({"date": str(pd.Timestamp(df.index[j]).date()),
                      "vol_x": float(df["Volume"].iloc[j]) / hit["avg20"],
                      "close_vs_surge_pct": (float(df["Close"].iloc[j]) / hit["close"] - 1) * 100})
    return {**hit, "after": after}
