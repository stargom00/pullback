"""RSI 상승 다이버전스(가격은 더 낮은 저점, RSI는 더 높은 저점) — 저점 관찰 종목에 붙이는 표시 전용 판정. v5.399.

사용자 2026-11-23: "가격은 계속 하락하는데 RSI가 점점 올라가면 다이버전스잖아" → "저점종목 중 다이버전스가 나타나는 종목 찾을 수있어?"
→ (11-20 종가 기준 목록을 보고) "응 붙여줘".

정의(아래 숫자는 전부 **AI(Claude) 판단으로 정한 어림값** — 웹 검색·측정 없이 설정, 재검토 필요):
  저점      = 종가가 앞뒤 PIVOT_K봉 종가보다 낮거나 같은 봉(가장 낮은 값). 마지막 PIVOT_K봉 안의 봉은 오른쪽이 덜 찼으므로
              "그 뒤 봉들보다 낮으면" 후보로 넣고 b_confirmed=False로 표시(다음 봉이 더 내려가면 저점이 바뀐다).
  다이버전스 = 가장 최근 저점 B가 최근 RECENT봉 안에 있고, 바로 앞 저점 A(B와 MIN_GAP봉 이상 떨어진)보다
              종가는 낮은데(B < A) RSI는 RSI_GAP 이상 높고, A의 RSI < RSI_A_MAX(과매도권).
  RSI       = lowpoint.rsi_wilder_sma(고전 Wilder 14, 저점 스크린과 같은 함수 — 사본 없음).
2026-11-23 11-20 종가 기준 저점 신호(10월 이후) 종목에서 이 정의로 113종목(일봉 63·주봉 56건).

**표시 전용 · 측정 전.** 저점 판정·단계·순위 어디에도 넣지 않는다. 승률·우위 주장 금지.
내부 요구 봉 수: 최소 LOOKBACK[tf] + RSI 14봉 — 그보다 짧으면 RSI가 NaN이라 다이버전스가 안 잡힌다(None).
"""
from __future__ import annotations

import math

import pandas as pd

import lowpoint as lp

PIVOT_K = 3                          # 저점 = 앞뒤 3봉보다 낮은 종가 (AI 어림값)
LOOKBACK = {"day": 60, "week": 40}   # 저점을 찾는 창(봉) (AI 어림값)
RECENT = {"day": 15, "week": 8}      # 가장 최근 저점이 이 안에 있어야 "지금" 다이버전스 (AI 어림값)
MIN_GAP = {"day": 5, "week": 3}      # 두 저점 최소 간격(봉) (AI 어림값)
RSI_GAP = 3.0                        # RSI가 이만큼 이상 높아야 (AI 어림값 — 0.1 차이 같은 잡음 제외)
RSI_A_MAX = 35.0                     # 앞 저점 RSI가 과매도권일 때만 (AI 어림값)


def bull_div(close: "pd.Series | None", tf: str) -> "dict | None":
    """확정 봉 종가(마지막 = 판정 봉) → {a_date, a_close, a_rsi, b_date, b_close, b_rsi, b_confirmed, last_rsi} 또는 None."""
    if close is None:
        return None
    c = close.dropna().astype(float)
    n = len(c)
    if n < PIVOT_K * 2 + 2:
        return None
    r = lp.rsi_wilder_sma(c)
    piv = [i for i in range(max(PIVOT_K, n - LOOKBACK[tf]), n - PIVOT_K)
           if c.iloc[i] == c.iloc[i - PIVOT_K:i + PIVOT_K + 1].min()]
    piv += [i for i in range(max(PIVOT_K, n - PIVOT_K), n) if c.iloc[i] == c.iloc[i - PIVOT_K:].min()]
    piv = sorted(set(piv))
    if len(piv) < 2 or piv[-1] < n - RECENT[tf]:
        return None
    b = piv[-1]
    prev = [i for i in piv[:-1] if b - i >= MIN_GAP[tf]]
    if not prev:
        return None
    a = prev[-1]
    ra, rb = float(r.iloc[a]), float(r.iloc[b])
    if math.isnan(ra) or math.isnan(rb):
        return None
    if not (c.iloc[b] < c.iloc[a] and rb - ra >= RSI_GAP and ra < RSI_A_MAX):
        return None
    d = lambda i: str(pd.Timestamp(c.index[i]).date())
    return {"a_date": d(a), "a_close": round(float(c.iloc[a]), 4), "a_rsi": round(ra, 1),
            "b_date": d(b), "b_close": round(float(c.iloc[b]), 4), "b_rsi": round(rb, 1),
            "b_confirmed": b < n - PIVOT_K, "last_rsi": round(float(r.iloc[-1]), 1)}


def weekly_closed(close: pd.Series, through: "str | None") -> pd.Series:
    """확정 일봉 → 다 끝난 주봉 종가만(W-FRI). 주 라벨(금요일)이 through보다 뒤면 그 주는 아직 진행 중이라 뺀다
    (금요일이 휴장인 주는 다음 확정일이 지나야 들어간다 — 하루 늦을 뿐 미확정 봉은 안 쓴다)."""
    bars = lp.resample_bars(close, "week")
    if through:
        bars = bars[bars.index <= pd.Timestamp(through)]
    return bars["close"]


def div_fields(daily: "pd.DataFrame | None", through: "str | None") -> dict:
    """정제·확정된 일봉 → 관찰 레코드 필드 {div_day, div_week}."""
    if daily is None or daily.empty:
        return {"div_day": None, "div_week": None}
    c = daily["Close"]
    return {"div_day": bull_div(c, "day"), "div_week": bull_div(weekly_closed(c, through), "week")}
