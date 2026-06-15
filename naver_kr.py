"""
네이버 금융 데이터 소스 — 한국 종목(.KS/.KQ) 전용.
- 과거 1년치 일봉: api.finance.naver.com/siseJson.naver (수정주가 기준)
- 장중 현재가: m.stock.naver.com 모바일 API (실시간 근접)

yfinance와 동일한 컬럼명(Open/High/Low/Close/Volume) + DatetimeIndex 의
pandas DataFrame을 반환하므로 scanner.py는 수정 불필요.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

import pandas as pd
import requests

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
}

_SISE_URL = "https://api.finance.naver.com/siseJson.naver"
_TIMEOUT = 8


def to_code(ticker: str) -> str:
    """'033640.KQ' -> '033640' (6자리 종목 코드)."""
    return ticker.split(".")[0].zfill(6)


def is_kr(ticker: str) -> bool:
    return ticker.upper().endswith((".KS", ".KQ"))


def _parse_sise(text: str) -> pd.DataFrame | None:
    """
    siseJson 응답 파싱.
    응답 예 (작은따옴표 변종, 첫 줄은 헤더):
    [['날짜', '시가', '고가', '저가', '종가', '거래량', '외국인소진율'],
     ["20250115", 70000, 71000, 69500, 70500, 12345678, 51.23],
     ...]
    """
    if not text:
        return None
    # 작은따옴표 → 큰따옴표, 개행/공백 정리 후 JSON 파싱
    cleaned = text.strip().replace("'", '"')
    # 후행 콤마 제거 (네이버가 가끔 붙임)
    cleaned = re.sub(r",\s*]", "]", cleaned)
    try:
        rows = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if not rows or len(rows) < 2:
        return None

    header, *data = rows
    if not data:
        return None

    recs = []
    for r in data:
        # r = [날짜, 시가, 고가, 저가, 종가, 거래량, 외국인소진율]
        if len(r) < 6:
            continue
        try:
            recs.append({
                "Date": pd.to_datetime(str(r[0]).strip(), format="%Y%m%d"),
                "Open": float(r[1]),
                "High": float(r[2]),
                "Low": float(r[3]),
                "Close": float(r[4]),
                "Volume": float(r[5]),
            })
        except (ValueError, TypeError):
            continue

    if not recs:
        return None

    df = pd.DataFrame(recs).set_index("Date").sort_index()
    # 0 종가(거래정지 등) 행 제거
    df = df[df["Close"] > 0]
    return df if not df.empty else None


def fetch_history(ticker: str, days: int = 400) -> pd.DataFrame | None:
    """한국 종목 과거 일봉 (수정주가). 기본 400일 → 1년치 확보."""
    code = to_code(ticker)
    end = datetime.now()
    start = end - timedelta(days=days)
    params = {
        "symbol": code,
        "requestType": 1,
        "startTime": start.strftime("%Y%m%d"),
        "endTime": end.strftime("%Y%m%d"),
        "timeframe": "day",
    }
    try:
        resp = requests.get(_SISE_URL, params=params, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        return _parse_sise(resp.text)
    except (requests.RequestException, ValueError):
        return None


def fetch_live_price(ticker: str) -> float | None:
    """
    장중 현재가 (실시간 근접). 네이버 모바일 통합 API.
    실패 시 None → 호출부에서 일봉 마지막 종가로 폴백.
    """
    code = to_code(ticker)
    url = f"https://m.stock.naver.com/api/stock/{code}/integration"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        # totalInfos 리스트에서 'closePrice' 또는 dealTrendInfos 등에 현재가 존재.
        # 구조 변동 대비: 여러 경로를 순서대로 시도.
        # 1) 최상위 'dealTrendInfos' / 'closePrice'
        price = None
        if isinstance(data, dict):
            # 가장 흔한 위치
            ct = data.get("closePrice") or data.get("nowVal")
            if ct:
                price = _to_num(ct)
            if price is None:
                # totalInfos: [{"code":"closePrice","value":"37,800"}, ...]
                for item in data.get("totalInfos", []) or []:
                    if item.get("code") in ("closePrice", "nowVal"):
                        price = _to_num(item.get("value"))
                        if price:
                            break
        return price
    except (requests.RequestException, ValueError, KeyError):
        return None


def _to_num(v) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def fetch(ticker: str) -> pd.DataFrame | None:
    """
    한국 종목 통합 fetch: 과거 일봉 + 현재가 보정.
    - 일봉을 받고, 장중 현재가가 있으면 '오늘' 봉의 Close를 덮어쓴다.
    - 오늘 봉이 아직 없으면 현재가로 새 행을 추가한다.
    """
    df = fetch_history(ticker)
    if df is None or df.empty:
        return None

    live = fetch_live_price(ticker)
    if live and live > 0:
        today = pd.Timestamp(datetime.now().date())
        last_day = df.index[-1].normalize()
        if last_day == today:
            # 오늘 봉 존재 → 종가/고저 보정
            df.loc[df.index[-1], "Close"] = live
            df.loc[df.index[-1], "High"] = max(df.iloc[-1]["High"], live)
            df.loc[df.index[-1], "Low"] = min(df.iloc[-1]["Low"], live)
        else:
            # 오늘 봉 없음 → 현재가로 임시 행 추가 (거래량은 직전값 근사)
            prev_close = float(df.iloc[-1]["Close"])
            df.loc[today] = {
                "Open": prev_close,
                "High": max(prev_close, live),
                "Low": min(prev_close, live),
                "Close": live,
                "Volume": float(df.iloc[-1]["Volume"]),
            }
    return df
