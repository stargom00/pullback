"""지수 하락 구간에서 덜 빠진 종목 — v5.385 (사용자 지시 2026-11-19: "지수빠질때 덜빠지는 종목은 어떻게 선별해?" → 기준 설명 후
"만들어줘"). 계기: 트레이더 율 채널(11-19) "지수 회복할 때 반등탄력 좋은 종목은 무엇인가" — claude/트레이더율_채널_노트.md.

구간 = 지수(코스피/코스닥, 종목이 속한 시장) 최근 PEAK_LOOKBACK봉 안의 최고 종가일(고점) → 그 뒤 최저 종가일(저점) → 오늘.
종목별:
  dd      = 고점일 종가 → 저점일 종가 수익률            (지수 하락 동안)
  rel_dd  = dd − 지수 dd                                  (+면 지수보다 덜 빠짐) ← 정렬 기준
  reb     = 저점일 종가 → 오늘 종가 수익률, rel_reb = reb − 지수 reb (회복 탄력)
  low_ok  = 종목의 [고점일..오늘] 최저 종가가 지수 저점일 **이전**에 나왔다 = 지수가 새 저점을 찍을 때 종목은 저점을 안 깼다
  ma50_ok = 오늘 종가 > 50일 단순평균
**관심 신호 · 측정 전.** "하락장에서 덜 빠진 종목이 회복기에 먼저 오른다"는 O'Neil·Minervini의 관찰이고 이 정의로 잰 적 없다
(승률·우위 주장 금지). 방어주·헷지주도 덜 빠지므로 업종을 같이 보여 준다(판정에 안 씀).

상수 출처(CLAUDE.md "새 CONFIG 조건은 출처 기록"):
  PEAK_LOOKBACK = 60   — AI(Claude) 어림값, 웹 검색·측정 없음. 약 3개월 안의 "최근" 고점.
  MIN_INDEX_DD  = -0.05 — AI 어림값. 지수 하락이 −5%보다 얕으면 "의미 있는 하락 구간 없음"으로 표시(그래도 계산은 함).
  MA_LEN        = 50   — 사용자에게 설명한 기준 ④(50일선 위 유지, O'Neil/Minervini 관례) 그대로.
"""
from __future__ import annotations

import pandas as pd

PEAK_LOOKBACK = 60
MIN_INDEX_DD = -0.05
MA_LEN = 50


def index_window(close: pd.Series) -> dict | None:
    """지수 종가 → 구간. 내부 요구 봉 수 = 2(최근 PEAK_LOOKBACK봉 안에서 고점·저점). 고점이 오늘이면 저점도 오늘(dd 0)."""
    c = close.dropna().astype(float)
    if len(c) < 2:
        return None
    w = c.iloc[-PEAK_LOOKBACK:]
    p_i = int(w.values.argmax())
    after = w.iloc[p_i:]
    t_i = p_i + int(after.values.argmin())
    peak_d, trough_d = w.index[p_i], w.index[t_i]
    pk, tr, now = float(w.iloc[p_i]), float(w.iloc[t_i]), float(w.iloc[-1])
    return {"peak_date": _d(peak_d), "trough_date": _d(trough_d), "asof": _d(w.index[-1]),
            "peak": pk, "trough": tr, "now": now, "dd": tr / pk - 1, "reb": now / tr - 1,
            "meaningful": tr / pk - 1 <= MIN_INDEX_DD}


def _d(x) -> str:
    return pd.Timestamp(x).strftime("%Y-%m-%d")


def _close_on(c: pd.Series, day: str):
    """그 날짜 이하 마지막 종가(거래정지·휴장 차이 흡수). 없으면 None."""
    s = c[pd.DatetimeIndex(c.index).strftime("%Y-%m-%d") <= day]
    return float(s.iloc[-1]) if len(s) else None


def stock_row(df: pd.DataFrame, w: dict) -> dict | None:
    """종목 일봉(Close) + 지수 구간 → 지표. 내부 요구 봉 수 = MA_LEN(50일선) + 고점일 이전 1봉. 부족하면 None."""
    c = df["Close"].dropna().astype(float)
    if len(c) < MA_LEN:
        return None
    days = pd.DatetimeIndex(c.index).strftime("%Y-%m-%d")
    if days[0] > w["peak_date"]:
        return None                                   # 고점일 이후 상장 — 비교 불가
    pk, tr, now = _close_on(c, w["peak_date"]), _close_on(c, w["trough_date"]), float(c.iloc[-1])
    if not pk or not tr:
        return None
    dd, reb = tr / pk - 1, now / tr - 1
    seg = c[days >= w["peak_date"]]
    low_day = _d(seg.idxmin())
    return {"dd": dd, "rel_dd": dd - w["dd"], "reb": reb, "rel_reb": reb - w["reb"],
            "low_ok": low_day < w["trough_date"],
            "ma50_ok": now > float(c.iloc[-MA_LEN:].mean()), "close": now}


def sector_table(rows: list[dict], min_n: int = 3) -> list[dict]:
    """업종별 rel_dd 중앙값(구성 min_n 이상) — 표시 전용. min_n = AI 어림값(3종목 미만은 우연)."""
    by: dict = {}
    for r in rows:
        s = r.get("sector") or "기타"
        if s == "기타":
            continue
        by.setdefault(s, []).append(r["rel_dd"])
    out = [{"sector": s, "n": len(v), "rel_dd_med": float(pd.Series(v).median())} for s, v in by.items() if len(v) >= min_n]
    return sorted(out, key=lambda x: -x["rel_dd_med"])
