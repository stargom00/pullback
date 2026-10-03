"""저점 탭 "평가" — 장기 체크리스트 자동 판정 · 단기 비교 지표 (v5.317, 사용자 지시).

사용자 지시 요지: "저점 탭에 종목 평가 페이지 추가. 장기 후보를 체크리스트로 O/X 평가해 관심종목 판정,
단기 후보는 '뭐가 먼저 +5% 가는지' 비교. 수동 표 대신 서버 데이터로 자동 판정."

데이터: 평가하는 **그 종목만** 기존 조회 함수로 받는다(새 소스 없음).
  KR    naver_kr.fetch_history(코드, days=lowpoint.KR_DAYS["month"])   — 일봉 OHLCV(수정주가), 약 10년
  US    harness._fetch_us_batch([티커], period=lowpoint.US_PERIOD["month"]) — yfinance 일봉, 10년
  UPBIT upbit.fetch_candles(마켓, "days"/"months") — 공개 API 1회 최대 200봉(일봉 약 200일, 월봉 약 16년)
월봉은 KR·US는 일봉을 달력 월말로 묶고, 코인은 업비트 월봉을 그대로 쓴다. **진행 중인 이번 달 봉은 뺀다**
(저점 스크린과 같은 마감 판정 lowpoint.is_bar_closed — 코인은 KST 달력 월).
한계: "상장 후 신고가"는 조회 기간(KR·US 최대 약 10년) 안의 최고 종가다. 코인의 일봉 지표(⑤⑦⑧⑨)는 최근
200일 안에서만 계산한다.

[장기 체크리스트 — 자동] 결과는 True(O)/False(X)/None(데이터 부족 — 미판정).
  ① 완성 월봉 12개 초과                                            (사용자 지시 값)
  ② 상장 후 최고 종가 대비 −50% 이하                               (사용자 지시 값, 계산은 newlisting.drawdown_pct)
  ③ 월봉 RSI(14)<30으로 마감한 달의 **다음 달**이 양봉(종가>시가)으로 마감한 적이 있다
  ④ 월봉 StochRSI %K가 0 근접(≤ STOCH_NEAR_ZERO)으로 마감한 달이 있다
  ⑤ 급등 전력 없음 — **신규상장 스크린의 surge_check를 그대로 호출**(5배·365일, 사본 금지)
  ⑥ 저점 높이기 — 최근 완성 월봉 6개의 최저가 ≥ 그 직전 6개의 최저가
  ⑦ 현재가(마지막 일봉 종가) > 20일 단순이동평균
  ⑧ 현재가 위 +30% 구간 매물대 비중 ≤ VP_UP30_MAX_PCT — 낮을수록 위가 가볍다(O)
  ⑨ 최근 20일 평균 거래대금 > 그 직전 60일 평균 거래대금
  매물대 = **일봉 종가 × 거래량 근사**: 그날 거래량 전부가 그날 종가에 있었다고 보고, 전 기간 거래량 중
  종가가 (현재가, 현재가×1.3] 안인 날의 거래량 비중(%)이다(UI에도 명기).

[단기 비교 — 자동] ATR%(14일, scanner.atr) · 위 +5% 구간 매물대 비중 · 5일 거래대금 증가율(최근 5일 평균 ÷
직전 5일 평균 − 1) · 최근 10일 수익률(후보 안 RS 순위의 근거, 1위 = 가장 강함).

[처음엔 AI가 정한 값 → 사용자 승인 2026-10-04 — 원문 "기준값 4개 모두 승인(③은 '바로 다음 달' 유지)"] STOCH_NEAR_ZERO=5(%K 0~100 척도), VP_UP30_MAX_PCT=20,
"5일 거래대금 증가율"의 비교 구간(직전 5일), ③의 "RSI<30 마감 후 양봉" = 바로 다음 달. 웹 검색·백테스트
없이 제안한 값을 사용자가 승인한 것이다(성과 검증은 아님).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "scripts", "measurements")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import lowpoint as lp      # noqa: E402 — 마감 판정·조회 기간 재사용
import newlisting as nl    # noqa: E402 — 급등 판정(surge_check)·하락률(drawdown_pct) 재사용

# 사용자 지시 값
MIN_MONTHS = 12
DRAWDOWN_MAX = -50.0
RSI_OVERSOLD = 30.0
HL_MONTHS = 6
MA_DAYS = 20
VP_UP_LONG_PCT = 30.0
TV_RECENT, TV_PRIOR = 20, 60
SHORT_VP_UP_PCT = 5.0
SHORT_RET_DAYS = 10
ATR_DAYS = 14
# AI 제안 → 사용자 승인 2026-10-04(모듈 docstring)
STOCH_NEAR_ZERO = 5.0
VP_UP30_MAX_PCT = 20.0
TV_SHORT_DAYS = 5

LONG_ITEMS = [
    ("months", "월봉 12개 초과"),
    ("drawdown", "상장 후 신고가 대비 −50% 이하"),
    ("rsi_rebound", "월봉 RSI<30 마감 다음 달 양봉 마감"),
    ("stoch_zero", "월봉 StochRSI 0 근접 마감"),
    ("no_surge", "1년 내 5배 급등 전력 없음"),
    ("higher_lows", "저점 높이기(최근 6개월 저가 ≥ 직전 6개월)"),
    ("above_ma20", "20일선 위"),
    ("light_overhead", "위 +30% 구간 매물대 비중 낮음"),
    ("tv_rising", "거래대금 증가(20일 평균 > 직전 60일)"),
]
MANUAL_ITEMS = [
    ("rise_2x", "의미있는 상승이 나왔나(2곳)"),
    ("long_base", "긴 횡보를 거쳤나"),
    ("dilution", "희석 이력(유증·CB)"),
    # v5.318(사용자 지시) — 같은 O/X/미표시 토글. 예전 레코드엔 이 키가 없어 미표시로 시작한다.
    ("ichimoku_cloud", "파란구름의 두꺼운 구간을 충분히 지났다(월봉 일목 구름 기준)"),
]


# ── 월봉 ───────────────────────────────────────────────────────────────

def monthly_ohlc(daily: pd.DataFrame) -> pd.DataFrame:
    """일봉 → 달력 월말 라벨 월봉(시가=첫 봉, 고가=최대, 저가=최소, 종가=마지막, 거래량=합)."""
    g = daily.resample(lp.RULE["month"])
    out = pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(), "Low": g["Low"].min(),
                        "Close": g["Close"].last(), "Volume": g["Volume"].sum()})
    return out.dropna(subset=["Close"])


def completed_months(monthly: pd.DataFrame, mkt: str, now: datetime) -> pd.DataFrame:
    """진행 중인 달을 뺀다. KR·US는 저점 스크린의 마감 판정 그대로, 코인은 KST 달력 월."""
    if monthly is None or monthly.empty:
        return monthly
    if mkt == "UPBIT":
        cur = now.astimezone(lp.KST)
        keep = [not (i.year == cur.year and i.month == cur.month) for i in monthly.index]
        return monthly[keep]
    market = "us" if mkt == "US" else "kr"
    labels = [pd.Timestamp(i.year, i.month, 1) + pd.offsets.MonthEnd(0) for i in monthly.index]
    keep = [lp.is_bar_closed(lbl, market, now) for lbl in labels]
    return monthly[keep]


def stoch_rsi_k(close: pd.Series, period: int = 14, smooth: int = 3) -> pd.Series:
    """StochRSI %K(0~100) = RSI의 period 구간 최저~최고 안 위치를 smooth로 평활. RSI는 scanner.rsi 그대로."""
    import scanner
    r = scanner.rsi(close, period)
    lo, hi = r.rolling(period).min(), r.rolling(period).max()
    st = (r - lo) / (hi - lo).replace(0, float("nan")) * 100
    return st.rolling(smooth).mean()


# ── 일봉 지표 ──────────────────────────────────────────────────────────

def volume_share_above(daily: pd.DataFrame, up_pct: float) -> float | None:
    """매물대(일봉 종가×거래량 근사): 전 기간 거래량 중 종가가 (현재가, 현재가×(1+up_pct%)] 안인 날의 비중 %."""
    if daily is None or daily.empty:
        return None
    c, v = daily["Close"], daily["Volume"].fillna(0)
    total = float(v.sum())
    if total <= 0:
        return None
    cur = float(c.iloc[-1])
    mask = (c > cur) & (c <= cur * (1 + up_pct / 100))
    return round(float(v[mask].sum()) / total * 100, 2)


def _trade_value(daily: pd.DataFrame) -> pd.Series:
    return daily["Close"] * daily["Volume"].fillna(0)


def _n(x) -> str:
    """상세 문구용 숫자 — 큰 값은 천 단위 구분(지수 표기 금지), 작은 값은 유효숫자 4자리."""
    x = float(x)
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:.4g}"


def _item(key, label, result, value=None, detail=""):
    return {"key": key, "label": label, "result": result, "value": value, "detail": detail, "auto": True}


def long_checks(daily: pd.DataFrame, months: pd.DataFrame) -> list:
    """장기 체크리스트 9개(자동). months = completed_months() 결과."""
    lab = dict(LONG_ITEMS)
    out = []
    n = 0 if months is None else len(months)
    out.append(_item("months", lab["months"], (n > MIN_MONTHS) if n else None, n, f"완성 월봉 {n}개"))

    c = daily["Close"] if daily is not None and not daily.empty else None
    if c is None:
        return out + [_item(k, lab[k], None, None, "일봉 없음") for k, _ in LONG_ITEMS[1:]]
    # 최고 종가 = 일봉·완성 월봉 종가 중 최대(코인은 일봉이 200일뿐이라 월봉이 상장 이후 전체를 덮는다)
    peak, peak_at = float(c.max()), c.idxmax()
    if n and float(months["Close"].max()) > peak:
        peak, peak_at = float(months["Close"].max()), months["Close"].idxmax()
    dd = nl.drawdown_pct(float(c.iloc[-1]), peak)
    out.append(_item("drawdown", lab["drawdown"], None if dd is None else dd <= DRAWDOWN_MAX, dd,
                     f"최고 종가 {_n(peak)}({peak_at.date()}) 대비 {dd}%" if dd is not None else "계산 불가"))

    if n >= 2:
        mr = __import__("scanner").rsi(months["Close"], 14)
        bull = months["Close"] > months["Open"]
        hits = [months.index[i + 1] for i in range(n - 1) if mr.iloc[i] < RSI_OVERSOLD and bool(bull.iloc[i + 1])]
        out.append(_item("rsi_rebound", lab["rsi_rebound"], bool(hits), len(hits),
                         f"해당 {len(hits)}회" + (f" · 최근 {hits[-1].strftime('%Y-%m')}" if hits else "")))
        k = stoch_rsi_k(months["Close"])
        near = k[k <= STOCH_NEAR_ZERO].dropna()
        valid = int(k.notna().sum())
        out.append(_item("stoch_zero", lab["stoch_zero"], bool(len(near)) if valid else None, len(near),
                         f"%K≤{STOCH_NEAR_ZERO:g} {len(near)}회" + (f" · 최근 {near.index[-1].strftime('%Y-%m')}"
                                                                   if len(near) else "") if valid else "월봉 부족"))
    else:
        out += [_item("rsi_rebound", lab["rsi_rebound"], None, None, "월봉 부족"),
                _item("stoch_zero", lab["stoch_zero"], None, None, "월봉 부족")]

    ratio, when = nl.surge_check(c)   # 신규상장 스크린과 같은 함수(사본 금지)
    out.append(_item("no_surge", lab["no_surge"], None if ratio is None else ratio < nl.SURGE_RATIO, ratio,
                     f"직전 365일 저점 대비 최대 {ratio}배({when})" if ratio is not None else "계산 불가"))

    if n >= HL_MONTHS * 2:
        recent, prior = float(months["Low"].iloc[-HL_MONTHS:].min()), float(months["Low"].iloc[-2 * HL_MONTHS:-HL_MONTHS].min())
        out.append(_item("higher_lows", lab["higher_lows"], recent >= prior, round(recent / prior, 4) if prior else None,
                         f"최근 6개월 저가 {_n(recent)} vs 직전 6개월 {_n(prior)}"))
    else:
        out.append(_item("higher_lows", lab["higher_lows"], None, None, f"완성 월봉 {HL_MONTHS * 2}개 필요"))

    if len(c) >= MA_DAYS:
        ma = float(c.iloc[-MA_DAYS:].mean())
        out.append(_item("above_ma20", lab["above_ma20"], float(c.iloc[-1]) > ma, round(ma, 4),
                         f"종가 {_n(c.iloc[-1])} / 20일선 {_n(ma)}"))
    else:
        out.append(_item("above_ma20", lab["above_ma20"], None, None, "일봉 20개 필요"))

    vp = volume_share_above(daily, VP_UP_LONG_PCT)
    out.append(_item("light_overhead", lab["light_overhead"], None if vp is None else vp <= VP_UP30_MAX_PCT, vp,
                     f"위 +30% 구간 비중 {vp}% (기준 ≤{VP_UP30_MAX_PCT:g}%, 일봉 종가×거래량 근사)" if vp is not None else "거래량 없음"))

    tv = _trade_value(daily)
    if len(tv) >= TV_RECENT + TV_PRIOR:
        r20, p60 = float(tv.iloc[-TV_RECENT:].mean()), float(tv.iloc[-(TV_RECENT + TV_PRIOR):-TV_RECENT].mean())
        out.append(_item("tv_rising", lab["tv_rising"], r20 > p60 if p60 > 0 else None,
                         round(r20 / p60, 3) if p60 > 0 else None, f"20일 평균 / 직전 60일 평균 = {r20 / p60:.2f}배" if p60 > 0 else "직전 거래대금 0"))
    else:
        out.append(_item("tv_rising", lab["tv_rising"], None, None, f"일봉 {TV_RECENT + TV_PRIOR}개 필요"))
    return out


def short_metrics(daily: pd.DataFrame) -> dict:
    """단기 비교 지표(자동). 데이터 부족 칸은 None."""
    import scanner
    out = {"atr_pct": None, "vp_up5_pct": None, "tv5_chg_pct": None, "ret10_pct": None, "close": None}
    if daily is None or daily.empty:
        return out
    c = daily["Close"]
    cur = float(c.iloc[-1])
    out["close"] = cur
    if len(c) > ATR_DAYS and cur > 0:
        out["atr_pct"] = round(scanner.atr(daily["High"], daily["Low"], c, ATR_DAYS) / cur * 100, 2)
    out["vp_up5_pct"] = volume_share_above(daily, SHORT_VP_UP_PCT)
    tv = _trade_value(daily)
    if len(tv) >= TV_SHORT_DAYS * 2:
        a, b = float(tv.iloc[-TV_SHORT_DAYS:].mean()), float(tv.iloc[-2 * TV_SHORT_DAYS:-TV_SHORT_DAYS].mean())
        out["tv5_chg_pct"] = round((a / b - 1) * 100, 2) if b > 0 else None
    if len(c) > SHORT_RET_DAYS:
        base = float(c.iloc[-1 - SHORT_RET_DAYS])
        out["ret10_pct"] = round((cur / base - 1) * 100, 2) if base > 0 else None
    return out


def rank_by_ret10(rows: list) -> list:
    """후보 안 RS 순위 — 최근 10일 수익률 내림차순 1위부터. 값 없는 종목은 순위 None."""
    ranked = sorted([r for r in rows if r.get("ret10_pct") is not None], key=lambda r: -r["ret10_pct"])
    for i, r in enumerate(ranked, 1):
        r["rs_rank"] = i
    for r in rows:
        r.setdefault("rs_rank", None)
    return rows


# ── 데이터(평가하는 종목만) ────────────────────────────────────────────

def fetch_ohlcv(code: str, mkt: str) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """(일봉, 월봉) — 월봉은 진행 중인 달 포함(호출부가 completed_months로 뺀다)."""
    if mkt == "UPBIT":
        import upbit
        return upbit.fetch_candles(code, "days"), upbit.fetch_candles(code, "months")
    if mkt == "KR":
        import naver_kr
        d = naver_kr.fetch_history(code, days=lp.KR_DAYS["month"])
    else:
        import harness
        d = harness._fetch_us_batch([code], period=lp.US_PERIOD["month"]).get(code)
    if d is None or d.empty:
        return None, None
    d = d[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
    return d, monthly_ohlc(d)


def evaluate(code: str, mkt: str, now: datetime) -> dict:
    daily, monthly = fetch_ohlcv(code, mkt)
    if daily is None or daily.empty:
        return {"ok": False, "error": "일봉을 받지 못했어요", "items": []}
    months = completed_months(monthly, mkt, now)
    items = long_checks(daily, months)
    # O·X·미표시 집계는 화면(lpeTally)이 자동+수동을 합쳐 한 곳에서 센다(사본 금지)
    return {"ok": True, "items": items,
            "last_date": str(daily.index[-1].date()), "close": float(daily["Close"].iloc[-1])}


def short_table(hits: list) -> list:
    """이번 주 저점 히트(행: code·name·market) → 단기 비교 행. KR은 naver 일봉, US는 yfinance 한 번에
    (평가 시점에 그 종목들만 조회). 순위는 rank_by_ret10."""
    from concurrent.futures import ThreadPoolExecutor
    import naver_kr
    import harness
    kr = [h["code"] for h in hits if h.get("market") != "US"]
    us = [h["code"] for h in hits if h.get("market") == "US"]
    data = {}
    if us:
        data.update(harness._fetch_us_batch(us, period=lp.US_PERIOD["month"]))
    if kr:
        with ThreadPoolExecutor(max_workers=lp.FETCH_CONCURRENCY) as ex:
            for code, df in zip(kr, ex.map(lambda c: naver_kr.fetch_history(c, days=lp.KR_DAYS["month"]), kr)):
                if df is not None and not df.empty:
                    data[code] = df
    rows = []
    for h in hits:
        m = short_metrics(data.get(h["code"]))
        rows.append({"code": h["code"], "name": h.get("name") or h["code"],
                     "mkt": "US" if h.get("market") == "US" else "KR", **m})
    return rank_by_ret10(rows)
