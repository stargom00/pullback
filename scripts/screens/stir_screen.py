"""일봉 준비(세력 꿈틀이 검색식) — 저점 관찰 종목에 붙이는 표시 전용 판정. v5.398.

사용자 2026-11-22~23: Threads @jtdj_official2 "11장. 세력 꿈틀이 검색기"(2026년 하반기 주식 차트 스터디 제11기, 키움 [0150]
조건검색 슬라이드 3/9~7/9)를 보내며 "저 조건식 네가 검색할수있어?" → "이게 내가 하는 저점종목매수랑 연결시킬수잇을것 같아 …
이건 이제 막 움직이려는 거잖아" → 표시 + 측정 "2번까지 같이해".

조건(슬라이드 원문 그대로 — 숫자는 사용자가 확인: "115이평이야", "55555주야", C "50이상"):
  A  주가이평배열(3) [일]0봉전 20이평 < 60이평 < 115이평 — 이평 종류 **지수**(슬라이드 3/9 드롭다운)
  B  [일]1봉전 5봉 평균거래량 55,555 이상(금일 제외)
  C  5일 평균거래대금(단위: 백만) 50 이상, 금일 제외
  D  가격-이동평균 비교 [일]0봉전 (종가 20)이평 < 종가 — 이평 종류 **지수**(6/9)
  E  이평이격도 [일]0봉전 (종가 20, 종가 60) 2% 이내 근접 1회 이상 — 이평 종류 **단순**(7/9)
  조건식 A and (B or C) and D and E. 대상 제외(우선주·거래정지·정리매매·ETF·스팩·ETN)는 저점 유니버스가 이미 거른다.
검증(2026-11-22, 11-20 종가, naver 통합 일봉): 국내 20종목 — 작가 결과 19종목 중 화면에 보이는 9종목 전부 포함.
지수이평은 pandas ewm(span=n, adjust=False) — 키움 지수이평과 같은 2/(n+1). 시작값 차이로 이력이 짧으면 값이 조금 다를 수 있다.

**표시 전용 · 측정 전.** 저점 판정·단계·순위 어디에도 넣지 않는다. 측정: docs/lowpoint_stir_filter.md.
내부 요구 봉 수: MIN_BARS(116 — 115 지수이평 + 1봉전 거래량 5봉). 그보다 짧으면 판정하지 않는다(stir None).
"""
from __future__ import annotations

import pandas as pd

MA_SHORT, MA_MID, MA_LONG = 20, 60, 115
VOL_MIN = 55_555            # B: 1봉전 5봉 평균거래량(주)
VALUE_MIN_MIL = 50          # C: 금일 제외 5일 평균거래대금(백만 원)
GAP_PCT = 2.0               # E: 단순 20·60 이격 %
AVG_N = 5
MIN_BARS = MA_LONG + 1


def stir_check(daily: "pd.DataFrame | None") -> dict:
    """확정·정제된 일봉(마지막 봉 = 판정일) → {stir, stir_parts{A,BC,D,E}, stir_date, stir_gap_pct, stir_ema(20·60·115)}.
    판정 못 하면 stir None(봉 부족·값 없음)."""
    out = {"stir": None, "stir_parts": None, "stir_date": None, "stir_gap_pct": None, "stir_ema": None}
    if daily is None or len(daily) < MIN_BARS:
        return out
    c = daily["Close"].astype(float)
    v = daily["Volume"].astype(float)
    if c.isna().iloc[-MIN_BARS:].any() or not (c.iloc[-1] > 0):
        return out
    ema = {n: float(c.ewm(span=n, adjust=False).mean().iloc[-1]) for n in (MA_SHORT, MA_MID, MA_LONG)}
    s20, s60 = float(c.rolling(MA_SHORT).mean().iloc[-1]), float(c.rolling(MA_MID).mean().iloc[-1])
    prev_v = v.iloc[-AVG_N - 1:-1]
    prev_val = (c * v).iloc[-AVG_N - 1:-1]
    gap = (s20 / s60 - 1) * 100
    parts = {
        "A": ema[MA_SHORT] < ema[MA_MID] < ema[MA_LONG],
        "BC": bool(prev_v.mean() >= VOL_MIN or prev_val.mean() / 1e6 >= VALUE_MIN_MIL),
        "D": ema[MA_SHORT] < float(c.iloc[-1]),
        "E": abs(gap) <= GAP_PCT,
    }
    return {"stir": all(parts.values()), "stir_parts": parts, "stir_date": str(pd.Timestamp(daily.index[-1]).date()),
            "stir_gap_pct": round(gap, 2), "stir_ema": {str(k): round(x, 2) for k, x in ema.items()}}
