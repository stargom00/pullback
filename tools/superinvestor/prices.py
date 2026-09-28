"""일봉(yfinance) — 분기 VWAP 근사·분기 저가/고가·현재가 맥락 + 분할 보정(함정 6).

[중요 — 무엇이 이미 보정돼 있는가] yfinance(`auto_adjust=False`)의 `Close`는
**이미 분할 보정된 값**이고 배당은 미보정이다(실측 확인: NVDA 10:1 분할 직전
2024-06-07 종가가 $120.89로 나온다 — 미보정이면 ~$1209). 그래서:
  · 가격: 그대로 쓴다(과거 VWAP이 오늘 가격과 같은 주당 기준).
  · **13F 주식 수: 보정해야 한다** — 분기말 이후 일어난 분할의 누적 배수를 곱해야
    분기 간 주식 수 비교(ADD/REDUCE 판정)와 "증가주식수 × 분기VWAP"이 맞는다.
`Stock Splits` 열(actions=True)이 그 배수의 출처다 → `split_factor_after()`.

함정 6(분기 간 주식 수 비교 시 분할 보정)은 이 파일의 `split_factor_after()` +
`screen.classify_history()`(보정된 주식 수로 재분류)가 같이 처리한다.

[VWAP 근사] Σ((H+L+C)/3 × Volume) / ΣVolume — 일봉으로 만드는 근사치라 컬럼명에
`_approx`를 붙인다(진짜 체결 VWAP이 아니다). 분기 중 실제 매수 시점은 알 수 없으므로
분기 저가~고가를 **같이** 내보내 "구간"으로 보게 한다(사용자 지시).
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(_HERE, "cache", "prices")

BATCH = 100                    # yfinance 배치 크기(앱 US_BATCH_SIZE와 같은 관례)
DEFAULT_PERIOD = "5y"          # 5개 분기 + 200일선/52주고점 계산에 넉넉하게


def yahoo_symbol(ticker: str) -> str:
    """OpenFIGI 티커 → 야후 심볼. 클래스 구분자가 다르다: OpenFIGI 'BRK/B',
    야후 'BRK-B'(2026-09-28 실측 — BRK/A·BRK/B·LEN/B·UHAL/B가 전부 404였다).
    CSV에는 원래 티커도 같이 남긴다(어느 쪽으로 검색해도 찾게)."""
    return (ticker or "").strip().upper().replace("/", "-")


def fetch_prices(tickers: list[str], period: str = DEFAULT_PERIOD,
                 use_cache: bool = True, verbose: bool = True) -> dict[str, pd.DataFrame]:
    """{ticker: DataFrame[Open High Low Close Volume, Stock Splits]}.
    캐시는 하루 단위(파일명에 오늘 날짜) — 같은 날 재실행은 네트워크 0회."""
    import yfinance as yf
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"daily_{date.today():%Y%m%d}_{period}.pkl")
    cached: dict[str, pd.DataFrame] = {}
    if use_cache and os.path.exists(cache_path):
        cached = pd.read_pickle(cache_path)
    todo = [t for t in dict.fromkeys(tickers) if t and t not in cached]
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        if verbose:
            print(f"[prices] {i + len(batch)}/{len(todo)} …", flush=True)
        raw = yf.download(batch, period=period, auto_adjust=False, actions=True,
                          group_by="ticker", progress=False, threads=True)
        for t in batch:
            try:
                df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
            except KeyError:
                continue
            df = df.dropna(subset=["Close"])
            if df.empty:
                continue
            cached[t] = df
        if use_cache:
            pd.to_pickle(cached, cache_path)
    return {t: cached[t] for t in tickers if t in cached}


# ── 함정 6 — 분할 보정 ───────────────────────────────────────────────

def split_factor_after(df: pd.DataFrame, after: date) -> float:
    """`after`(분기말) **이후**에 발생한 분할의 누적 배수.
    예: 분기말 이후 10:1 분할 1회 → 10.0. 분할 없으면 1.0.
    13F 주식 수에 곱하면 오늘(분할 보정된) 주당 기준으로 환산된다."""
    if df is None or "Stock Splits" not in df.columns or df.empty:
        return 1.0
    s = pd.to_numeric(df["Stock Splits"], errors="coerce").fillna(0.0)
    idx = pd.to_datetime(s.index).tz_localize(None) if getattr(s.index, "tz", None) else pd.to_datetime(s.index)
    mask = idx > pd.Timestamp(after)
    ratios = [r for r in s[mask].tolist() if r and r > 0]
    factor = 1.0
    for r in ratios:
        factor *= float(r)
    return factor


# ── 분기 통계 ────────────────────────────────────────────────────────

def quarter_bounds(period_end: date) -> tuple[date, date]:
    """분기말 날짜 → (분기 시작일, 분기말)."""
    q_start_month = ((period_end.month - 1) // 3) * 3 + 1
    return date(period_end.year, q_start_month, 1), period_end


def quarter_stats(df: pd.DataFrame, period_end: date) -> dict | None:
    """그 분기의 VWAP 근사·저가·고가·봉수. 데이터 없으면 None."""
    if df is None or df.empty:
        return None
    start, end = quarter_bounds(period_end)
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    d = df.copy()
    d.index = idx
    q = d[(d.index >= pd.Timestamp(start)) & (d.index <= pd.Timestamp(end) + pd.Timedelta(days=1))]
    q = q.dropna(subset=["Close"])
    if q.empty:
        return None
    typical = (q["High"] + q["Low"] + q["Close"]) / 3.0
    vol = pd.to_numeric(q["Volume"], errors="coerce").fillna(0.0)
    vwap = float((typical * vol).sum() / vol.sum()) if vol.sum() > 0 else float(typical.mean())
    return {"vwap_approx": round(vwap, 4), "low": round(float(q["Low"].min()), 4),
            "high": round(float(q["High"].max()), 4), "bars": int(len(q))}


def price_context(df: pd.DataFrame) -> dict | None:
    """현재가 맥락 — 종가·이평 위치·52주 고점 대비·200일선 기울기."""
    if df is None or df.empty:
        return None
    close = pd.to_numeric(df["Close"], errors="coerce").dropna()
    if close.empty:
        return None
    last = float(close.iloc[-1])
    out = {"last_close": round(last, 4),
           "last_bar_date": str(pd.to_datetime(close.index[-1]).date())}
    for w in (20, 60, 120, 200):
        ma = float(close.rolling(w).mean().iloc[-1]) if len(close) >= w else float("nan")
        out[f"ma{w}"] = round(ma, 4) if ma == ma else None
        out[f"above_ma{w}"] = (last > ma) if ma == ma else None
    if len(close) >= 221:
        ma200 = close.rolling(200).mean()
        prev = float(ma200.iloc[-21])
        cur = float(ma200.iloc[-1])
        out["ma200_slope_pct_20d"] = round((cur / prev - 1) * 100, 2) if prev > 0 else None
    else:
        out["ma200_slope_pct_20d"] = None
    w52 = close[close.index >= (pd.to_datetime(close.index[-1]) - pd.Timedelta(days=365))]
    hi = float(w52.max()) if len(w52) else float("nan")
    out["high_52w"] = round(hi, 4) if hi == hi else None
    out["off_52w_high_pct"] = round((last / hi - 1) * 100, 2) if hi == hi and hi > 0 else None
    return out


def splits_in_window(df: pd.DataFrame, start: date, end: date) -> list[tuple[str, float]]:
    """검증·로그용 — 구간 내 분할 [(날짜, 배수)]."""
    if df is None or "Stock Splits" not in df.columns:
        return []
    s = pd.to_numeric(df["Stock Splits"], errors="coerce").fillna(0.0)
    idx = pd.to_datetime(s.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    out = []
    for ts, r in zip(idx, s.tolist()):
        if r and r > 0 and pd.Timestamp(start) <= ts <= pd.Timestamp(end) + timedelta(days=1):
            out.append((str(ts.date()), float(r)))
    return out
